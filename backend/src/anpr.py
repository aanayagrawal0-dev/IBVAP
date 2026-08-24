"""
Event-driven ANPR (Automatic Number Plate Recognition) engine.

Uses PaddleOCR to extract license plate text from vehicle bounding-box
crops.  Only invoked on alert frames — never per-frame — so it adds
negligible overhead to the 30+ FPS pipeline.

Preprocessing: grayscale + CLAHE contrast stretching.
Post-processing: regex filter + confidence threshold.
"""

import re
import cv2
import numpy as np

_PLATE_RE = re.compile(r"[^A-Z0-9]")
_MIN_CONF = 0.40

# Vehicle COCO class IDs recognised by this module.
VEHICLE_CLASS_IDS = {2, 3, 5, 7}  # car, motorcycle, bus, truck


class ANPREngine:
    """Lightweight wrapper around PaddleOCR with graceful fallback."""

    def __init__(self):
        self.enabled = False
        try:
            from paddleocr import PaddleOCR
            self.ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
            self.enabled = True
        except Exception as e:
            print(
                f"[ANPR Warning] PaddleOCR failed to initialize: {e}. "
                "Falling back to disabled state."
            )

    # ------------------------------------------------------------------
    def extract_plate(self, frame: np.ndarray, x1, y1, x2, y2) -> str | None:
        """Crop, preprocess, OCR, filter.  Returns cleaned plate string or
        None when nothing useful was detected."""
        if not self.enabled:
            return None

        try:
            crop = self._safe_crop(frame, x1, y1, x2, y2)
            if crop is None or crop.size == 0:
                return None

            processed = self._preprocess(crop)
            result = self.ocr.ocr(processed, cls=False)

            if not result or not result[0]:
                return None

            return self._best_plate(result[0])
        except Exception:
            return None  # never interrupt the pipeline

    # ------------------------------------------------------------------
    @staticmethod
    def _safe_crop(frame, x1, y1, x2, y2) -> np.ndarray | None:
        """Boundary-clipped crop so edge detections don't throw."""
        h, w = frame.shape[:2]
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w, int(x2)), min(h, int(y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    @staticmethod
    def _preprocess(crop: np.ndarray) -> np.ndarray:
        """Grayscale + CLAHE contrast stretching to sharpen plate chars."""
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        return clahe.apply(gray)

    @staticmethod
    def _best_plate(lines) -> str | None:
        """Pick the highest-confidence line, clean it, and return it if it
        has at least 4 alphanumeric chars (shortest real plate length)."""
        best_text, best_conf = None, 0.0
        for line in lines:
            text_block = line[1]  # (bbox, (text, conf))
            raw_text, conf = text_block[0], text_block[1]
            if conf >= _MIN_CONF and conf > best_conf:
                cleaned = _PLATE_RE.sub("", raw_text.upper())
                if len(cleaned) >= 4:
                    best_text, best_conf = cleaned, conf
        return best_text
