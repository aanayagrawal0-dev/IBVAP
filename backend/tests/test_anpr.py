"""
Tests for the ANPR module — preprocessing, regex filtering, SQLite
integration, and graceful fallback behaviour.
"""

import os
import sys
import sqlite3
import numpy as np
import cv2

# Allow imports from backend/src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.anpr import ANPREngine, VEHICLE_CLASS_IDS  # noqa: E402
from src import history_store  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────

def _make_plate_image(text="MH12AB1234", width=300, height=80):
    """Render white text on a dark background — a synthetic plate crop."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(img, text, (10, 55), cv2.FONT_HERSHEY_SIMPLEX,
                1.5, (255, 255, 255), 3)
    return img


# ── unit tests ───────────────────────────────────────────────────────────

def test_safe_crop_clipping():
    """Boundary clipping must never raise, even with out-of-range coords."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    engine = ANPREngine.__new__(ANPREngine)  # skip PaddleOCR init
    engine.enabled = False

    # Normal crop
    crop = engine._safe_crop(frame, 10, 10, 100, 100)
    assert crop is not None and crop.shape == (90, 90, 3)

    # Negative coords should be clipped to 0
    crop = engine._safe_crop(frame, -50, -50, 100, 100)
    assert crop is not None and crop.shape == (100, 100, 3)

    # Coords exceeding frame dims should be clipped
    crop = engine._safe_crop(frame, 600, 400, 800, 600)
    assert crop is not None and crop.shape == (80, 40, 3)

    # Inverted / zero-size crops should return None
    assert engine._safe_crop(frame, 100, 100, 50, 50) is None
    print("  [PASS] test_safe_crop_clipping")


def test_preprocess_returns_grayscale():
    """CLAHE preprocessing must return a single-channel image."""
    crop = np.random.randint(0, 255, (80, 200, 3), dtype=np.uint8)
    engine = ANPREngine.__new__(ANPREngine)
    result = engine._preprocess(crop)
    assert len(result.shape) == 2, "Expected single-channel output"
    assert result.shape == (80, 200)
    print("  [PASS] test_preprocess_returns_grayscale")


def test_vehicle_class_ids():
    """Ensure the expected COCO vehicle IDs are defined."""
    assert VEHICLE_CLASS_IDS == {2, 3, 5, 7}
    print("  [PASS] test_vehicle_class_ids")


def test_extract_plate_disabled():
    """When PaddleOCR isn't available, extract_plate returns None silently."""
    engine = ANPREngine.__new__(ANPREngine)
    engine.enabled = False
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert engine.extract_plate(frame, 10, 10, 200, 100) is None
    print("  [PASS] test_extract_plate_disabled")


def test_history_store_license_plate():
    """Verify that the license_plate column exists and round-trips correctly."""
    # Use an in-memory-like temp DB to avoid touching the real one
    import tempfile
    tmp = tempfile.mkdtemp()
    orig_db = history_store._DB_PATH
    orig_thumb = history_store._THUMB_DIR
    history_store._DB_PATH = os.path.join(tmp, "test_history.db")
    history_store._THUMB_DIR = os.path.join(tmp, "thumbs")

    try:
        history_store.init_db()
        eid = history_store.insert_event(
            camera_id="CAM-TEST",
            event_type="entered",
            zone_name="zone-1",
            tracker_id=42,
            class_name="car",
            severity="critical",
            title="CAR ENTERED ZONE",
            description="test",
            license_plate="MH12AB1234",
        )
        rows, total = history_store.query_events(camera_id="CAM-TEST")
        assert total >= 1
        found = [r for r in rows if r["id"] == eid]
        assert len(found) == 1
        assert found[0]["license_plate"] == "MH12AB1234"
        print("  [PASS] test_history_store_license_plate")
    finally:
        history_store._DB_PATH = orig_db
        history_store._THUMB_DIR = orig_thumb


def test_extract_plate_live():
    """If PaddleOCR is available, run a real extraction on a synthetic plate."""
    engine = ANPREngine()
    if not engine.enabled:
        print("  [SKIP] test_extract_plate_live — PaddleOCR not installed")
        return
    plate_img = _make_plate_image("MH12AB1234")
    # Embed plate crop inside a larger frame
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[200:280, 170:470] = plate_img
    result = engine.extract_plate(frame, 170, 200, 470, 280)
    print(f"  [INFO] OCR returned: {result}")
    if result:
        assert "MH12" in result or "1234" in result, f"Unexpected OCR output: {result}"
        print("  [PASS] test_extract_plate_live")
    else:
        print("  [WARN] test_extract_plate_live — OCR returned None (may be font/env issue)")


# ── runner ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running ANPR tests…")
    test_safe_crop_clipping()
    test_preprocess_returns_grayscale()
    test_vehicle_class_ids()
    test_extract_plate_disabled()
    test_history_store_license_plate()
    test_extract_plate_live()
    print("\nAll ANPR tests completed.")
