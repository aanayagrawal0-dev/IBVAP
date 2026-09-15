"""
Event history persistence — a real SQLite database backing the History
page, replacing what used to be a hardcoded array on the frontend.

Every zone-crossing event gets one row here (metadata) plus one JPEG file
on disk (the annotated frame at the moment the event fired, so an operator
reviewing history later can actually see what triggered the alert, not
just read a text description of it).

SQLite rather than the flat-JSON approach zone_store.py uses on purpose:
zone configs are a handful of small, frequently-*rewritten* documents,
but history is an *append-only*, potentially-large, *queried* log
(filter by camera/severity/time, paginate) — exactly what a real
database is for, and exactly what "not hardcoded" means here.

Timestamp & concurrency guarantees (relied on by route reconstruction and
the audit trail downstream):

  * One authoritative timestamp per event. The epoch time is read ONCE and
    the ISO string is derived from that same value, so a row's ts_epoch and
    ts_iso can never disagree, and the live WebSocket alert can be stamped
    from the same instant instead of a third, independent clock read.
  * Monotonic, orderable ordering. ts_epoch is wall-clock (real absolute
    time for display) but clamped non-decreasing so an NTP correction that
    steps the clock backward can't make the log sort out of order. The
    strictly-increasing AUTOINCREMENT id is the tiebreaker, so (ts_epoch,
    id) is a strict total order with no ambiguous ties — see query_events.
  * Safe concurrent multi-camera writes. One CV thread per camera plus
    FastAPI's threadpool all funnel through a single shared connection
    guarded by one lock, so concurrent inserts can't corrupt, drop, or
    interleave — and, unlike the previous open-per-call code, the
    connection is never leaked.
"""

import os
import sqlite3
import threading
import time
from datetime import datetime

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
_DB_PATH = os.path.join(_BASE_DIR, "history.db")
_THUMB_DIR = os.path.join(_BASE_DIR, "history_thumbnails")

# Guards EVERY use of the single shared connection below. SQLite connections
# are not safe to touch from multiple threads at once, and this process has
# many: one CV thread per camera calling insert_event(), plus FastAPI's
# threadpool running the read/export endpoints. Serializing all access
# through one lock is what keeps concurrent multi-camera writes from
# corrupting or interleaving.
_lock = threading.Lock()

# One long-lived connection for the whole process, opened once in init_db()
# (and lazily on first use). The previous code ran every statement inside
# `with sqlite3.connect(...) as conn:` — but sqlite3's connection context
# manager only COMMITS the transaction, it never CLOSES the connection, so
# every insert/query leaked a connection and a file handle. Over a
# multi-hour, multi-camera run that steadily exhausts handles. One reused
# connection removes the leak and the per-call open overhead entirely.
_conn: sqlite3.Connection | None = None

# The highest ts_epoch stored so far, used to clamp new events to be
# non-decreasing (see the module docstring / insert_event).
_last_ts_epoch = 0.0

# Soft cap so a multi-day demo/deployment can't quietly fill the disk with
# thumbnails. Checked (cheaply) every INSERT; only actually prunes once
# the table is comfortably over the limit.
_MAX_ROWS = 5000
_PRUNE_TO = 4000


def _get_conn() -> sqlite3.Connection:
    """Return the shared connection, opening it against the current _DB_PATH
    on first use. check_same_thread=False is safe here ONLY because every
    caller holds _lock while using the connection (see above)."""
    global _conn
    if _conn is None:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL + NORMAL: the standard "fast but still crash-safe" combo for a
        # write-heavy log. WAL lets a reader (e.g. a CSV/PDF export) proceed
        # without blocking on writers at the SQLite level; NORMAL avoids an
        # fsync on every commit (safe against app crashes under WAL). The
        # busy_timeout is belt-and-suspenders against any checkpoint stall.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _conn = conn
    return _conn


def init_db():
    """Create the schema (idempotent) and (re)open the shared connection
    against the current _DB_PATH. Tests monkeypatch _DB_PATH and then call
    this, so any previous connection is closed and reopened at the new path
    rather than left pointing at the old database."""
    global _conn
    os.makedirs(_THUMB_DIR, exist_ok=True)
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        conn = _get_conn()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_epoch REAL NOT NULL,
                    ts_iso TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    zone_name TEXT NOT NULL,
                    tracker_id INTEGER NOT NULL,
                    class_name TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    has_thumbnail INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_epoch)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_camera ON events(camera_id)")

            # Migration for DBs created before the Gemini explanation feature —
            # ALTER TABLE ADD COLUMN is safe/idempotent-checked here so an
            # existing history.db with real logged events doesn't need to be
            # wiped to pick this up.
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
            if "explanation" not in existing_cols:
                conn.execute("ALTER TABLE events ADD COLUMN explanation TEXT")
            if "license_plate" not in existing_cols:
                conn.execute("ALTER TABLE events ADD COLUMN license_plate TEXT")

        # Rebuild _last_ts_epoch from the DB so the monotonic guarantee holds
        # across restarts, not just within one process lifetime.
        global _last_ts_epoch
        row = conn.execute("SELECT MAX(ts_epoch) AS hi FROM events").fetchone()
        _last_ts_epoch = row["hi"] or 0.0


def insert_event(
    camera_id: str,
    event_type: str,
    zone_name: str,
    tracker_id: int,
    class_name: str,
    severity: str,
    title: str,
    description: str,
    thumbnail_jpeg: bytes | None = None,
    license_plate: str | None = None,
    event_ts: float | None = None,
) -> int:
    """Persists one history row and (if given) its thumbnail JPEG. Returns
    the new row's id, which the caller uses both as the thumbnail's
    filename and as the id embedded in the live alert broadcast — so a
    WebSocket alert and its History row are always the same id.

    event_ts is the authoritative epoch time the event fired, captured ONCE
    by the caller so the DB row, its ISO string, and the live alert all
    share a single timestamp instead of three independent clock reads. If
    omitted, the event is stamped here at write time."""
    global _last_ts_epoch
    raw_ts = time.time() if event_ts is None else event_ts

    with _lock:
        # Clamp non-decreasing: a backward wall-clock step (NTP correction)
        # must never make this event sort before one already logged. The
        # stored ts_iso is derived from the SAME value, so the two columns
        # can't drift apart.
        now = raw_ts if raw_ts >= _last_ts_epoch else _last_ts_epoch
        _last_ts_epoch = now
        ts_iso = datetime.fromtimestamp(now).isoformat(timespec="seconds")

        conn = _get_conn()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO events (
                    ts_epoch, ts_iso, camera_id, event_type, zone_name,
                    tracker_id, class_name, severity, title, description,
                    has_thumbnail, license_plate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    ts_iso,
                    camera_id,
                    event_type,
                    zone_name,
                    tracker_id,
                    class_name,
                    severity,
                    title,
                    description,
                    1 if thumbnail_jpeg else 0,
                    license_plate,
                ),
            )
            event_id = cur.lastrowid

        if thumbnail_jpeg:
            path = os.path.join(_THUMB_DIR, f"{event_id}.jpg")
            with open(path, "wb") as f:
                f.write(thumbnail_jpeg)

        if event_id % 200 == 0:
            _prune_locked()

    return event_id


def query_events(
    camera_id: str | None = None,
    severity: str | None = None,
    since_epoch: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Returns (rows, total_matching) — total_matching ignores limit/offset
    so the frontend can render an accurate "Showing X-Y of Z" footer and
    real pagination instead of a hardcoded count.

    Ordered by (ts_epoch DESC, id DESC): the id tiebreaker makes the order a
    strict total order even when two events share a ts_epoch, so pagination
    can't drop or double-show a row and route reconstruction gets a stable,
    unambiguous sequence."""
    where = []
    params: list = []
    if camera_id:
        where.append("camera_id = ?")
        params.append(camera_id)
    if severity:
        where.append("severity = ?")
        params.append(severity)
    if since_epoch is not None:
        where.append("ts_epoch >= ?")
        params.append(since_epoch)
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    with _lock:
        conn = _get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM events {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT id, ts_epoch, ts_iso, camera_id, event_type, zone_name,
                   tracker_id, class_name, severity, title, description,
                   has_thumbnail, license_plate
            FROM events {clause}
            ORDER BY ts_epoch DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total


def get_thumbnail_path(event_id: int) -> str | None:
    path = os.path.join(_THUMB_DIR, f"{event_id}.jpg")
    return path if os.path.exists(path) else None


def get_event(event_id: int) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None


def set_explanation(event_id: int, text: str):
    """Caches a generated Gemini explanation against its event, so re-expanding
    the same row later (or a second operator opening it) doesn't re-call the
    API — see api_server.py's /api/history/{id}/explain."""
    with _lock:
        conn = _get_conn()
        with conn:
            conn.execute("UPDATE events SET explanation = ? WHERE id = ?", (text, event_id))


def summary_stats(since_epoch: float | None = None) -> dict:
    """Aggregates for the analytics report: totals broken down by severity
    and by camera, plus the covered time range — all computed straight
    from the real event log, nothing pre-baked."""
    where = "WHERE ts_epoch >= ?" if since_epoch is not None else ""
    params = [since_epoch] if since_epoch is not None else []

    with _lock:
        conn = _get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM events {where}", params).fetchone()[0]
        by_severity = {
            r["severity"]: r["n"]
            for r in conn.execute(
                f"SELECT severity, COUNT(*) as n FROM events {where} GROUP BY severity", params
            ).fetchall()
        }
        by_camera = {
            r["camera_id"]: r["n"]
            for r in conn.execute(
                f"SELECT camera_id, COUNT(*) as n FROM events {where} GROUP BY camera_id", params
            ).fetchall()
        }
        span = conn.execute(
            f"SELECT MIN(ts_epoch) as lo, MAX(ts_epoch) as hi FROM events {where}", params
        ).fetchone()

    return {
        "total": total,
        "by_severity": by_severity,
        "by_camera": by_camera,
        "earliest_ts": span["lo"],
        "latest_ts": span["hi"],
    }


def _prune_locked():
    """Caller already holds _lock. Drops the oldest rows (and their
    thumbnail files) once the table grows past _MAX_ROWS, keeping the
    newest _PRUNE_TO. Cheap no-op on every call except the rare one that
    crosses the threshold."""
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    if total <= _MAX_ROWS:
        return
    to_drop = total - _PRUNE_TO
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM events ORDER BY ts_epoch ASC, id ASC LIMIT ?", (to_drop,)
        ).fetchall()
    ]
    with conn:
        conn.executemany("DELETE FROM events WHERE id = ?", [(i,) for i in ids])

    for i in ids:
        try:
            os.remove(os.path.join(_THUMB_DIR, f"{i}.jpg"))
        except OSError:
            pass
