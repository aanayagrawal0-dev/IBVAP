"""
Selective redaction (Phase 5).

Privacy-preserving export: pixelate bystanders while keeping the subject of
interest visible. Reuses the same person detections the pipeline already
produces — for an exported clip we blur every detected person region so
uninvolved pedestrians are anonymised, while a watchlisted VEHICLE (not a
person) stays fully visible. An optional keep-set leaves specified boxes
untouched, for when a specific subject must remain in frame.

Split into a pure `redact_boxes` (pixelate given regions — trivially testable)
and `redact_clip` (drive it over a video via a caller-supplied boxes_fn, so
redaction has no hard dependency on the detector).
"""

import cv2
import numpy as np

from src.ingestion import open_source

# COCO person class id — the "bystander" class blurred by default.
PERSON_CLASS_ID = 0


def redact_boxes(frame: np.ndarray, boxes, blocks: int = 12) -> np.ndarray:
    """Pixelate each (x1, y1, x2, y2) region of *frame* in place and return it.
    Pixelation (downscale → nearest-neighbour upscale) makes faces
    unrecognisable while keeping the redaction obvious on screen."""
    h, w = frame.shape[:2]
    for box in boxes:
        x1, y1, x2, y2 = (int(round(v)) for v in box)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        roi = frame[y1:y2, x1:x2]
        small_w = max(1, (x2 - x1) // blocks)
        small_h = max(1, (y2 - y1) // blocks)
        small = cv2.resize(roi, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
        frame[y1:y2, x1:x2] = cv2.resize(small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST)
    return frame


def person_boxes_from_detector(detector, keep_boxes=None, iou_keep=0.5):
    """Build a boxes_fn(frame) -> list[box] that returns every person box the
    detector finds, minus any that overlap a keep box (the protected subject)."""
    keep_boxes = keep_boxes or []

    def boxes_fn(frame):
        dets = detector.detect(frame)
        out = []
        for i in range(len(dets)):
            if int(dets.class_id[i]) != PERSON_CLASS_ID:
                continue
            box = [float(v) for v in dets.xyxy[i]]
            if any(_iou(box, k) >= iou_keep for k in keep_boxes):
                continue  # protected subject — leave visible
            out.append(box)
        return out

    return boxes_fn


def redact_clip(source_spec, out_path, boxes_fn, max_frames=None, fps=None) -> dict:
    """Read *source_spec*, blur the boxes returned by boxes_fn(frame) on each
    frame, and write an mp4 to out_path. Returns {frames, redacted_regions, path}."""
    src = open_source(source_spec, name="export")
    writer = None
    n = 0
    total_regions = 0
    try:
        for _idx, frame in src.frames():
            if max_frames is not None and n >= max_frames:
                break
            boxes = boxes_fn(frame) or []
            total_regions += len(boxes)
            redacted = redact_boxes(frame, list(boxes))
            if writer is None:
                h, w = redacted.shape[:2]
                writer = cv2.VideoWriter(
                    out_path, cv2.VideoWriter_fourcc(*"mp4v"), float(fps or src.fps), (w, h)
                )
            writer.write(redacted)
            n += 1
    finally:
        if writer is not None:
            writer.release()
        src.release()
    return {"frames": n, "redacted_regions": total_regions, "path": out_path}


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    return inter / (area_a + area_b - inter + 1e-9)
