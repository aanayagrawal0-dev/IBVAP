"""
Road-following geometry for route hops (map display only).

Route reconstruction decides WHICH cameras a vehicle passed and in what
order; drawing each hop as a straight line cuts across buildings. This asks
an OSRM routing service (OpenStreetMap's public demo server by default) for
the road path between two camera positions and caches it on disk, so each
pair is fetched once and later views are instant and work offline. Only the
two cameras' coordinates are sent. If routing is unavailable the caller
simply falls back to a straight line.

    IBVAP_ROAD_ROUTING=0        turn it off (straight lines)
    IBVAP_OSRM_URL=...          another OSRM server (default: router.project-osrm.org)
"""

import json
import os
import threading

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "road_paths_cache.json")
_MAX_POINTS = 400  # plenty for a smooth line; keeps responses small

_lock = threading.Lock()
_cache: dict | None = None


def _enabled() -> bool:
    return os.environ.get("IBVAP_ROAD_ROUTING", "1").lower() not in ("0", "false", "no", "off")


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_CACHE_PATH, encoding="utf-8") as fh:
                _cache = json.load(fh)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save():
    tmp = _CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_cache, fh)
    os.replace(tmp, _CACHE_PATH)


def _thin(points):
    if len(points) <= _MAX_POINTS:
        return points
    step = (len(points) - 1) / (_MAX_POINTS - 1)
    return [points[round(i * step)] for i in range(_MAX_POINTS)]


def road_path(a_lat, a_lon, b_lat, b_lon) -> dict | None:
    """{'points': [[lat, lon], ...], 'road_km': float} along roads, or None."""
    key = f"{a_lat:.5f},{a_lon:.5f};{b_lat:.5f},{b_lon:.5f}"
    with _lock:
        cached = _load().get(key)
    if cached is not None or not _enabled():
        return cached
    import requests

    base = os.environ.get("IBVAP_OSRM_URL", "https://router.project-osrm.org").rstrip("/")
    try:
        resp = requests.get(
            f"{base}/route/v1/driving/{a_lon},{a_lat};{b_lon},{b_lat}",
            params={"overview": "full", "geometries": "geojson"},
            headers={"User-Agent": "PRAHARI-IBVAP/1.0 (route map)"}, timeout=8,
        )
        route = resp.json()["routes"][0]
        result = {"points": _thin([[lat, lon] for lon, lat in route["geometry"]["coordinates"]]),
                  "road_km": round(route["distance"] / 1000.0, 2)}
    except Exception:
        return None  # offline / rate-limited: caller draws a straight line
    with _lock:
        _load()[key] = result
        _save()
    return result


def add_road_paths(result: dict) -> dict:
    """Attach 'road_path' (the road from the previous stop) to each route stop."""
    stops = result.get("stops") or []
    for prev, cur in zip(stops, stops[1:]):
        if None in (prev.get("lat"), prev.get("lon"), cur.get("lat"), cur.get("lon")):
            continue
        if (prev["lat"], prev["lon"]) == (cur["lat"], cur["lon"]):
            continue
        cur["road_path"] = road_path(prev["lat"], prev["lon"], cur["lat"], cur["lon"])
    return result
