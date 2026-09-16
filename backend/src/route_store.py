"""
Vehicle sightings store — the raw material for cross-camera route
reconstruction (Phase 4).

One row per vehicle-visit-per-camera: when a vehicle is tracked on a feed we
upsert a sighting keyed by (camera_id, tracker_id), carrying the best plate
read for that visit AND its appearance embedding (from reid.py). Route
reconstruction (route.py) then, given a plate, pulls the matching sightings,
fuses in plate-less sightings whose embedding matches (Re-ID), filters by
camera-topology transition plausibility, joins the camera registry for GIS
coordinates, and returns an ordered, timestamped path.

Same history.db as everything else so the join against the camera registry
is a plain in-database lookup. ts_epoch is the FIRST time the vehicle was
seen at that camera (the arrival time used for ordering); last_ts_epoch is
refreshed while it stays in view.
"""

import os
import sqlite3
import threading
from datetime import datetime

import numpy as np

from src.watchlist import normalize_plate

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
_DB_PATH = os.path.join(_BASE_DIR, "history.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# Retention: sightings are a rolling window; keep the table bounded.
_MAX_ROWS = 20000
_PRUNE_TO = 16000


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
                CREATE TABLE IF NOT EXISTS vehicle_sightings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_epoch REAL NOT NULL,
                    ts_iso TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    tracker_id INTEGER NOT NULL,
                    class_name TEXT NOT NULL,
                    plate_normalized TEXT,
                    plate_display TEXT,
                    embedding BLOB,
                    last_ts_epoch REAL NOT NULL,
                    UNIQUE(camera_id, tracker_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sightings_plate ON vehicle_sightings(plate_normalized)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sightings_ts ON vehicle_sightings(ts_epoch)"
            )


def _embedding_to_blob(embedding) -> bytes | None:
    if embedding is None:
        return None
    arr = np.asarray(embedding, dtype=np.float32).ravel()
    return arr.tobytes() if arr.size else None


def embedding_from_blob(blob) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def record_sighting(camera_id, tracker_id, class_name, ts_epoch,
                    plate=None, embedding=None) -> None:
    """Upsert a sighting for (camera_id, tracker_id). First call sets the
    arrival ts_epoch; later calls refresh last_ts_epoch, backfill the plate
    if one wasn't known yet, and refresh the embedding. Never overwrites a
    known plate with null."""
    norm = normalize_plate(plate) if plate else None
    display = plate.strip().upper() if plate else None
    emb_blob = _embedding_to_blob(embedding)
    ts_iso = datetime.fromtimestamp(ts_epoch).isoformat(timespec="seconds")

    with _lock:
        conn = _get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO vehicle_sightings (
                    ts_epoch, ts_iso, camera_id, tracker_id, class_name,
                    plate_normalized, plate_display, embedding, last_ts_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_id, tracker_id) DO UPDATE SET
                    last_ts_epoch = excluded.last_ts_epoch,
                    plate_normalized = COALESCE(vehicle_sightings.plate_normalized, excluded.plate_normalized),
                    plate_display    = COALESCE(vehicle_sightings.plate_display, excluded.plate_display),
                    embedding        = COALESCE(excluded.embedding, vehicle_sightings.embedding)
                """,
                (ts_epoch, ts_iso, camera_id, int(tracker_id), class_name,
                 norm, display, emb_blob, ts_epoch),
            )
            n = conn.execute("SELECT COUNT(*) FROM vehicle_sightings").fetchone()[0]
            if n > _MAX_ROWS:
                conn.execute(
                    "DELETE FROM vehicle_sightings WHERE id IN "
                    "(SELECT id FROM vehicle_sightings ORDER BY ts_epoch ASC LIMIT ?)",
                    (n - _PRUNE_TO,),
                )


def all_sightings(include_embeddings=True) -> list[dict]:
    cols = ("id, ts_epoch, ts_iso, camera_id, tracker_id, class_name, "
            "plate_normalized, plate_display, last_ts_epoch")
    if include_embeddings:
        cols += ", embedding"
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            f"SELECT {cols} FROM vehicle_sightings ORDER BY ts_epoch ASC, id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def sightings_for_plate(plate_normalized: str) -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM vehicle_sightings WHERE plate_normalized = ? ORDER BY ts_epoch ASC, id ASC",
            (plate_normalized,),
        ).fetchall()
        return [dict(r) for r in rows]


def distinct_plates() -> list[dict]:
    """Plates that have at least one sighting, with sighting + camera counts —
    powers a plate picker in the UI."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT plate_normalized AS plate,
                   MAX(plate_display) AS plate_display,
                   COUNT(*) AS sightings,
                   COUNT(DISTINCT camera_id) AS cameras,
                   MIN(ts_epoch) AS first_ts,
                   MAX(last_ts_epoch) AS last_ts
            FROM vehicle_sightings
            WHERE plate_normalized IS NOT NULL
            GROUP BY plate_normalized
            ORDER BY last_ts DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
