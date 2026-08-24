"""
Per-camera zone persistence.

Deliberately a flat JSON file, not a database — this is a hackathon-scale
config store (a handful of cameras, a handful of zones each), and a JSON
file is trivial to inspect/hand-edit/back up. Swap for a real DB if this
ever needs to survive concurrent multi-operator edits.

Polygons are stored as PERCENTAGE coordinates (0-100, matching the
frontend's resolution-independent drawing canvas), not pixels — so the
same saved zone still lines up correctly if the frame size ever changes.
"""

import json
import os
import threading

_STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "zone_config.json")
_lock = threading.Lock()


def _read_raw() -> dict:
    if not os.path.exists(_STORE_PATH):
        return {}
    try:
        with open(_STORE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # A corrupt/partial file shouldn't crash the whole server — treat it
        # as empty and let the next successful save overwrite it.
        return {}


def _write_raw(data: dict):
    tmp_path = _STORE_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, _STORE_PATH)  # atomic on POSIX and Windows alike


def load_all_zones() -> dict:
    """{camera_id: [{"name": str, "polygon": [[x, y], ...]}, ...]}"""
    with _lock:
        return _read_raw()


def load_zones_for_camera(camera_id: str) -> list:
    with _lock:
        return _read_raw().get(camera_id, [])


def save_zones_for_camera(camera_id: str, zones: list):
    with _lock:
        data = _read_raw()
        data[camera_id] = zones
        _write_raw(data)
