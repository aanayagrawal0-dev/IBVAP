"""
Live CCTV gateway integration (Sentinel Gujarat sandbox grid).

The gateway publishes every camera as a live stream (RTSP for AI inference,
HLS for restricted networks, WebRTC for browsers). Its catalogue —
``cameras.json`` / ``/api/ingest`` — is the contract: camera ids, the camera
set and per-camera stream properties can change, so we never hard-code URLs.
This module fetches that catalogue and upserts it into the Camera Registry
(camera_store), after which the normal CameraWorker machinery streams it.

Operating rules honoured here (the stream-level ones live in
ingestion.VideoSource):
  * credentials are NEVER stored: registry rows hold credential-less RTSP
    URLs; ingestion injects IBVAP_GATEWAY_EMAIL/PASSWORD at open time
  * pace the load: only cameras listed in IBVAP_GATEWAY_ACTIVE (or the first
    IBVAP_GATEWAY_MAX_ACTIVE live ones) are enabled — each open capture is a
    separate stream copy from the gateway
  * consume only: this module issues GETs on the catalogue and nothing else;
    it never publishes streams or calls the gateway's control API

Configuration (backend/.env):
  IBVAP_GATEWAY_CATALOG        URL of cameras.json / /api/ingest, or a local
                               JSON file path (e.g. a copy saved from the
                               browser while logged in)
  IBVAP_GATEWAY_COOKIE         session cookie, if the catalogue URL needs one
  IBVAP_GATEWAY_EMAIL/PASSWORD RTSP credentials (injected at open time only)
  IBVAP_GATEWAY_TRANSPORT      rtsp (default) | hls
  IBVAP_GATEWAY_RTSP_BASE      fallback when an entry has no RTSP URL
  IBVAP_GATEWAY_HLS_BASE       fallback when an entry has no HLS URL
  IBVAP_GATEWAY_ACTIVE         comma-separated ids to process, or "all"
  IBVAP_GATEWAY_MAX_ACTIVE     default cap when ACTIVE is unset (4)
  IBVAP_GATEWAY_DEPARTMENT     registry department for gateway cameras
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, urlunparse

from src import camera_store

GATEWAY_OWNERSHIP = "Sentinel Gateway"

_lock = threading.Lock()
_last_catalog: dict = {"fetched_at": None, "source": None, "cameras": [], "error": None}


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def catalog_configured() -> bool:
    return bool(_env("IBVAP_GATEWAY_CATALOG"))


def _strip_userinfo(url):
    """Registry rows must never hold credentials."""
    if not isinstance(url, str) or not url:
        return url
    parsed = urlparse(url)
    if "@" not in parsed.netloc:
        return url
    return urlunparse(parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[1]))


# --- fetching ---------------------------------------------------------------

def fetch_catalog(source: str | None = None, timeout=10) -> object:
    """Load the raw catalogue JSON from a URL (GET only) or a local file."""
    source = source or _env("IBVAP_GATEWAY_CATALOG")
    if not source:
        raise ValueError("IBVAP_GATEWAY_CATALOG is not set.")
    if urlparse(source).scheme not in ("http", "https"):
        with open(source, encoding="utf-8") as fh:
            return json.load(fh)

    headers = {"Accept": "application/json", "User-Agent": "PRAHARI-IBVAP/1.0"}
    cookie = _env("IBVAP_GATEWAY_COOKIE")
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(source, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            body = resp.read(5_000_000)
    except urllib.error.HTTPError as exc:
        raise ConnectionError(f"catalogue request failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"catalogue unreachable: {exc.reason}") from exc
    if "/auth/login" in urlparse(final_url).path:
        raise PermissionError(
            "The catalogue redirected to the gateway login page. Set "
            "IBVAP_GATEWAY_COOKIE to a valid session cookie, or save the JSON "
            "from a logged-in browser and point IBVAP_GATEWAY_CATALOG at the file."
        )
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Catalogue response was not JSON.") from exc


# --- parsing ----------------------------------------------------------------

def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_live(raw: dict) -> bool:
    live = raw.get("live")
    if isinstance(live, bool):
        return live
    status = str(_first(raw, "status", "state") or "").lower()
    if status:
        return status in ("live", "online", "up", "ok", "running", "ready")
    return True  # listed without a status: assume available


def normalize_entry(raw: dict) -> dict | None:
    """Map one catalogue entry (field names vary between the cameras.json and
    /api/ingest flavours) onto a stable shape. Returns None if it has no id."""
    if not isinstance(raw, dict):
        return None
    cam_id = _first(raw, "id", "camera_id", "cameraId", "key", "slug")
    if cam_id is None:
        return None
    cam_id = str(cam_id).strip()
    urls = raw.get("urls") or raw.get("endpoints") or raw.get("streams") or {}
    if not isinstance(urls, dict):
        urls = {}
    props = raw.get("stream") or raw.get("properties") or raw.get("video") or {}
    if not isinstance(props, dict):
        props = {}

    width = _first(props, "width") or _first(raw, "width")
    height = _first(props, "height") or _first(raw, "height")
    resolution = _first(props, "resolution") or _first(raw, "resolution")
    if resolution and not (width and height) and "x" in str(resolution).lower():
        w, _, h = str(resolution).lower().partition("x")
        width, height = _to_float(w), _to_float(h)

    location = _first(raw, "location", "place", "site", "address")
    name = _first(raw, "name", "title", "label")
    return {
        "id": cam_id,
        "name": str(name or location or cam_id),
        "location": location if isinstance(location, str) else None,
        "live": _is_live(raw),
        "codec": _first(props, "codec", "video_codec") or _first(raw, "codec", "video_codec"),
        "width": int(width) if _to_float(width) else None,
        "height": int(height) if _to_float(height) else None,
        # Declared rate is informational only — timing always uses PTS.
        "declared_fps": _to_float(_first(props, "fps", "framerate", "frame_rate")
                                  or _first(raw, "fps", "framerate", "frame_rate")),
        "bitrate": _first(props, "bitrate", "bitrate_kbps") or _first(raw, "bitrate", "bitrate_kbps"),
        "lat": _to_float(_first(raw, "lat", "latitude")),
        "lon": _to_float(_first(raw, "lon", "lng", "longitude")),
        "rtsp_url": _strip_userinfo(_first(urls, "rtsp") or _first(raw, "rtsp", "rtsp_url")),
        "hls_url": _strip_userinfo(_first(urls, "hls", "m3u8") or _first(raw, "hls", "hls_url")),
        "webrtc_url": _strip_userinfo(_first(urls, "webrtc", "whep")
                                      or _first(raw, "webrtc", "whep", "webrtc_url")),
    }


def parse_catalog(data) -> list[dict]:
    """Accepts a list, {"cameras": [...]}, or a dict keyed by camera id."""
    if isinstance(data, dict):
        for key in ("cameras", "items", "data", "streams"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [{"id": k, **v} for k, v in data.items() if isinstance(v, dict)]
    if not isinstance(data, list):
        raise ValueError("Unrecognised catalogue shape.")
    out, seen = [], set()
    for raw in data:
        entry = normalize_entry(raw)
        if entry and entry["id"] not in seen:
            seen.add(entry["id"])
            out.append(entry)
    return out


def source_spec_for(entry: dict, transport: str | None = None) -> str | None:
    """The credential-less URL a worker should open for this camera."""
    transport = (transport or _env("IBVAP_GATEWAY_TRANSPORT", "rtsp")).lower()
    rtsp_base = _env("IBVAP_GATEWAY_RTSP_BASE", "rtsp://103.250.160.189:8554").rstrip("/")
    hls_base = _env("IBVAP_GATEWAY_HLS_BASE", "https://cctv.corp8.cloud").rstrip("/")
    rtsp = entry.get("rtsp_url") or f"{rtsp_base}/stream/{entry['id']}"
    hls = entry.get("hls_url") or f"{hls_base}/{entry['id']}/index.m3u8"
    return hls if transport == "hls" else rtsp


def _active_ids(entries: list[dict]) -> set[str]:
    raw = _env("IBVAP_GATEWAY_ACTIVE")
    if raw.lower() == "all":
        return {e["id"] for e in entries if e["live"]}
    if raw:
        return {x.strip() for x in raw.split(",") if x.strip()}
    cap = int(_env("IBVAP_GATEWAY_MAX_ACTIVE", "4") or 4)
    return {e["id"] for e in [x for x in entries if x["live"]][:cap]}


_LOCATIONS_PATH = os.path.join(os.path.dirname(__file__), "..", "camera_locations.json")


def _known_location(entry: dict) -> dict | None:
    """Map position for a camera the catalogue gives no coordinates for, from
    camera_locations.json (IBVAP_CAMERA_LOCATIONS). Only used when the
    table's 'match' text appears in the camera's name, so a renumbered
    catalogue can't put a camera in the wrong town."""
    path = _env("IBVAP_CAMERA_LOCATIONS") or _LOCATIONS_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            table = json.load(fh).get("cameras", {})
    except (OSError, ValueError):
        return None
    loc = table.get(entry["id"])
    name = f"{entry.get('name', '')} {entry.get('location') or ''}".lower()
    if not loc or loc.get("match", "").lower() not in name:
        return None
    return loc


def _storage_details(entry: dict, loc: dict | None = None) -> str:
    bits = ["Live gateway feed (no local copy)"]
    if entry.get("codec"):
        bits.append(str(entry["codec"]).upper())
    if entry.get("width") and entry.get("height"):
        bits.append(f"{entry['width']}x{entry['height']}")
    if entry.get("declared_fps"):
        bits.append(f"{entry['declared_fps']:g} fps declared")
    if loc:
        bits.append(f"Map position: {loc['place']} ({loc['precision']}-level, approximate)")
    return " · ".join(bits)


# --- registry sync ------------------------------------------------------------

def sync_registry(entries: list[dict], apply_active: bool = False) -> dict:
    """Upsert catalogue entries into the Camera Registry.

    New cameras are enabled only if selected for processing (pace the load);
    existing rows keep the operator's enabled flag unless ``apply_active``.
    Gateway cameras that vanished from the catalogue are disabled, not
    deleted, so their history stays joinable. Returns the ids touched."""
    department = _env("IBVAP_GATEWAY_DEPARTMENT", "Gujarat Police Sandbox")
    active = _active_ids(entries)
    added, updated, removed = [], [], []
    catalog_ids = set()

    for entry in entries:
        cam_id = entry["id"]
        catalog_ids.add(cam_id)
        existing = camera_store.get_camera(cam_id)
        # The catalogue has no coordinates: place known cameras on the map,
        # but never overwrite a position an operator has set in the Registry.
        loc = _known_location(entry) if entry.get("lat") is None else None
        moved = existing and existing.get("lat") is not None and loc \
            and (existing["lat"], existing["lon"]) != (loc["lat"], loc["lon"])
        if moved:
            loc = None  # an operator moved this camera: keep their position
        fields = {
            "name": (f"{entry['name']} — {entry['location']}"
                     if entry.get("location") and entry["location"] != entry["name"]
                     else entry["name"]),
            "department": department,
            "ownership": GATEWAY_OWNERSHIP,
            "source_spec": source_spec_for(entry),
            "status": "live" if entry["live"] else "down",
            "storage_details": _storage_details(entry, loc),
            "lat": entry.get("lat") if entry.get("lat") is not None else (loc or {}).get("lat"),
            "lon": entry.get("lon") if entry.get("lon") is not None else (loc or {}).get("lon"),
        }
        if existing is None:
            camera_store.add_camera(cam_id, {**fields, "enabled": cam_id in active and entry["live"]})
            added.append(cam_id)
        else:
            if apply_active:
                fields["enabled"] = cam_id in active and entry["live"]
            elif not entry["live"]:
                fields["enabled"] = False  # don't hammer a camera marked down
            before = {k: existing.get(k) for k in fields}
            after = {k: v for k, v in fields.items() if v is not None}
            if any(before.get(k) != v for k, v in after.items()):
                camera_store.update_camera(cam_id, after)
                updated.append(cam_id)

    for cam in camera_store.list_cameras():
        if cam.get("ownership") == GATEWAY_OWNERSHIP and cam["id"] not in catalog_ids:
            if cam.get("enabled") or cam.get("status") != "removed":
                camera_store.update_camera(cam["id"], {"enabled": False, "status": "removed"})
                removed.append(cam["id"])

    return {"added": added, "updated": updated, "removed": removed,
            "active": sorted(active & catalog_ids)}


def sync_from_gateway(source: str | None = None, apply_active: bool = False) -> dict:
    """Fetch + parse + upsert. Records the catalogue (or the error) for the
    /api/gateway/catalog status view."""
    try:
        entries = parse_catalog(fetch_catalog(source))
    except Exception as exc:
        with _lock:
            _last_catalog.update({"fetched_at": time.time(), "error": str(exc),
                                  "source": _redact_source(source)})
        raise
    result = sync_registry(entries, apply_active=apply_active)
    with _lock:
        _last_catalog.update({"fetched_at": time.time(), "error": None, "cameras": entries,
                              "source": _redact_source(source)})
    return {**result, "total": len(entries)}


def _redact_source(source):
    source = source or _env("IBVAP_GATEWAY_CATALOG")
    return _strip_userinfo(source) if source else None


def last_catalog() -> dict:
    with _lock:
        return {**_last_catalog, "cameras": list(_last_catalog["cameras"])}
