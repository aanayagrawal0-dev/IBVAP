"""
End-to-end smoke test: ingestion -> detection -> tracking -> virtual fence,
run on the synthetic panning clip (real photo content, synthetic motion).

Run from the ibvap/ directory:
    python3 tests/run_pipeline_smoke_test.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.pipeline import Pipeline
from src.zones import Zone, ZoneManager

BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
SAMPLE_DIR = os.path.join(BASE_DIR, "sample_data")
INPUT_VIDEO = os.path.join(SAMPLE_DIR, "synthetic_test_clip.mp4")
OUTPUT_VIDEO = os.path.join(SAMPLE_DIR, "annotated_output.mp4")
# Use the weights packaged in models/ so this runs offline once cloned —
# if you deleted that file, passing "yolov8n.pt" instead lets ultralytics
# auto-download it (needs internet, ~6MB).
WEIGHTS_PATH = os.path.join(BASE_DIR, "models", "yolov8n.pt")

# A "virtual fence" zone covering the right 40% of the frame — the panning
# motion in the synthetic clip guarantees objects cross into it, so this
# proves entered/exited events actually fire. Zone.polygon is percentages
# (0-100) of frame width/height, not pixels — resolution-independent, so
# this same zone is correct regardless of the source clip's actual size
# (see src/zones.py and src/pipeline.py for where the conversion happens).
fence_zone = Zone(
    name="restricted-zone",
    polygon=[(60, 0), (100, 0), (100, 100), (60, 100)],
)

zone_manager = ZoneManager(zones=[fence_zone])

pipeline = Pipeline(
    source_uri=INPUT_VIDEO,
    zone_manager=zone_manager,
    weights=WEIGHTS_PATH,
    conf_threshold=0.30,
    source_name="synthetic-test",
)

stats = pipeline.run(output_path=OUTPUT_VIDEO, print_every=25)

print("\n--- SMOKE TEST SUMMARY ---")
print(f"Frames processed:   {stats['frame_count']}")
print(f"Total detections:   {stats['detection_count']}")
print(f"Zone events fired:  {len(stats['zone_events'])}")
print(f"Output video:       {OUTPUT_VIDEO}")

assert stats["frame_count"] > 0, "No frames were processed — ingestion failed"
assert stats["detection_count"] > 0, "Zero detections — detector likely broken"
assert len(stats["zone_events"]) > 0, "No zone crossing events — zone logic likely broken"
print("\nAll smoke-test assertions passed.")
