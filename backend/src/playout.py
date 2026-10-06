"""
Process-ahead playout for buffered feeds (portal HLS with AI on).

The HLS reader holds video before it's shown, so the AI can analyse frames
AHEAD of playback instead of racing the display. The AI loop pushes every
finished (annotated, JPEG-encoded) frame here with its PTS; a playout thread
releases them at the video's own rate after a short start delay. Result:
every frame is analysed, boxes belong to the exact frame they're drawn on,
and slow AI moments are absorbed by the lead instead of showing up as
choppiness.

Alerts found by the AI are attached to their frame and fire when that frame
is PLAYED, so the operator sees the alert and the picture together.

Backpressure keeps it gentle: push() blocks once the AI is `max_ahead_s`
ahead, so after the initial fill the AI simply runs at the video's pace.
"""

import collections
import threading
import time


class PlayoutBuffer:
    def __init__(self, publish, start_delay_s=2.0, max_ahead_s=10.0, stop_flag=None):
        # publish(jpeg_bytes) is called for each frame at its playback time.
        self.publish = publish
        self.start_delay_s = start_delay_s
        self.max_ahead_s = max_ahead_s
        self.stop_flag = stop_flag or (lambda: False)
        self._q = collections.deque()  # [pts_s, jpeg, [deferred callables], played]
        self._cond = threading.Condition()
        self._closed = False
        self.stalls = 0
        self._thread = threading.Thread(target=self._run, name="playout", daemon=True)
        self._thread.start()

    def _span(self):
        return self._q[-1][0] - self._q[0][0] if len(self._q) > 1 else 0.0

    def ahead_s(self) -> float:
        with self._cond:
            return round(self._span(), 1)

    def push(self, pts_s, jpeg):
        """Queue an analysed frame. Blocks while the AI is max_ahead_s ahead.
        Returns the queued item so alerts for this frame can be attached."""
        item = [pts_s, jpeg, [], False]
        with self._cond:
            while (self._span() >= self.max_ahead_s and not self._closed
                   and not self.stop_flag()):
                self._cond.wait(timeout=0.25)
            self._q.append(item)
            self._cond.notify_all()
        return item

    def defer(self, item, fn):
        """Run fn when `item` is played (immediately if it already was)."""
        with self._cond:
            if not item[3]:
                item[2].append(fn)
                return
        fn()

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def _wait_for_start(self):
        """Hold playback until start_delay_s of analysed video is queued."""
        with self._cond:
            while (self._span() < self.start_delay_s and not self._closed and not self.stop_flag()):
                self._cond.wait(timeout=0.25)

    def _run(self):
        self._wait_for_start()
        anchor = None  # (monotonic wall, pts)
        last_pts = None
        while not self._closed and not self.stop_flag():
            with self._cond:
                if not self._q:
                    self.stalls += 1
                    anchor = None
                while not self._q and not self._closed and not self.stop_flag():
                    self._cond.wait(timeout=0.25)
                if not self._q:
                    continue
                pts = self._q[0][0]
            # A loop point / PTS jump / resume after a stall restarts the clock.
            if anchor is None or last_pts is None or pts < last_pts or pts - last_pts > 1.0:
                anchor = (time.monotonic(), pts)
            due = anchor[0] + (pts - anchor[1])
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(min(delay, 1.0))
                if delay > 1.0:
                    continue  # re-check (stop/close) during long waits
            with self._cond:
                item = self._q.popleft()
                item[3] = True
                self._cond.notify_all()
            last_pts = item[0]
            for fn in item[2]:
                try:
                    fn()
                except Exception as exc:  # an alert handler must never stop playback
                    print(f"[playout] deferred alert failed: {exc}")
            self.publish(item[1])
