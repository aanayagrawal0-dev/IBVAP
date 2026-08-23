"""
Live Webcam Runner for IBVAP
Runs real-time YOLOv8 object detection, ByteTrack tracking, and Virtual Fence logic
from your local webcam feed.

Usage:
    python3 run_webcam.py [--cam 0] [--conf 0.35]
"""

import sys
import os
import argparse
import cv2

sys.path.insert(0, os.path.dirname(__file__))

from src.pipeline import Pipeline
from src.zones import Zone, ZoneManager
from src.ingestion import VideoSource

def main():
    parser = argparse.ArgumentParser(description="Run IBVAP live on webcam")
    parser.add_argument("--cam", type=int, default=0, help="Webcam device index (default: 0)")
    parser.add_argument("--conf", type=float, default=0.35, help="Detection confidence threshold (default: 0.35)")
    args = parser.parse_args()

    print(f"\n==========================================")
    print(f"  Starting IBVAP Live Webcam Pipeline")
    print(f"  Webcam Device Index: {args.cam}")
    print(f"  Confidence Threshold: {args.conf}")
    print(f"==========================================\n")

    # Probe webcam resolution to construct relative virtual fence zone
    cap = cv2.VideoCapture(args.cam)
    if not cap.isOpened():
        print(f"Error: Could not open webcam index {args.cam}.")
        print("Please check camera permissions or try --cam 1")
        sys.exit(1)

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
    cap.release()

    # Define a virtual fence polygon (right 40% of camera field of view)
    fence_zone = Zone(
        name="restricted-zone",
        polygon=[(w * 0.6, 0), (w, 0), (w, h), (w * 0.6, h)],
        debounce_frames=3,
    )
    zone_manager = ZoneManager(zones=[fence_zone])

    pipeline = Pipeline(
        source_uri=args.cam,
        zone_manager=zone_manager,
        weights="yolov8n.pt",
        conf_threshold=args.conf,
        source_name=f"webcam-{args.cam}",
    )

    print("Launching live detection window. Press 'q' in the window to stop...")
    try:
        stats = pipeline.run(show_window=True, print_every=30)
        print("\n--- WEBCAM SESSION SUMMARY ---")
        print(f"Frames processed:  {stats['frame_count']}")
        print(f"Total detections:  {stats['detection_count']}")
        print(f"Zone events fired: {len(stats['zone_events'])}")
    except Exception as e:
        print(f"\nPipeline runtime error: {e}")

if __name__ == "__main__":
    main()
