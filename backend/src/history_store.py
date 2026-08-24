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
"""

import os
import sqlite3
import threading
import time
from datetime import datetime

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
_DB_PATH = os.path.join(_BASE_DIR, "history.db")
_THUMB_DIR = os.path.join(_BASE_DIR, "history_thumbnails")

_lock = threading.Lock()

# Soft cap so a multi-day demo/deployment can't quietly fill the disk with
# thumbnails. Checked (cheaply) every INSERT; only actually prunes once
# the table is comfortably over the limit.
_MAX_ROWS = 5000
_PRUNE_TO = 4000


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    os.makedirs(_THUMB_DIR, exist_ok=True)
    with _lock, _connect() as conn:
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
) -> int:
    """Persists one history row and (if given) its thumbnail JPEG. Returns
    the new row's id, which the caller uses both as the thumbnail's
    filename and as the id embedded in the live alert broadcast — so a
    WebSocket alert and its History row are always the same id."""
    now = time.time()
    ts_iso = datetime.now().isoformat(timespec="seconds")

    with _lock:
        with _connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO events (
                    ts_epoch, ts_iso, camera_id, event_type, zone_name,
                    tracker_id, class_name, severity, title, description,
                    has_thumbnail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    real pagination instead of a hardcoded count."""
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

    with _lock, _connect() as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(f"SELECT COUNT(*) FROM events {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT id, ts_epoch, ts_iso, camera_id, event_type, zone_name,
                   tracker_id, class_name, severity, title, description,
                   has_thumbnail
            FROM events {clause}
            ORDER BY ts_epoch DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total


def get_thumbnail_path(event_id: int) -> str | None:
    path = os.path.join(_THUMB_DIR, f"{event_id}.jpg")
    return path if os.path.exists(path) else None


def summary_stats(since_epoch: float | None = None) -> dict:
    """Aggregates for the analytics report: totals broken down by severity
    and by camera, plus the covered time range — all computed straight
    from the real event log, nothing pre-baked."""
    where = "WHERE ts_epoch >= ?" if since_epoch is not None else ""
    params = [since_epoch] if since_epoch is not None else []

    with _lock, _connect() as conn:
        conn.row_factory = sqlite3.Row
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
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if total <= _MAX_ROWS:
            return
        to_drop = total - _PRUNE_TO
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM events ORDER BY ts_epoch ASC LIMIT ?", (to_drop,)
            ).fetchall()
        ]
        conn.executemany("DELETE FROM events WHERE id = ?", [(i,) for i in ids])

    for i in ids:
        try:
            os.remove(os.path.join(_THUMB_DIR, f"{i}.jpg"))
        except OSError:
            pass
