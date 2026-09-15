"""
FastAPI bridge between the CV pipeline(s) and the SENTINEL-X frontend.

Each configured camera gets its OWN background thread running its own
Pipeline.stream() loop against its own video source (OpenCV/ultralytics are
blocking, synchronous libraries — they do not belong on the asyncio event
loop). Cameras are fully independent: separate video capture, separate
detector/tracker state, separate zone list, separate thermal toggle. A
crash or stall on one camera's thread doesn't affect any other.

Each camera's thread hands frames and events off through thread-safe
primitives that the async HTTP/WebSocket handlers read from:

  - latest_jpeg (bytes) behind a per-camera threading.Lock, polled by
    GET /api/stream/{camera_id} for that camera's MJPEG feed.
  - a single shared queue.Queue of formatted alert dicts (each tagged with
    its camera id), drained by WS /ws/alerts and broadcast to every
    connected browser tab.

Run with:  uvicorn src.api_server:app --reload --port 8000
(from the ibvap/ project root, with the venv active)
"""

import asyncio
import csv
import io
import json
import os
import queue
import threading
import time
from datetime import datetime

import cv2
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

# Loads backend/.env (if present) into os.environ before anything below
# reads a config var — GEMINI_API_KEY, GEMINI_MODEL, the IBVAP_CAM*_SOURCE
# vars, all of it. A shell-exported value always wins over .env (dotenv's
# default: it never overrides a variable that's already set), so this is
# purely a convenience — nothing breaks for anyone who prefers `export`.
load_dotenv()

from src.pipeline import Pipeline
from src.zones import Zone, ZoneManager, ZoneEventType
from src import history_store, zone_store, camera_store, watchlist


# --- Config -----------------------------------------------------------
WEIGHTS_PATH = "models/yolo11n-pose.pt"
STREAM_FPS = 15
# No fixed frame resolution here on purpose — zones are percentage-based
# and converted against each frame's actual dimensions in pipeline.py, so
# different cameras can run at whatever resolution their source provides.

# The Camera Registry (camera_store) is now the source of truth for which
# cameras exist and how to reach them — see the /api/cameras endpoints
# below. These entries only SEED an empty registry on first boot; after
# that an operator's runtime edits win and are never overwritten.
#
# A camera's source_spec is whatever ingestion.open_source() understands:
#   "0"                          laptop/USB webcam (device index)
#   "rtsp://192.168.1.50/..."    a real IP camera
#   "onvif://user:pass@host"     an ONVIF camera (handshake -> RTSP)
#   "http://host/mjpeg"          an HTTP MJPEG endpoint
#   "sample_data/x.mp4"          a recorded clip
#   ""                           no live source yet (not started)
#
# GIS coordinates are seeded around Gujarat so the /registry map has real
# points to plot out of the box. Each source is still overridable on first
# boot via its IBVAP_CAM0x_SOURCE env var, preserving existing .env setups.
def _seed_source(cam_id: str, default: str) -> str:
    return os.environ.get(f"IBVAP_{cam_id.replace('-', '')}_SOURCE", default)


_DEFAULT_CAMERAS = [
    {
        "id": "CAM-01", "name": "Webcam — Ashram Road", "department": "Traffic Police",
        "lat": 23.0300, "lon": 72.5714, "ownership": "GSP", "storage_details": "Edge NVR, 15-day retention",
        "source_spec": _seed_source("CAM-01", "0"),
    },
    {
        "id": "CAM-02", "name": "Gate Camera — Gandhinagar Secretariat", "department": "Home Guard",
        "lat": 23.2156, "lon": 72.6369, "ownership": "GSP", "storage_details": "Central VMS, 30-day retention",
        "source_spec": _seed_source("CAM-02", "sample_data/synthetic_test_clip.mp4"),
    },
    {
        "id": "CAM-03", "name": "Junction Camera — Surat Ring Road", "department": "Traffic Police",
        "lat": 21.1702, "lon": 72.8311, "ownership": "Municipal Corp", "storage_details": "Central VMS, 30-day retention",
        "source_spec": _seed_source("CAM-03", "sample_data/synthetic_test_clip.mp4"),
    },
    {
        "id": "CAM-04", "name": "Perimeter Camera — Vadodara Depot", "department": "Reserve Police",
        "lat": 22.3072, "lon": 73.1812, "ownership": "GSP", "storage_details": "Not yet provisioned",
        "source_spec": _seed_source("CAM-04", ""),
    },
]

# Used the first time a camera has no saved zone config yet (see
# zone_store.py / the /api/zones endpoints below). Stored/edited from the
# frontend as percentage coordinates, same as everything else here.
DEFAULT_ZONE_PCT = {
    "name": "restricted-zone",
    "polygon": [[60, 0], [100, 0], [100, 100], [60, 100]],
}


def _zone_from_pct(zone_pct: dict) -> Zone:
    """Build a Zone from the percentage-coordinate polygon saved/edited by
    the frontend. Zone.polygon IS percentages (0-100) — no pixel conversion
    here, since that has to happen per-frame against each frame's actual
    dimensions (see pipeline.py) rather than any one assumed resolution.
    This is what makes the same saved zone line up correctly whether the
    camera is a 960x540 recorded clip or a webcam at some other resolution."""
    polygon = [(pt[0], pt[1]) for pt in zone_pct["polygon"]]
    return Zone(name=zone_pct["name"], polygon=polygon)


def _load_zone_manager_for(camera_id: str) -> ZoneManager:
    saved = zone_store.load_zones_for_camera(camera_id)
    if not saved:
        # First boot, nothing saved yet for this camera: fall back to the
        # built-in default AND persist it immediately, so GET
        # /api/zones/{camera_id} (and the zone-config UI) reflects what's
        # actually being enforced instead of showing an empty list while a
        # zone is silently live underneath it.
        saved = [DEFAULT_ZONE_PCT]
        zone_store.save_zones_for_camera(camera_id, saved)
    return ZoneManager(zones=[_zone_from_pct(z) for z in saved])


def _apply_thermal_colormap(bgr_frame):
    """False-color 'thermal' look: grayscale intensity remapped through a
    heat palette (dark purple -> orange -> yellow, like a FLIR display).
    SIMULATED — there is no IR/thermal sensor involved."""
    gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    return cv2.applyColorMap(gray, cv2.COLORMAP_INFERNO)


# --- One alert stream shared by every camera, each alert tagged with which
# camera raised it ------------------------------------------------------
_alert_queue: "queue.Queue[dict]" = queue.Queue(maxsize=200)

# Thumbnails are stored at reduced size — a live 1280x720 frame doesn't
# need to be saved full-res just to show "what triggered this" in History.
THUMBNAIL_MAX_WIDTH = 320


def _emit_watchlist_alert(camera_id, plate, entry, tracker_id, class_name, thumbnail_jpeg=None):
    """Persist a watchlist_match event and broadcast its alert — the single
    shared path used by both the live pipeline (CameraWorker._on_watchlist_match)
    and the /api/watchlist/simulate demo hook. Reuses the same DB +
    WebSocket transport as zone alerts; only the trigger/type differs."""
    event_ts = time.time()
    severity = entry.get("severity") or "critical"
    label = entry.get("label") or "watchlisted vehicle"
    title = f"WATCHLIST MATCH — {plate}"
    description = (
        f"Plate {plate} ({label}) detected on {camera_id} "
        f"— tracked object #{tracker_id} ({class_name})."
    )
    event_id = history_store.insert_event(
        camera_id=camera_id,
        event_type="watchlist_match",
        zone_name=label,  # reuse the column to carry the watchlist reason
        tracker_id=tracker_id,
        class_name=class_name,
        severity=severity,
        title=title,
        description=description,
        thumbnail_jpeg=thumbnail_jpeg,
        license_plate=plate,
        event_ts=event_ts,
    )
    alert = {
        "id": f"evt-{event_id}",
        "severity": severity,
        "title": title,
        "description": description,
        "camera": camera_id,
        "timestamp": datetime.fromtimestamp(event_ts).strftime("%H:%M:%S"),
        "license_plate": plate,
        "type": "watchlist_match",
    }
    try:
        _alert_queue.put_nowait(alert)
    except queue.Full:
        pass
    return event_id


class CameraWorker:
    """Owns one camera's entire live pipeline: its own video capture,
    detector, tracker, zone manager, thermal toggle, and background thread.
    Completely independent of every other camera — nothing here is shared
    except the module-level alert queue above."""

    def __init__(self, camera_id: str, source):
        self.camera_id = camera_id
        self.source = source
        self.zone_manager = _load_zone_manager_for(camera_id)
        self.thermal_enabled = False
        self.frame_lock = threading.Lock()
        self.latest_jpeg: bytes | None = None
        self.stop_requested = False
        self.thread: threading.Thread | None = None
        self.started_at = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name=f"cam-{self.camera_id}", daemon=True)
        self.thread.start()
        self.started_at = time.time()

    def stop(self):
        self.stop_requested = True
        if self.thread:
            self.thread.join(timeout=5)

    def is_alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def _run(self):
        try:
            pipeline = Pipeline(
                source_uri=self.source,
                zone_manager=self.zone_manager,
                weights=WEIGHTS_PATH,
                conf_threshold=0.30,
                source_name=self.camera_id,
            )
        except Exception as exc:
            # A bad/missing source (e.g. no webcam attached to this
            # machine) shouldn't take the whole server down — just log and
            # leave this camera's stream 404-ing forever.
            print(f"[{self.camera_id}] failed to open source {self.source!r}: {exc}")
            return

        pipeline.stream(
            on_frame=self._on_frame,
            on_event=self._on_event,
            on_watchlist=self._on_watchlist_match,
            loop=True,
            target_fps=STREAM_FPS,
            stop_flag=lambda: self.stop_requested,
            night_vision_flag=lambda: self.thermal_enabled,
        )

    def _on_frame(self, annotated_bgr):
        ok, buf = cv2.imencode(".jpg", annotated_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self.frame_lock:
                self.latest_jpeg = buf.tobytes()

    def _on_event(self, evt, class_name, annotated_bgr):
        # One authoritative timestamp for this event, read ONCE and shared by
        # the DB row and the live alert below, so the audit record and the
        # broadcast can't disagree about when the event happened.
        event_ts = time.time()
        if evt.event_type == ZoneEventType.CLIMBING:
            severity = "critical"
            title = f"{class_name.upper()} CLIMBING DETECTED"
            description = (
                f"Tracked object #{evt.tracker_id} ({class_name}) "
                f"detected climbing at '{evt.zone_name}'."
            )
        elif evt.event_type == ZoneEventType.CRAWLING:
            severity = "critical"
            title = f"{class_name.upper()} CRAWLING DETECTED"
            description = (
                f"Tracked object #{evt.tracker_id} ({class_name}) "
                f"detected crawling/crouching at '{evt.zone_name}'."
            )
        else:
            entered = evt.event_type == ZoneEventType.ENTERED
            severity = "critical" if entered else "warning"
            title = f"{class_name.upper()} {'ENTERED' if entered else 'EXITED'} ZONE"
            description = (
                f"Tracked object #{evt.tracker_id} ({class_name}) "
                f"{'entered' if entered else 'exited'} '{evt.zone_name}'."
            )

        thumbnail_jpeg = self._make_thumbnail(annotated_bgr)

        # Extract license plate if the pipeline attached one to the event
        license_plate = getattr(evt, "license_plate", None)

        # The DB assigns the id (autoincrement) — reused as the live alert's
        # id too, so a WebSocket alert and its History row always match.
        event_id = history_store.insert_event(
            camera_id=self.camera_id,
            event_type=evt.event_type.value,
            zone_name=evt.zone_name,
            tracker_id=evt.tracker_id,
            class_name=class_name,
            severity=severity,
            title=title,
            description=description,
            thumbnail_jpeg=thumbnail_jpeg,
            license_plate=license_plate,
            event_ts=event_ts,
        )

        alert = {
            "id": f"evt-{event_id}",
            "severity": severity,
            "title": title,
            "description": description,
            "camera": self.camera_id,
            "timestamp": datetime.fromtimestamp(event_ts).strftime("%H:%M:%S"),
            "license_plate": license_plate,
        }
        try:
            _alert_queue.put_nowait(alert)
        except queue.Full:
            pass  # drop rather than block the CV thread if nobody's draining it

    def _on_watchlist_match(self, match, annotated_bgr):
        """Fires when a plate read off this camera's feed matches an active
        watchlist entry. Delegates to the shared emitter so the live-pipeline
        path and the /api/watchlist/simulate demo hook stay identical."""
        return _emit_watchlist_alert(
            camera_id=self.camera_id,
            plate=match["plate"],
            entry=match["entry"],
            tracker_id=match["tracker_id"],
            class_name=match["class_name"],
            thumbnail_jpeg=self._make_thumbnail(annotated_bgr),
        )

    @staticmethod
    def _make_thumbnail(annotated_bgr) -> bytes | None:
        h, w = annotated_bgr.shape[:2]
        if w > THUMBNAIL_MAX_WIDTH:
            scale = THUMBNAIL_MAX_WIDTH / w
            annotated_bgr = cv2.resize(
                annotated_bgr, (THUMBNAIL_MAX_WIDTH, max(1, int(h * scale)))
            )
        ok, buf = cv2.imencode(".jpg", annotated_bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes() if ok else None


# The live CameraWorker for each camera that's currently streaming, keyed by
# camera id. Populated from the registry on startup and mutated at RUNTIME by
# the /api/cameras endpoints (add / edit / remove) with no server restart.
# _cameras_lock guards structural mutations; reads use dict.get (GIL-atomic).
_cameras: dict[str, CameraWorker] = {}
_cameras_lock = threading.Lock()


def _runnable_source(cam: dict):
    """The source spec to hand a worker, or None if this camera shouldn't run
    (disabled, or no source configured yet)."""
    if not cam.get("enabled", True):
        return None
    spec = cam.get("source_spec")
    if spec is None or (isinstance(spec, str) and spec.strip() == ""):
        return None
    return spec.strip() if isinstance(spec, str) else spec


def _start_worker(cam: dict) -> bool:
    """Start (or restart) the CameraWorker for a registry row. Returns True if
    a worker is now running for it. Caller must hold _cameras_lock."""
    camera_id = cam["id"]
    existing = _cameras.pop(camera_id, None)
    if existing is not None:
        existing.stop()

    source = _runnable_source(cam)
    if source is None:
        return False

    worker = CameraWorker(camera_id, source)
    worker.start()
    _cameras[camera_id] = worker
    return True


def _stop_worker(camera_id: str):
    """Stop and forget a camera's worker if it has one. Caller holds _cameras_lock."""
    worker = _cameras.pop(camera_id, None)
    if worker is not None:
        worker.stop()


# --- FastAPI app --------------------------------------------------------
app = FastAPI(title="IBVAP Bridge API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    history_store.init_db()
    camera_store.init_db()
    watchlist.init_db()
    camera_store.seed_defaults(_DEFAULT_CAMERAS)
    with _cameras_lock:
        for cam in camera_store.list_cameras():
            try:
                _start_worker(cam)
            except Exception as exc:
                print(f"[{cam['id']}] failed to start on boot: {exc}")


@app.on_event("shutdown")
def _shutdown():
    with _cameras_lock:
        for camera in list(_cameras.values()):
            camera.stop()
        _cameras.clear()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "cameras": {cam_id: cam.is_alive() for cam_id, cam in _cameras.items()},
    }


# --- Camera Registry (Model 1: source of truth for onboarded cameras) -------
# CRUD over camera_store plus CSV bulk import/export. Adding/editing/removing a
# camera here also starts/stops its live CameraWorker so a change takes effect
# WITHOUT a server restart — that's the Phase 2 acceptance requirement.


class CameraIn(BaseModel):
    id: str | None = None
    name: str | None = None
    department: str | None = None
    lat: float | None = None
    lon: float | None = None
    ownership: str | None = None
    source_spec: str | None = None
    status: str | None = None
    storage_details: str | None = None
    enabled: bool | None = None


class CsvImport(BaseModel):
    csv: str


def _next_camera_id() -> str:
    existing = {c["id"] for c in camera_store.list_cameras()}
    n = 1
    while f"CAM-{n:02d}" in existing:
        n += 1
    return f"CAM-{n:02d}"


def _serialize_camera(cam: dict) -> dict:
    """Registry row + live runtime status, merged for the frontend."""
    worker = _cameras.get(cam["id"])
    streaming = bool(worker and worker.is_alive())
    spec = cam.get("source_spec")
    has_source = bool(spec.strip()) if isinstance(spec, str) else bool(spec)
    if not cam.get("enabled", True):
        connectivity = "disabled"
    elif not has_source:
        connectivity = "no-source"
    elif streaming:
        connectivity = "online"
    else:
        connectivity = "offline"
    return {**cam, "streaming": streaming, "connectivity": connectivity}


def _to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_bool(v, default=True):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


@app.get("/api/cameras")
def list_cameras():
    return {"cameras": [_serialize_camera(c) for c in camera_store.list_cameras()]}


@app.get("/api/cameras/export.csv")
def export_cameras_csv():
    # Declared BEFORE the /api/cameras/{camera_id} route so "export.csv" is
    # matched as this static path, not captured as a camera id.
    cols = ["id", "name", "department", "lat", "lon", "camera_type",
            "ownership", "source_spec", "status", "storage_details", "enabled"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for cam in camera_store.list_cameras():
        writer.writerow(cam)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ibvap_cameras_{stamp}.csv"'},
    )


@app.get("/api/cameras/{camera_id}")
def get_camera(camera_id: str):
    cam = camera_store.get_camera(camera_id)
    if cam is None:
        return _error(f"No camera '{camera_id}'.", status_code=404)
    return _serialize_camera(cam)


@app.post("/api/cameras")
def create_camera(body: CameraIn):
    camera_id = (body.id or "").strip() or _next_camera_id()
    fields = body.model_dump(exclude_none=True)
    fields.pop("id", None)
    try:
        cam = camera_store.add_camera(camera_id, fields)
    except ValueError as exc:
        return _error(str(exc), status_code=409)  # id already exists
    with _cameras_lock:
        _start_worker(cam)
    return JSONResponse(status_code=201, content=_serialize_camera(cam))


@app.put("/api/cameras/{camera_id}")
def update_camera(camera_id: str, body: CameraIn):
    before = camera_store.get_camera(camera_id)
    if before is None:
        return _error(f"No camera '{camera_id}'.", status_code=404)

    fields = body.model_dump(exclude_none=True)
    fields.pop("id", None)
    cam = camera_store.update_camera(camera_id, fields)

    # Restart the worker only when something that affects streaming changed.
    source_changed = "source_spec" in fields and (fields.get("source_spec") or "") != (before.get("source_spec") or "")
    enabled_changed = "enabled" in fields and bool(fields["enabled"]) != bool(before.get("enabled"))
    if source_changed or enabled_changed:
        with _cameras_lock:
            _start_worker(cam)  # stops any existing, then (re)starts if runnable
    return _serialize_camera(cam)


@app.delete("/api/cameras/{camera_id}")
def delete_camera(camera_id: str):
    with _cameras_lock:
        _stop_worker(camera_id)
    if not camera_store.delete_camera(camera_id):
        return _error(f"No camera '{camera_id}'.", status_code=404)
    return {"deleted": camera_id}


@app.post("/api/cameras/import")
def import_cameras(body: CsvImport):
    """Bulk-onboard cameras from CSV text. Columns (header row required):
    id,name,department,lat,lon,source_spec,ownership,storage_details,enabled.
    id is optional (auto-assigned); duplicate ids are skipped, not errored."""
    reader = csv.DictReader(io.StringIO(body.csv))
    added, skipped, errors = [], [], []
    to_start = []
    for line_no, row in enumerate(reader, start=2):  # header is line 1
        cam_id = (row.get("id") or "").strip() or _next_camera_id()
        fields = {
            "name": row.get("name"),
            "department": row.get("department"),
            "lat": _to_float(row.get("lat")),
            "lon": _to_float(row.get("lon")),
            "ownership": row.get("ownership"),
            "source_spec": row.get("source_spec"),
            "storage_details": row.get("storage_details"),
            "enabled": _to_bool(row.get("enabled"), default=True),
        }
        try:
            cam = camera_store.add_camera(cam_id, {k: v for k, v in fields.items() if v is not None})
            added.append(cam["id"])
            to_start.append(cam)
        except ValueError:
            skipped.append(cam_id)
        except Exception as exc:  # malformed row shouldn't abort the whole import
            errors.append({"line": line_no, "error": str(exc)})

    with _cameras_lock:
        for cam in to_start:
            try:
                _start_worker(cam)
            except Exception as exc:
                errors.append({"camera": cam["id"], "error": str(exc)})

    return {"added": added, "skipped": skipped, "errors": errors}


# --- Watchlist (Phase 3: continuous plate cross-referencing + alerting) -----


class WatchlistIn(BaseModel):
    plate: str
    label: str | None = None
    severity: str | None = None


class WatchlistActiveIn(BaseModel):
    active: bool


class SimulateSightingIn(BaseModel):
    camera_id: str
    plate: str


@app.get("/api/watchlist")
def get_watchlist():
    return {"entries": watchlist.list_entries()}


@app.post("/api/watchlist")
def add_watchlist_entry(body: WatchlistIn):
    try:
        entry = watchlist.add_plate(body.plate, label=body.label, severity=body.severity or "critical")
    except ValueError as exc:
        return _error(str(exc), status_code=409)
    return JSONResponse(status_code=201, content=entry)


@app.put("/api/watchlist/{entry_id}/active")
def set_watchlist_active(entry_id: int, body: WatchlistActiveIn):
    entry = watchlist.set_active(entry_id, body.active)
    if entry is None:
        return _error(f"No watchlist entry {entry_id}.", status_code=404)
    return entry


@app.delete("/api/watchlist/{entry_id}")
def delete_watchlist_entry(entry_id: int):
    if not watchlist.remove(entry_id):
        return _error(f"No watchlist entry {entry_id}.", status_code=404)
    return {"deleted": entry_id}


@app.post("/api/watchlist/simulate")
def simulate_watchlist_sighting(body: SimulateSightingIn):
    """Demo/test hook: pretend `camera_id`'s ANPR just read `plate`, running
    the REAL watchlist check + alert path — so a match can be demonstrated
    without a physical plate or a live OCR model installed. On a match it
    emits the same watchlist_match event + WebSocket alert the live pipeline
    would (using the camera's latest frame as the thumbnail if it's live)."""
    entry = watchlist.check_plate(body.plate)
    norm = watchlist.normalize_plate(body.plate)
    if entry is None:
        return {"matched": False, "plate": norm}

    worker = _cameras.get(body.camera_id)
    thumbnail = None
    if worker is not None:
        with worker.frame_lock:
            thumbnail = worker.latest_jpeg  # already a JPEG; reuse as-is

    event_id = _emit_watchlist_alert(
        camera_id=body.camera_id,
        plate=norm,
        entry=entry,
        tracker_id=0,
        class_name="car",
        thumbnail_jpeg=thumbnail,
    )
    return {"matched": True, "plate": norm, "event_id": event_id, "severity": entry.get("severity")}


class ThermalToggleRequest(BaseModel):
    enabled: bool


@app.get("/api/thermal/{camera_id}")
def get_thermal(camera_id: str):
    camera = _cameras.get(camera_id)
    if camera is None:
        return _error(f"'{camera_id}' has no live source configured.", status_code=404)
    return {"enabled": camera.thermal_enabled, "simulated": False, "mode": "Zero-DCE++"}


@app.post("/api/thermal/{camera_id}")
def set_thermal(camera_id: str, body: ThermalToggleRequest):
    camera = _cameras.get(camera_id)
    if camera is None:
        return _error(f"'{camera_id}' has no live source configured.", status_code=404)
    camera.thermal_enabled = body.enabled
    return {"enabled": camera.thermal_enabled, "simulated": False, "mode": "Zero-DCE++"}


# --- Zone config ---------------------------------------------------------
# Any number of cameras, any number of restricted-zone polygons each.
# Coordinates are percentages (0-100) of frame width/height, matching the
# frontend's resolution-independent drawing canvas — see zone_store.py.
MAX_ZONES_PER_CAMERA = 20


class ZoneIn(BaseModel):
    name: str
    polygon: list[list[float]]


class ZonesPayload(BaseModel):
    zones: list[ZoneIn]


@app.get("/api/zones/{camera_id}")
def get_zones(camera_id: str):
    return {"camera_id": camera_id, "zones": zone_store.load_zones_for_camera(camera_id)}


@app.put("/api/zones/{camera_id}")
def put_zones(camera_id: str, body: ZonesPayload):
    if len(body.zones) > MAX_ZONES_PER_CAMERA:
        return _error(f"Too many zones (max {MAX_ZONES_PER_CAMERA} per camera).")

    seen_names = set()
    for z in body.zones:
        name = z.name.strip()
        if not name:
            return _error("Every zone needs a non-empty name.")
        if name in seen_names:
            return _error(f"Duplicate zone name '{name}' — names must be unique per camera.")
        seen_names.add(name)
        if len(z.polygon) < 3:
            return _error(f"Zone '{name}' needs at least 3 points to form a polygon.")

    zones_pct = [{"name": z.name.strip(), "polygon": z.polygon} for z in body.zones]
    zone_store.save_zones_for_camera(camera_id, zones_pct)

    camera = _cameras.get(camera_id)
    hot_reloaded = camera is not None
    if camera is not None:
        camera.zone_manager.replace_zones([_zone_from_pct(z) for z in zones_pct])

    return {"camera_id": camera_id, "zones": zones_pct, "hot_reloaded": hot_reloaded}


def _error(message: str, status_code: int = 400):
    return JSONResponse(status_code=status_code, content={"detail": message})


def _mjpeg_generator(camera: CameraWorker):
    boundary = b"--frame"
    interval = 1.0 / STREAM_FPS
    while True:
        with camera.frame_lock:
            frame = camera.latest_jpeg
        if frame is not None:
            yield (
                boundary
                + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(frame)).encode()
                + b"\r\n\r\n"
                + frame
                + b"\r\n"
            )
        time.sleep(interval)


@app.get("/api/stream/{camera_id}")
def stream(camera_id: str):
    camera = _cameras.get(camera_id)
    if camera is None:
        # No live source configured for this camera — 404 so the
        # frontend's <img onError> falls back to its offline placeholder
        # instead of hanging on an empty response.
        return _error(f"'{camera_id}' has no live source configured.", status_code=404)
    return StreamingResponse(
        _mjpeg_generator(camera), media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            try:
                alert = _alert_queue.get_nowait()
                await websocket.send_text(json.dumps(alert))
            except queue.Empty:
                await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        pass


# --- Event history --------------------------------------------------------
# Real, queryable history backed by history_store.py (SQLite) — every
# zone-crossing event across every camera lands here automatically via
# CameraWorker._on_event, thumbnail included. This is what the History page
# reads instead of a hardcoded array.
MAX_HISTORY_LIMIT = 200


def _history_filters(camera_id: str | None, severity: str | None, since_hours: float | None):
    since_epoch = time.time() - since_hours * 3600 if since_hours else None
    cam = camera_id if camera_id and camera_id.lower() != "all" else None
    sev = severity if severity and severity.lower() != "all" else None
    return cam, sev, since_epoch


def _serialize_event(row: dict) -> dict:
    return {
        "id": row["id"],
        "timestamp": row["ts_iso"].replace("T", " "),
        "camera": row["camera_id"],
        "eventType": row["title"],
        "description": row["description"],
        "trackerId": f"#{row['tracker_id']}",
        "severity": row["severity"],
        "thumbnailUrl": f"/api/history/thumbnail/{row['id']}" if row["has_thumbnail"] else None,
    }


@app.get("/api/history")
def get_history(
    camera_id: str | None = None,
    severity: str | None = None,
    since_hours: float | None = None,
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(limit, MAX_HISTORY_LIMIT))
    offset = max(0, offset)
    cam, sev, since_epoch = _history_filters(camera_id, severity, since_hours)
    rows, total = history_store.query_events(
        camera_id=cam, severity=sev, since_epoch=since_epoch, limit=limit, offset=offset
    )
    return {"events": [_serialize_event(r) for r in rows], "total": total}


@app.get("/api/history/thumbnail/{event_id}")
def get_history_thumbnail(event_id: int):
    path = history_store.get_thumbnail_path(event_id)
    if path is None:
        return _error(f"No thumbnail stored for event {event_id}.", status_code=404)
    return FileResponse(path, media_type="image/jpeg")


@app.post("/api/history/{event_id}/explain")
def explain_history_event(event_id: int):
    """On-demand only — nothing calls Gemini automatically for every logged
    event. This fires the first time an operator expands a row in the
    History page; the result is cached on the row (history_store.
    set_explanation) so re-expanding it later, or another operator opening
    the same event, doesn't spend API quota again."""
    row = history_store.get_event(event_id)
    if row is None:
        return _error(f"No event with id {event_id}.", status_code=404)

    if row.get("explanation"):
        return {"explanation": row["explanation"], "cached": True}

    try:
        from src import gemini_explainer
    except ImportError:
        return _error(
            "The google-genai package isn't installed on the backend. "
            "Run: pip install google-genai",
            status_code=503,
        )

    thumb_path = history_store.get_thumbnail_path(event_id)
    thumbnail_jpeg = None
    if thumb_path:
        with open(thumb_path, "rb") as f:
            thumbnail_jpeg = f.read()

    try:
        text = gemini_explainer.explain_event(row, thumbnail_jpeg)
    except gemini_explainer.GeminiNotConfigured as exc:
        return _error(str(exc), status_code=503)
    except gemini_explainer.GeminiRequestFailed as exc:
        return _error(f"Gemini request failed: {exc}", status_code=502)

    history_store.set_explanation(event_id, text)
    return {"explanation": text, "cached": False}


@app.get("/api/history/export.csv")
def export_history_csv(
    camera_id: str | None = None,
    severity: str | None = None,
    since_hours: float | None = None,
):
    cam, sev, since_epoch = _history_filters(camera_id, severity, since_hours)
    rows, _total = history_store.query_events(
        camera_id=cam, severity=sev, since_epoch=since_epoch, limit=5000, offset=0
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Timestamp", "Camera", "Event", "Tracker ID", "Severity", "Description"])
    for r in rows:
        writer.writerow(
            [r["ts_iso"], r["camera_id"], r["title"], r["tracker_id"], r["severity"], r["description"]]
        )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ibvap_history_{stamp}.csv"'},
    )


@app.get("/api/analytics/report.pdf")
def generate_analytics_report():
    """Builds a real operational-report PDF straight from the event
    history database — summary stats plus the recent event log. Backs the
    Analytics page's "Generate Report" button (previously not wired to
    anything)."""
    from fpdf import FPDF  # imported lazily so a missing dep can't break the whole API
    from fpdf.enums import XPos, YPos

    NL = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}  # shorthand for "cell, then newline"

    stats = history_store.summary_stats()
    recent_rows, _total = history_store.query_events(limit=100, offset=0)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "IBVAP Operational Report", **NL)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 6, f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", **NL)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Summary", **NL)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Total events logged: {stats['total']}", **NL)
    if stats["earliest_ts"] and stats["latest_ts"]:
        lo = datetime.fromtimestamp(stats["earliest_ts"]).strftime("%Y-%m-%d %H:%M:%S")
        hi = datetime.fromtimestamp(stats["latest_ts"]).strftime("%Y-%m-%d %H:%M:%S")
        pdf.cell(0, 7, f"Time range covered: {lo} to {hi}", **NL)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "By severity", **NL)
    pdf.set_font("Helvetica", "", 10)
    for sev in ("critical", "warning", "info"):
        pdf.cell(0, 6, f"  {sev.upper()}: {stats['by_severity'].get(sev, 0)}", **NL)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "By camera", **NL)
    pdf.set_font("Helvetica", "", 10)
    if stats["by_camera"]:
        for cam_id, n in sorted(stats["by_camera"].items()):
            pdf.cell(0, 6, f"  {cam_id}: {n}", **NL)
    else:
        pdf.cell(0, 6, "  No events recorded yet.", **NL)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, f"Recent events (most recent {len(recent_rows)})", **NL)
    pdf.set_font("Helvetica", "B", 9)
    col_widths = (32, 22, 70, 20, 46)
    for w, htxt in zip(col_widths, ["Timestamp", "Camera", "Event", "Tracker", "Severity"]):
        pdf.cell(w, 7, htxt, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for r in recent_rows:
        pdf.cell(col_widths[0], 6, r["ts_iso"].replace("T", " "), border=1)
        pdf.cell(col_widths[1], 6, r["camera_id"], border=1)
        pdf.cell(col_widths[2], 6, r["title"][:45], border=1)
        pdf.cell(col_widths[3], 6, f"#{r['tracker_id']}", border=1)
        pdf.cell(col_widths[4], 6, r["severity"].upper(), border=1)
        pdf.ln()
    if not recent_rows:
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 8, "No events recorded yet.", **NL)

    pdf_bytes = bytes(pdf.output())
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="ibvap_report_{stamp}.pdf"'},
    )
