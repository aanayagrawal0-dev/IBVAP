"""
Weapon detection (Phase 6.2) — the SAME two-stage crop-and-classify shape as
the pose stage: crop each tracked person, run a weapon classifier on the crop,
alert if it fires.

The classifier is pluggable. Point IBVAP_WEAPON_MODEL at a fine-tuned model
(e.g. an ultralytics weapon-detection .pt trained from an open pretrained
model) to enable it. With no model it stays cleanly DISABLED — exactly like
ANPR without PaddleOCR — so the integration is in place and ready, without
fabricating detections. Tests inject a `classify_fn` directly.
"""

import os

import numpy as np


def _safe_crop(frame, box):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return frame[y1:y2, x1:x2]


class WeaponDetector:
    """classify_fn(crop_bgr) -> list[(label, confidence)]. Provided directly
    (tests), or built from an ultralytics model at IBVAP_WEAPON_MODEL."""

    def __init__(self, model_path=None, conf=0.40, classify_fn=None):
        self.conf = conf
        self._classify = classify_fn
        self.enabled = classify_fn is not None

        if self._classify is None:
            mp = model_path or os.environ.get("IBVAP_WEAPON_MODEL")
            if mp and os.path.exists(mp):
                try:
                    from ultralytics import YOLO
                    model = YOLO(mp)

                    def _cf(crop):
                        r = model(crop, conf=self.conf, verbose=False)[0]
                        out = []
                        for k in range(len(r.boxes)):
                            out.append((model.names[int(r.boxes.cls[k])], float(r.boxes.conf[k])))
                        return out

                    self._classify = _cf
                    self.enabled = True
                except Exception as exc:  # pragma: no cover
                    print(f"[Weapon] failed to load {mp}: {exc}")

    def detect(self, frame: np.ndarray, person_boxes) -> list[dict]:
        """person_boxes: list of (tracker_id, (x1,y1,x2,y2)). Empty when disabled."""
        if not self.enabled or self._classify is None:
            return []
        events = []
        for tid, box in person_boxes:
            crop = _safe_crop(frame, box)
            if crop is None:
                continue
            for label, conf in self._classify(crop) or []:
                if conf >= self.conf:
                    events.append({
                        "type": "weapon",
                        "severity": "critical",
                        "title": f"WEAPON DETECTED — {str(label).upper()}",
                        "description": f"Possible {label} on object #{tid} (confidence {conf:.2f}).",
                        "tracker_id": tid,
                        "confidence": round(float(conf), 3),
                        "label": label,
                    })
                    break
        return events
