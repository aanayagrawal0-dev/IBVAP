"""
Snapshot previews for cameras that aren't loaded live.

On a bandwidth-limited link only IBVAP_MAX_LIVE cameras stream continuously
(with full AI). Every other enabled camera is refreshed as a still: one
background thread visits them ONE AT A TIME — connect, take a frame from
the keyframe the gateway replays on join, disconnect — so at most one extra
stream is ever open, and every capture is closed as soon as it's used.
"""

import threading
import time

import cv2

from src.ingestion import classify_source, open_source

PREVIEW_WIDTH = 640


class SnapshotService:
    def __init__(self, get_targets, interval_s=30.0, frames_per_grab=5, max_grab_s=15.0):
        # get_targets() -> [(camera_id, source_spec), ...] to refresh this cycle.
        self.get_targets = get_targets
        self.interval_s = interval_s
        self.frames_per_grab = frames_per_grab
        self.max_grab_s = max_grab_s
        self._images: dict[str, tuple[bytes, float]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="snapshots", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def get(self, camera_id) -> tuple[bytes, float] | None:
        with self._lock:
            return self._images.get(camera_id)

    def grab(self, camera_id, spec) -> bool:
        """Take one still. A few frames are read (they arrive fast from the
        replayed GOP) so the kept one is past any join-time decode artifacts."""
        if classify_source(spec) not in ("rtsp", "hls", "file"):
            return False  # e.g. never grab a local webcam from a second thread
        src = None
        try:
            src = open_source(spec, name=f"snap-{camera_id}",
                              max_reconnect_attempts=0, latest_frame_only=False)
            frame, t0 = None, time.monotonic()
            for i, (_idx, f) in enumerate(src.frames()):
                frame = f
                if i + 1 >= self.frames_per_grab or time.monotonic() - t0 > self.max_grab_s:
                    break
            if frame is None:
                return False
            h, w = frame.shape[:2]
            if w > PREVIEW_WIDTH:
                frame = cv2.resize(frame, (PREVIEW_WIDTH, int(h * PREVIEW_WIDTH / w)))
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if not ok:
                return False
            with self._lock:
                self._images[camera_id] = (buf.tobytes(), time.time())
            return True
        except Exception as exc:
            print(f"[snapshot] {camera_id}: {exc}")
            return False
        finally:
            if src is not None:
                src.release()

    def _loop(self):
        while not self._stop.is_set():
            started = time.monotonic()
            for camera_id, spec in self.get_targets():
                if self._stop.is_set():
                    return
                self.grab(camera_id, spec)
            self._stop.wait(max(1.0, self.interval_s - (time.monotonic() - started)))
