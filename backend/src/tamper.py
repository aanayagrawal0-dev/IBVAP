"""
Camera tampering / health detection (Phase 6.1).

Cheap, GPU-free integrity check on the raw frame — catches the three classic
tamper modes without any model:

  * covered   — lens blocked / blacked out: very dark AND very flat.
  * defocus   — lost focus or smeared: high-frequency energy (Laplacian
                variance) collapses well below the learned baseline.
  * scene_change — camera moved or view obstructed: the frame's intensity
                histogram stops correlating with the running reference.

A short warm-up learns the baseline sharpness and reference histogram; while
"ok" the reference adapts slowly (EMA) so gradual lighting changes don't false
-trigger. `update(frame)` returns the current status and flags the moment the
state changes, which is what the pipeline turns into an alert — this is the
"cover the lens, watch it alert instantly" demo.
"""

import cv2
import numpy as np


class TamperDetector:
    def __init__(self, warmup_frames=12, dark_mean=18.0, dark_std=12.0,
                 defocus_ratio=0.25, defocus_abs=60.0, scene_corr=0.35, ema=0.02):
        self.warmup_frames = warmup_frames
        self.dark_mean = dark_mean
        self.dark_std = dark_std
        self.defocus_ratio = defocus_ratio
        self.defocus_abs = defocus_abs
        self.scene_corr = scene_corr
        self.ema = ema

        self.count = 0
        self.baseline_lap = None      # learned sharpness baseline
        self.reference_hist = None    # learned intensity histogram
        self.state = "warming_up"

    @staticmethod
    def _hist(gray):
        h = cv2.calcHist([gray], [0], None, [64], [0, 256])
        return cv2.normalize(h, h).flatten()

    def update(self, frame_bgr) -> dict:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        mean = float(gray.mean())
        std = float(gray.std())
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        hist = self._hist(gray)

        self.count += 1
        if self.count <= self.warmup_frames or self.baseline_lap is None:
            # Learn the baseline (running average of sharpness + reference hist).
            self.baseline_lap = lap_var if self.baseline_lap is None else 0.5 * (self.baseline_lap + lap_var)
            self.reference_hist = hist if self.reference_hist is None else self.reference_hist
            self.state = "warming_up"
            return self._status(None, mean, std, lap_var, None, transitioned=False)

        corr = float(cv2.compareHist(self.reference_hist, hist, cv2.HISTCMP_CORREL))

        if mean < self.dark_mean and std < self.dark_std:
            issue = "covered"
        elif lap_var < self.baseline_lap * self.defocus_ratio and lap_var < self.defocus_abs:
            issue = "defocus"
        elif corr < self.scene_corr:
            issue = "scene_change"
        else:
            issue = None

        new_state = issue or "ok"
        transitioned = new_state != self.state and self.state != "warming_up"
        # Only adapt the reference while healthy, so a tamper doesn't get baked
        # into the baseline.
        if issue is None:
            self.reference_hist = (1 - self.ema) * self.reference_hist + self.ema * hist
            self.baseline_lap = (1 - self.ema) * self.baseline_lap + self.ema * lap_var
        self.state = new_state
        return self._status(issue, mean, std, lap_var, corr, transitioned)

    def _status(self, issue, mean, std, lap_var, corr, transitioned) -> dict:
        return {
            "state": self.state,
            "issue": issue,
            "healthy": issue is None and self.state == "ok",
            "transitioned": transitioned,
            "metrics": {
                "brightness": round(mean, 1),
                "contrast": round(std, 1),
                "sharpness": round(lap_var, 1),
                "scene_corr": round(corr, 3) if corr is not None else None,
            },
        }


_ISSUE_TEXT = {
    "covered": ("critical", "CAMERA TAMPERING — LENS COVERED",
                "Feed is dark and flat — the lens appears covered or blocked."),
    "defocus": ("warning", "CAMERA HEALTH — DEFOCUSED",
                "Image sharpness collapsed — the camera appears defocused or smeared."),
    "scene_change": ("warning", "CAMERA TAMPERING — VIEW CHANGED",
                     "The scene no longer matches the reference — the camera may have been moved or obstructed."),
}


def issue_to_alert(issue: str, camera_id: str) -> dict | None:
    """Map a tamper issue to (severity, title, description) for an alert."""
    if issue not in _ISSUE_TEXT:
        return None
    severity, title, desc = _ISSUE_TEXT[issue]
    return {"severity": severity, "title": title, "description": f"[{camera_id}] {desc}"}
