"""
Builds a short synthetic test clip from real photographic stills (people +
a bus) by panning/zooming a crop window across each image. This is NOT a
substitute for real CCTV footage — it exists purely to smoke-test the
ingestion -> detection -> tracking -> zone pipeline end to end with content
YOLO can actually detect, since no real border/surveillance footage is
available in this environment.

Swap this for real recorded footage (or an RTSP feed) before trusting any
detection-quality numbers.
"""

import cv2
import numpy as np
import os

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_data")
OUT_PATH = os.path.join(SRC_DIR, "synthetic_test_clip.mp4")

OUT_W, OUT_H = 960, 540
FPS = 25
SECONDS_PER_IMAGE = 4


def pan_frames(img, n_frames):
    """Yield n_frames frames, each a crop window sliding left->right and
    zooming slightly, giving detected objects an actual trajectory for the
    tracker to follow."""
    h, w = img.shape[:2]
    crop_w, crop_h = int(w * 0.55), int(h * 0.9)
    max_x = w - crop_w
    max_y = (h - crop_h) // 2

    for i in range(n_frames):
        t = i / max(n_frames - 1, 1)
        x = int(t * max_x)
        y = max_y
        crop = img[y:y + crop_h, x:x + crop_w]
        frame = cv2.resize(crop, (OUT_W, OUT_H))
        yield frame


def main():
    bus = cv2.imread(os.path.join(SRC_DIR, "bus.jpg"))
    zidane = cv2.imread(os.path.join(SRC_DIR, "zidane.jpg"))
    if bus is None or zidane is None:
        raise FileNotFoundError("Expected bus.jpg and zidane.jpg in sample_data/")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(OUT_PATH, fourcc, FPS, (OUT_W, OUT_H))

    n = FPS * SECONDS_PER_IMAGE
    for img in (zidane, bus, zidane):
        for frame in pan_frames(img, n):
            writer.write(frame)

    writer.release()
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
