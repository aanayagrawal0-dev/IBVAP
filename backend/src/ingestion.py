"""
Video ingestion layer.

Wraps OpenCV's VideoCapture to give a uniform interface over:
  - local video files (for dev/testing and recorded-footage demos)
  - RTSP streams (real IP cameras in the field)
  - a local webcam, passed as an integer device index (e.g. 0) — useful for
    a live demo when no RTSP camera is available

Handles reconnects on RTSP streams and webcams, since border-area links
drop and USB/webcam devices can also glitch; a local video file is treated
as finite and simply ends at EOF instead.
"""

import time
import cv2


class VideoSource:
    """Iterable frame source with automatic reconnect for RTSP streams."""

    def __init__(self, uri, name="camera", reconnect_delay_s=2.0, max_reconnect_attempts=5):
        self.uri = uri
        self.name = name
        self.reconnect_delay_s = reconnect_delay_s
        self.max_reconnect_attempts = max_reconnect_attempts
        # Anything that isn't a path to a finite local file is treated as a
        # "live" source that should reconnect on failure rather than just
        # stopping: RTSP URLs and webcam device indices both qualify.
        self.is_stream = isinstance(uri, int) or (
            isinstance(uri, str) and uri.lower().startswith("rtsp://")
        )
        self.cap = None
        self._open()

    def _open(self):
        self.cap = cv2.VideoCapture(self.uri)
        if not self.cap.isOpened():
            raise ConnectionError(f"[{self.name}] could not open source: {self.uri}")

    def _reconnect(self):
        for attempt in range(1, self.max_reconnect_attempts + 1):
            print(f"[{self.name}] reconnect attempt {attempt}/{self.max_reconnect_attempts}...")
            time.sleep(self.reconnect_delay_s)
            self.cap.release()
            self.cap = cv2.VideoCapture(self.uri)
            if self.cap.isOpened():
                print(f"[{self.name}] reconnected.")
                return True
        return False

    @property
    def fps(self):
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        return fps if fps and fps > 0 else 25.0

    def frames(self):
        """Yield (frame_index, frame) tuples. Blocks/reconnects on RTSP drop;
        stops cleanly at EOF for file sources."""
        idx = 0
        while True:
            ok, frame = self.cap.read()
            if not ok:
                if self.is_stream:
                    print(f"[{self.name}] stream read failed, attempting reconnect...")
                    if not self._reconnect():
                        print(f"[{self.name}] giving up after {self.max_reconnect_attempts} attempts.")
                        break
                    continue
                else:
                    break  # end of file — normal termination for recorded footage
            yield idx, frame
            idx += 1

    def release(self):
        if self.cap is not None:
            self.cap.release()
