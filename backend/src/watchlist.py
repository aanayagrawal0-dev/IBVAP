"""
Watchlist matching engine.

A small, operator-controlled table of plates of interest. Every plate the
ANPR engine reads off a live feed is cross-referenced against it, and a match
fires a distinct `watchlist_match` alert through the existing
queue -> WebSocket -> DB pipeline (see api_server.CameraWorker). This is the
"continuous cross-referencing of live feeds against watchlist records,
automated real-time alert on match" requirement — the transport is reused,
only the trigger is new.

Lives in the same history.db as events and cameras. Plates are stored
normalized (uppercase, alphanumeric only) so a watchlist entry typed as
"MH12 AB 1234" matches an OCR read of "MH12AB1234" — the exact same
normalization ANPR applies to what it reads (see anpr._PLATE_RE), so the two
sides can't disagree.

The table has a `kind` column so person/face identifiers can be added later
(Phase 6.4) without a migration; Phase 3 only populates plate entries.
"""

import os
import re
import sqlite3
import threading
import time

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
_DB_PATH = os.path.join(_BASE_DIR, "history.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_VALID_SEVERITIES = {"critical", "warning", "info"}


def normalize_plate(plate: str) -> str:
    """Uppercase + strip everything but A-Z0-9 — identical to the cleaning
    anpr._best_plate does to OCR output, so both sides normalize the same."""
    return _NON_ALNUM.sub("", (plate or "").upper())


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
                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL DEFAULT 'plate',
                    plate_normalized TEXT NOT NULL UNIQUE,
                    plate_display TEXT NOT NULL,
                    label TEXT,
                    severity TEXT NOT NULL DEFAULT 'critical',
                    active INTEGER NOT NULL DEFAULT 1,
                    added_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_watchlist_norm ON watchlist(plate_normalized)"
            )


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["active"] = bool(d["active"])
    return d


def add_plate(plate: str, label: str | None = None, severity: str = "critical") -> dict:
    """Add (or ValueError if the normalized plate already exists)."""
    norm = normalize_plate(plate)
    if len(norm) < 3:
        raise ValueError("Plate must have at least 3 alphanumeric characters.")
    if severity not in _VALID_SEVERITIES:
        severity = "critical"
    now = time.time()
    with _lock:
        conn = _get_conn()
        if conn.execute(
            "SELECT 1 FROM watchlist WHERE plate_normalized = ?", (norm,)
        ).fetchone():
            raise ValueError(f"Plate '{norm}' is already on the watchlist.")
        with conn:
            cur = conn.execute(
                """
                INSERT INTO watchlist (kind, plate_normalized, plate_display, label, severity, active, added_at)
                VALUES ('plate', ?, ?, ?, ?, 1, ?)
                """,
                (norm, plate.strip().upper(), label, severity, now),
            )
            new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM watchlist WHERE id = ?", (new_id,)).fetchone()
    return _row_to_dict(row)


def list_entries() -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()
        return [_row_to_dict(r) for r in rows]


def remove(entry_id: int) -> bool:
    with _lock:
        conn = _get_conn()
        with conn:
            cur = conn.execute("DELETE FROM watchlist WHERE id = ?", (entry_id,))
        return cur.rowcount > 0


def set_active(entry_id: int, active: bool) -> dict | None:
    with _lock:
        conn = _get_conn()
        if conn.execute("SELECT 1 FROM watchlist WHERE id = ?", (entry_id,)).fetchone() is None:
            return None
        with conn:
            conn.execute(
                "UPDATE watchlist SET active = ? WHERE id = ?", (1 if active else 0, entry_id)
            )
        row = conn.execute("SELECT * FROM watchlist WHERE id = ?", (entry_id,)).fetchone()
    return _row_to_dict(row)


def check_plate(plate: str) -> dict | None:
    """The hot path: is this (already-read) plate on the active watchlist?
    Returns the matching entry dict, or None. Normalizes both sides so
    spacing/formatting differences never cause a miss."""
    norm = normalize_plate(plate)
    if len(norm) < 3:
        return None
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM watchlist WHERE plate_normalized = ? AND active = 1", (norm,)
        ).fetchone()
        return _row_to_dict(row) if row else None
