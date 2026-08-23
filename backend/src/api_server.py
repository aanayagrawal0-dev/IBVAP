"""
FastAPI bridge between the CV pipeline and the SENTINEL-X frontend.

Runs the Pipeline.stream() loop in a background thread (OpenCV/ultralytics
are blocking, synchronous libraries — they do not belong on the asyncio
event loop). The thread hands frames and events off through thread-safe
primitives that the async HTTP/WebSocket handlers read from:

  - latest_frame (bytes, JPEG-encoded) behind a threading.Lock, polled by
    GET /api/stream for an MJPEG feed.
  - a queue.Queue of formatted alert dicts, drained by WS /ws/alerts and
    broadcast to every connected browser tab.

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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.pipeline import Pipeline
from src.zones import Zone, ZoneManager, ZoneEventType


def _parse_video_source(raw: str):
    """A bare integer string ("0", "1", ...) means a local webcam device
    index. Anything else — an RTSP URL or a file path — is passed through
    as-is to cv2.VideoCapture."""
    return int(raw) if raw.strip().lstrip("-").isdigit() else raw


# --- Config -----------------------------------------------------------
# Override with an env var, e.g.:
#   IBVAP_VIDEO_SOURCE=0                 (laptop webcam)
#   IBVAP_VIDEO_SOURCE=rtsp://...        (real IP camera)
#   IBVAP_VIDEO_SOURCE=sample_data/x.mp4 (recorded file, default)
VIDEO_SOURCE = _parse_video_source(
    os.environ.get("IBVAP_VIDEO_SOURCE", "sample_data/synthetic_test_clip.mp4")
)
WEIGHTS_PATH = "models/yolov8n.pt"
CAMERA_ID = "CAM-01"
STREAM_FPS = 15
FRAME_W, FRAME_H = 960, 540

DEFAULT_ZONE = Zone(
    name="restricted-zone",
    polygon=[
        (FRAME_W * 0.6, 0),
        (FRAME_W, 0),
        (FRAME_W, FRAME_H),
        (FRAME_W * 0.6, FRAME_H),
    ],
)

# --- Shared state between the background CV thread and the async app --
_frame_lock = threading.Lock()
_latest_jpeg: bytes | None = None
_alert_queue: "queue.Queue[dict]" = queue.Queue(maxsize=200)
_stop_requested = False
_event_counter = 0

# Thermal vision is SIMULATED — there is no IR/thermal sensor. Toggling it
# applies a false-color heat-map style transform to the ordinary visible-
# light frame, which is a reasonable night-time-viewing stand-in for a demo
# but must never be represented to end users as real thermal imaging.
_thermal_enabled = False


def _apply_thermal_colormap(bgr_frame):
    """False-color 'thermal' look: grayscale intensity remapped through a
    heat palette (dark purple -> orange -> yellow, like a FLIR display)."""
    gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
    # Boost contrast a bit so the false-color mapping reads clearly instead
    # of collapsing into a narrow mid-tone band.
    gray = cv2.equalizeHist(gray)
    return cv2.applyColorMap(gray, cv2.COLORMAP_INFERNO)


def _on_frame(annotated_bgr):
    global _latest_jpeg
    frame = _apply_thermal_colormap(annotated_bgr) if _thermal_enabled else annotated_bgr
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if ok:
        with _frame_lock:
            _latest_jpeg = buf.tobytes()


def _on_event(evt, class_name):
    global _event_counter
    _event_counter += 1
    entered = evt.event_type == ZoneEventType.ENTERED
    alert = {
        "id": f"evt-{_event_counter}",
        "severity": "critical" if entered else "warning",
        "title": f"{class_name.upper()} {'ENTERED' if entered else 'EXITED'} ZONE",
        "description": (
            f"Tracked object #{evt.tracker_id} ({class_name}) "
            f"{'entered' if entered else 'exited'} '{evt.zone_name}'."
        ),
        "camera": CAMERA_ID,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    }
    try:
        _alert_queue.put_nowait(alert)
    except queue.Full:
        pass  # drop rather than block the CV thread if nobody's draining it


def _run_pipeline():
    zone_manager = ZoneManager(zones=[DEFAULT_ZONE])
    pipeline = Pipeline(
        source_uri=VIDEO_SOURCE,
        zone_manager=zone_manager,
        weights=WEIGHTS_PATH,
        conf_threshold=0.30,
        source_name=CAMERA_ID,
    )
    pipeline.stream(
        on_frame=_on_frame,
        on_event=_on_event,
        loop=True,
        target_fps=STREAM_FPS,
        stop_flag=lambda: _stop_requested,
    )


_worker_thread: threading.Thread | None = None


def _start_worker():
    global _worker_thread
    _worker_thread = threading.Thread(target=_run_pipeline, daemon=True)
    _worker_thread.start()


def _stop_worker():
    global _stop_requested
    _stop_requested = True
    if _worker_thread:
        _worker_thread.join(timeout=5)


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
    _start_worker()


@app.on_event("shutdown")
def _shutdown():
    _stop_worker()


@app.get("/api/health")
def health():
    return {"status": "ok", "worker_alive": bool(_worker_thread and _worker_thread.is_alive())}


class ThermalToggleRequest(BaseModel):
    enabled: bool


@app.get("/api/thermal")
def get_thermal():
    return {"enabled": _thermal_enabled, "simulated": True}


@app.post("/api/thermal")
def set_thermal(body: ThermalToggleRequest):
    # NOTE: simulated=True always — this is a false-color visible-light
    # transform for a night-time-viewing demo, not real IR/thermal sensing.
    global _thermal_enabled
    _thermal_enabled = body.enabled
    return {"enabled": _thermal_enabled, "simulated": True}


def _mjpeg_generator():
    boundary = b"--frame"
    interval = 1.0 / STREAM_FPS
    while True:
        with _frame_lock:
            frame = _latest_jpeg
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
    # Single shared feed for now (one Pipeline instance) — camera_id is
    # accepted so the frontend's per-camera URL scheme doesn't need to
    # change when multiple real feeds land later.
    return StreamingResponse(
        _mjpeg_generator(), media_type="multipart/x-mixed-replace; boundary=frame"
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
