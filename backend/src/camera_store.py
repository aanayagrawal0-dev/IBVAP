"""
Camera Registry — the source of truth for every camera the system knows
about (Model 1's mandatory foundation).

Replaces the old static IBVAP_CAM0x_SOURCE env vars: cameras live in a real
table with GIS coordinates, department, type, ownership and connectivity
metadata, and can be added / edited / removed at RUNTIME (see api_server's
camera manager, which starts and stops a CameraWorker to match — no server
restart).

Deliberately in the SAME SQLite file as the event history (history.db) so
Phase 4 route reconstruction can JOIN history rows straight against each
camera's lat/long without cross-database ATTACH gymnastics. It keeps its
own connection + lock (registry writes are rare admin actions; event writes
are a constant stream) — WAL lets the two coexist, and busy_timeout absorbs
the rare collision.
"""

import os
import sqlite3
import threading
import time

from src.ingestion import classify_source

# Same database file as history_store (see module docstring).
_BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
_DB_PATH = os.path.join(_BASE_DIR, "history.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# Columns a caller may set; anything else in an incoming dict is ignored.
_WRITABLE = (
    "name", "department", "lat", "lon", "camera_type",
    "ownership", "source_spec", "status", "storage_details", "enabled",
)


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
    """Create the cameras table (idempotent) and (re)open the connection
    against the current _DB_PATH (tests monkeypatch it)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        conn = _get_conn()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cameras (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    department TEXT,
                    lat REAL,
                    lon REAL,
                    camera_type TEXT,
                    ownership TEXT,
                    source_spec TEXT,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    storage_details TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    # camera_type is always kept consistent with the actual source spec.
    d["camera_type"] = classify_source(d.get("source_spec"))
    return d


def list_cameras() -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM cameras ORDER BY id").fetchall()
        return [_row_to_dict(r) for r in rows]


def get_camera(camera_id: str) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        return _row_to_dict(row) if row else None


def add_camera(camera_id: str, fields: dict) -> dict:
    """Insert a new camera. Raises ValueError if the id already exists."""
    now = time.time()
    f = {k: fields.get(k) for k in _WRITABLE}
    f["camera_type"] = classify_source(f.get("source_spec"))
    if f.get("name") is None:
        f["name"] = camera_id
    if f.get("status") is None:
        f["status"] = "unknown"
    # Default to enabled when unset/None; only an explicit falsy value disables.
    # (f["enabled"] is None here when the caller omitted it, so a plain
    # .get("enabled", True) would wrongly return None — handle None explicitly.)
    enabled = f.get("enabled")
    f["enabled"] = 1 if (enabled is None or enabled) else 0

    with _lock:
        conn = _get_conn()
        exists = conn.execute("SELECT 1 FROM cameras WHERE id = ?", (camera_id,)).fetchone()
        if exists:
            raise ValueError(f"Camera '{camera_id}' already exists.")
        with conn:
            conn.execute(
                """
                INSERT INTO cameras (
                    id, name, department, lat, lon, camera_type, ownership,
                    source_spec, status, storage_details, enabled,
                    created_at, updated_at
                ) VALUES (
                    :id, :name, :department, :lat, :lon, :camera_type, :ownership,
                    :source_spec, :status, :storage_details, :enabled,
                    :created_at, :updated_at
                )
                """,
                {"id": camera_id, "created_at": now, "updated_at": now, **f},
            )
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    return _row_to_dict(row)


def update_camera(camera_id: str, fields: dict) -> dict | None:
    """Patch the given writable fields on an existing camera. Returns the
    updated row, or None if no such camera."""
    updates = {k: v for k, v in fields.items() if k in _WRITABLE and v is not None}
    if "enabled" in updates:
        updates["enabled"] = 1 if updates["enabled"] else 0
    if "source_spec" in updates:
        updates["camera_type"] = classify_source(updates["source_spec"])

    with _lock:
        conn = _get_conn()
        if conn.execute("SELECT 1 FROM cameras WHERE id = ?", (camera_id,)).fetchone() is None:
            return None
        if updates:
            updates["updated_at"] = time.time()
            set_clause = ", ".join(f"{k} = :{k}" for k in updates)
            with conn:
                conn.execute(
                    f"UPDATE cameras SET {set_clause} WHERE id = :id",
                    {"id": camera_id, **updates},
                )
        row = conn.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    return _row_to_dict(row)


def delete_camera(camera_id: str) -> bool:
    with _lock:
        conn = _get_conn()
        with conn:
            cur = conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
        return cur.rowcount > 0


def count() -> int:
    with _lock:
        conn = _get_conn()
        return conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]


def seed_defaults(defaults: list[dict]):
    """Populate the registry from a list of camera dicts, but ONLY if it's
    empty — so an operator's runtime edits are never clobbered on restart.
    Each dict needs at least an 'id'."""
    with _lock:
        conn = _get_conn()
        if conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0] > 0:
            return
    for cam in defaults:
        add_camera(cam["id"], cam)
