"""
Stream timing helpers — PTS-driven frame indexing and scene-cut detection.

Live gateway feeds (see ingestion.VideoSource) deliver frames at an uneven
cadence: the GOP replayed on connect arrives faster than real time, frames
are dropped when the model is slower than the feed, and inter-frame gaps
happen. Downstream stages (zones, behavior, tracker history) reason in
"frame indices" with a nominal rate, so instead of counting delivered frames
we derive the index from each frame's PTS:

    media_index = round(pts_seconds * NOMINAL_FPS)   (strictly increasing)

A 20 s dwell is then 20 s of *stream* time no matter how many frames were
actually processed, and neither CAP_PROP_FPS nor arrival time is used.

SceneCutDetector catches the gateway's loop point (and camera reboots): each
feed is a looping recording whose PTS stays monotonic across the cut, so
it can only be found visually. Long-lived per-scene state is reset on a cut.
"""

import cv2
import numpy as np


class MediaIndex:
    """Maps PTS seconds to a strictly increasing frame index at a nominal rate."""

    def __init__(self, nominal_fps: float):
        self.nominal_fps = float(nominal_fps)
        self._base = None
        self._last = -1

    def index(self, pts_s: float) -> int:
        if self._base is None:
            # Continue after the last index handed out (also after rebase()).
            self._base = pts_s - (self._last + 1) / self.nominal_fps
        v = int(round((pts_s - self._base) * self.nominal_fps))
        v = max(v, self._last + 1)
        self._last = v
        return v

    def rebase(self):
        """Start a new timeline segment. Indices keep increasing, so state
        keyed on earlier indices can never see time run backwards."""
        self._base = None


class SceneCutDetector:
    """Hard-cut detector on a tiny grayscale thumbnail: a cut is flagged when
    the luminance histogram decorrelates AND the pixels change broadly. Cheap
    enough to run on every processed frame."""

    def __init__(self, hist_corr_threshold=0.5, pixel_diff_threshold=0.2,
                 min_interval_s=3.0, size=(64, 36)):
        self.hist_corr_threshold = hist_corr_threshold
        self.pixel_diff_threshold = pixel_diff_threshold
        self.min_interval_s = min_interval_s
        self.size = size
        self._prev_small = None
        self._prev_hist = None
        self._last_cut_pts = None

    def reset(self):
        self._prev_small = None
        self._prev_hist = None

    def update(self, frame_bgr, pts_s: float) -> bool:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
        small = cv2.resize(gray, self.size, interpolation=cv2.INTER_AREA)
        hist = cv2.calcHist([small], [0], None, [32], [0, 256])
        cv2.normalize(hist, hist)

        cut = False
        if self._prev_small is not None:
            corr = cv2.compareHist(self._prev_hist, hist, cv2.HISTCMP_CORREL)
            diff = float(np.mean(cv2.absdiff(small, self._prev_small))) / 255.0
            recent = (self._last_cut_pts is not None
                      and pts_s - self._last_cut_pts < self.min_interval_s)
            if corr < self.hist_corr_threshold and diff > self.pixel_diff_threshold and not recent:
                cut = True
                self._last_cut_pts = pts_s
        self._prev_small = small
        self._prev_hist = hist
        return cut
