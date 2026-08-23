"""
Pipeline runner — wires ingestion -> detection -> tracking -> zone logic
together, annotates frames, and logs zone-crossing events. This is the
smoke-test harness for the core detect+track+fence loop before the
alert engine, ANPR, and dashboard layers get bolted on.
"""

import time
import cv2
import numpy as np
import supervision as sv

from src.ingestion import VideoSource
from src.detector import Detector
from src.tracker import Tracker
from src.zones import ZoneManager


class Pipeline:
    def __init__(self, source_uri, zone_manager: ZoneManager, weights="yolov8n.pt",
                 conf_threshold=0.35, source_name="camera-1"):
        self.source = VideoSource(source_uri, name=source_name)
        self.detector = Detector(weights=weights, conf_threshold=conf_threshold)
        self.tracker = Tracker(frame_rate=int(self.source.fps))
        self.zone_manager = zone_manager

        self.box_annotator = sv.BoxAnnotator(thickness=2)
        self.label_annotator = sv.LabelAnnotator(text_thickness=1, text_scale=0.5)

        self.events = []  # collected ZoneEvent log

    def _draw_zones(self, frame):
        for zone in self.zone_manager.zones:
            pts = np.array([(int(x), int(y)) for x, y in zone.polygon])
            overlay = frame.copy()
            cv2.fillPoly(overlay, [pts], (0, 0, 255))
            cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
            cv2.polylines(frame, [pts], True, (0, 0, 255), 2)
            cv2.putText(frame, zone.name, tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 0, 255), 2)
        return frame

    def run(self, output_path=None, max_frames=None, print_every=25):
        writer = None
        frame_count = 0
        detection_count = 0
        t0 = time.time()

        for idx, frame in self.source.frames():
            if max_frames and idx >= max_frames:
                break

            detections = self.detector.detect(frame)
            tracked = self.tracker.update(detections, idx)
            detection_count += len(tracked)

            labels = []
            for i in range(len(tracked)):
                cls_id = int(tracked.class_id[i])
                tid = int(tracked.tracker_id[i])
                conf = float(tracked.confidence[i])
                labels.append(f"#{tid} {self.detector.class_name(cls_id)} {conf:.2f}")

                x1, y1, x2, y2 = tracked.xyxy[i]
                cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                for evt in self.zone_manager.update(tid, cx, cy, idx):
                    self.events.append(evt)
                    print(f"[ZONE EVENT] frame={evt.frame_idx} zone={evt.zone_name} "
                          f"tracker_id={evt.tracker_id} type={evt.event_type.value}")

            annotated = frame.copy()
            annotated = self.box_annotator.annotate(annotated, tracked)
            annotated = self.label_annotator.annotate(annotated, tracked, labels=labels)
            annotated = self._draw_zones(annotated)

            if output_path:
                if writer is None:
                    h, w = annotated.shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(output_path, fourcc, self.source.fps, (w, h))
                writer.write(annotated)

            frame_count += 1
            if frame_count % print_every == 0:
                elapsed = time.time() - t0
                print(f"frame {frame_count} | {frame_count/elapsed:.1f} fps | "
                      f"{len(tracked)} tracked objects this frame")

        if writer is not None:
            writer.release()
        self.source.release()

        elapsed = time.time() - t0
        print(f"\nDone. {frame_count} frames in {elapsed:.1f}s "
              f"({frame_count/max(elapsed,1e-6):.1f} fps), "
              f"{detection_count} total detections, {len(self.events)} zone events.")
        return {
            "frame_count": frame_count,
            "detection_count": detection_count,
            "zone_events": self.events,
            "elapsed_s": elapsed,
        }
