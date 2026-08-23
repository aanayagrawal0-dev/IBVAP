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

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_data")
INPUT_VIDEO = os.path.join(SAMPLE_DIR, "synthetic_test_clip.mp4")
OUTPUT_VIDEO = os.path.join(SAMPLE_DIR, "annotated_output.mp4")

# A "virtual fence" zone covering the right half of the frame — the
# panning motion in the synthetic clip guarantees objects cross into it,
# so this proves entered/exited events actually fire.
OUT_W, OUT_H = 960, 540
fence_zone = Zone(
    name="restricted-zone",
    polygon=[(OUT_W * 0.6, 0), (OUT_W, 0), (OUT_W, OUT_H), (OUT_W * 0.6, OUT_H)],
)

zone_manager = ZoneManager(zones=[fence_zone])

pipeline = Pipeline(
    source_uri=INPUT_VIDEO,
    zone_manager=zone_manager,
    weights="yolov8n.pt",
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
