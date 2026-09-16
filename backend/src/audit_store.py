"""
Audit log (Phase 5).

Records WHO did WHAT — who viewed which camera, searched which plate, changed
the watchlist, exported a clip, logged in — separate from the detection event
history. This is the accountability trail the "auditability" requirement asks
for. Same history.db, its own table and connection.
"""

import os
import sqlite3
import threading
import time
from datetime import datetime

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
_DB_PATH = os.path.join(_BASE_DIR, "history.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_MAX_ROWS = 50000
_PRUNE_TO = 40000


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _conn = conn
    return _conn


def init_db():
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        conn = _get_conn()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_epoch REAL NOT NULL,
                    ts_iso TEXT NOT NULL,
                    username TEXT NOT NULL,
                    role TEXT,
                    action TEXT NOT NULL,
                    target TEXT,
                    detail TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts_epoch)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(username)")


def log(username, role, action, target=None, detail=None) -> None:
    """Append one audit entry. Never raises into the caller — auditing must
    not break the action being audited."""
    try:
        now = time.time()
        ts_iso = datetime.fromtimestamp(now).isoformat(timespec="seconds")
        with _lock:
            conn = _get_conn()
            with conn:
                conn.execute(
                    "INSERT INTO audit_log (ts_epoch, ts_iso, username, role, action, target, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (now, ts_iso, username, role, action, target, detail),
                )
                n = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
                if n > _MAX_ROWS:
                    conn.execute(
                        "DELETE FROM audit_log WHERE id IN "
                        "(SELECT id FROM audit_log ORDER BY ts_epoch ASC LIMIT ?)",
                        (n - _PRUNE_TO,),
                    )
    except Exception:
        pass


def query(limit=100, offset=0, username=None, action=None) -> tuple[list[dict], int]:
    where, params = [], []
    if username:
        where.append("username = ?")
        params.append(username)
    if action:
        where.append("action = ?")
        params.append(action)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with _lock:
        conn = _get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM audit_log {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, ts_iso, username, role, action, target, detail "
            f"FROM audit_log {clause} ORDER BY ts_epoch DESC, id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total
