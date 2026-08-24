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
import json
import os
import queue
import threading
import time
from datetime import datetime

import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from src.pipeline import Pipeline
from src.zones import Zone, ZoneManager, ZoneEventType
from src import zone_store


def _parse_video_source(raw: str):
    """A bare integer string ("0", "1", ...) means a local webcam device
    index. Anything else — an RTSP URL or a file path — is passed through
    as-is to cv2.VideoCapture. An empty/unset string means "this camera has
    no live source" — it's simply not started (see CAMERA_SOURCES below)."""
    raw = raw.strip()
    return int(raw) if raw.lstrip("-").isdigit() else raw


# --- Config -----------------------------------------------------------
WEIGHTS_PATH = "models/yolov8n.pt"
STREAM_FPS = 15
# No fixed frame resolution here on purpose — zones are percentage-based
# and converted against each frame's actual dimensions in pipeline.py, so
# different cameras can run at whatever resolution their source provides.

# One entry per camera the frontend knows about (see frontend/lib/mock-data
# .ts). Each is independently overridable via its own env var — an empty
# value means that camera simply isn't run (GET /api/stream/{id} 404s and
# the frontend falls back to its offline placeholder for it).
#
#   IBVAP_CAM01_SOURCE=0                        laptop/USB webcam
#   IBVAP_CAM02_SOURCE=rtsp://192.168.1.50/...  a real IP camera
#   IBVAP_CAM03_SOURCE=sample_data/x.mp4        a recorded clip
#   IBVAP_CAM04_SOURCE=                         (default) not run
#
# CAM-01 defaults to your webcam so there's always one genuinely live feed
# out of the box. CAM-02/03 default to the bundled demo clip — the repo
# only ships one recorded clip, so both play it independently (separate
# VideoCapture instances, so they *will* drift out of sync with each
# other — they're not mirrors of one shared decode). Point them at your
# own footage via the env vars above for visually distinct feeds.
_DEFAULT_SOURCES = {
    "CAM-01": "0",
    "CAM-02": "sample_data/synthetic_test_clip.mp4",
    "CAM-03": "sample_data/synthetic_test_clip.mp4",
    "CAM-04": "",
}
CAMERA_SOURCES = {
    cam_id: os.environ.get(f"IBVAP_{cam_id.replace('-', '')}_SOURCE", default)
    for cam_id, default in _DEFAULT_SOURCES.items()
}

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
_event_counter_lock = threading.Lock()
_event_counter = 0


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
            loop=True,
            target_fps=STREAM_FPS,
            stop_flag=lambda: self.stop_requested,
        )

    def _on_frame(self, annotated_bgr):
        frame = _apply_thermal_colormap(annotated_bgr) if self.thermal_enabled else annotated_bgr
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self.frame_lock:
                self.latest_jpeg = buf.tobytes()

    def _on_event(self, evt, class_name):
        global _event_counter
        with _event_counter_lock:
            _event_counter += 1
            event_id = _event_counter
        entered = evt.event_type == ZoneEventType.ENTERED
        alert = {
            "id": f"evt-{event_id}",
            "severity": "critical" if entered else "warning",
            "title": f"{class_name.upper()} {'ENTERED' if entered else 'EXITED'} ZONE",
            "description": (
                f"Tracked object #{evt.tracker_id} ({class_name}) "
                f"{'entered' if entered else 'exited'} '{evt.zone_name}'."
            ),
            "camera": self.camera_id,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        try:
            _alert_queue.put_nowait(alert)
        except queue.Full:
            pass  # drop rather than block the CV thread if nobody's draining it


# Only cameras with a non-empty configured source actually run. The rest
# stay fully configurable via the zone-config UI (their zones are saved
# and waiting) but simply aren't started — see CAMERA_SOURCES above.
_cameras: dict[str, CameraWorker] = {
    cam_id: CameraWorker(cam_id, _parse_video_source(raw))
    for cam_id, raw in CAMERA_SOURCES.items()
    if raw.strip() != ""
}


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
    for camera in _cameras.values():
        camera.start()


@app.on_event("shutdown")
def _shutdown():
    for camera in _cameras.values():
        camera.stop()


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "cameras": {cam_id: cam.is_alive() for cam_id, cam in _cameras.items()},
    }


class ThermalToggleRequest(BaseModel):
    enabled: bool


@app.get("/api/thermal/{camera_id}")
def get_thermal(camera_id: str):
    camera = _cameras.get(camera_id)
    if camera is None:
        return _error(f"'{camera_id}' has no live source configured.", status_code=404)
    return {"enabled": camera.thermal_enabled, "simulated": True}


@app.post("/api/thermal/{camera_id}")
def set_thermal(camera_id: str, body: ThermalToggleRequest):
    # NOTE: simulated=True always — this is a false-color visible-light
    # transform for a night-time-viewing demo, not real IR/thermal sensing.
    camera = _cameras.get(camera_id)
    if camera is None:
        return _error(f"'{camera_id}' has no live source configured.", status_code=404)
    camera.thermal_enabled = body.enabled
    return {"enabled": camera.thermal_enabled, "simulated": True}


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
