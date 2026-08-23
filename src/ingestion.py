"""
Video ingestion layer.

Wraps OpenCV's VideoCapture to give a uniform interface over:
  - local video files (for dev/testing and recorded-footage demos)
  - RTSP streams (real IP cameras in the field)

Handles reconnects on RTSP streams, since border-area links drop.
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
        self.is_stream = isinstance(uri, str) and uri.lower().startswith("rtsp://")
        self.cap = None
        self._open()

    def _open(self):
        uri = int(self.uri) if isinstance(self.uri, str) and self.uri.isdigit() else self.uri
        self.cap = cv2.VideoCapture(uri)
        if not self.cap.isOpened():
            raise ConnectionError(f"[{self.name}] could not open source: {self.uri}")

    def _reconnect(self):
        uri = int(self.uri) if isinstance(self.uri, str) and self.uri.isdigit() else self.uri
        for attempt in range(1, self.max_reconnect_attempts + 1):
            print(f"[{self.name}] reconnect attempt {attempt}/{self.max_reconnect_attempts}...")
            time.sleep(self.reconnect_delay_s)
            self.cap.release()
            self.cap = cv2.VideoCapture(uri)
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
