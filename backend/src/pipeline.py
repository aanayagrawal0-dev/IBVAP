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
        # zone.polygon is stored as percentages (0-100) of frame width/
        # height — convert to this frame's actual pixel coordinates rather
        # than assuming any fixed resolution, so zones drawn against a
        # 960x540 clip still line up correctly on a 1280x720 webcam feed.
        h, w = frame.shape[:2]
        for zone in self.zone_manager.zones:
            pts = np.array([(int(x / 100.0 * w), int(y / 100.0 * h)) for x, y in zone.polygon])
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
            frame_h, frame_w = frame.shape[:2]

            labels = []
            for i in range(len(tracked)):
                cls_id = int(tracked.class_id[i])
                tid = int(tracked.tracker_id[i])
                conf = float(tracked.confidence[i])
                labels.append(f"#{tid} {self.detector.class_name(cls_id)} {conf:.2f}")

                x1, y1, x2, y2 = tracked.xyxy[i]
                cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                # Zones are stored as percentages — convert this frame's
                # actual pixel centroid into that same space rather than
                # assuming any fixed resolution.
                cx_pct, cy_pct = cx / frame_w * 100.0, cy / frame_h * 100.0
                for evt in self.zone_manager.update(tid, cx_pct, cy_pct, idx):
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

    def stream(self, on_frame, on_event=None, loop=True, target_fps=None, stop_flag=None):
        """
        Callback-driven variant of run(), for serving a live feed instead of
        writing one file. Calls on_frame(annotated_bgr_frame) every frame and
        on_event(zone_event, class_name, annotated_bgr_frame) whenever a zone
        crossing fires — the annotated frame (boxes + zone overlay already
        drawn) is handed over too so a caller can save it as a thumbnail
        showing exactly what triggered the alert, without redoing any of
        the drawing itself.

        loop=True replays a recorded file source indefinitely so a video file
        behaves like a continuous live camera for demo purposes — RTSP
        sources already behave this way via VideoSource's own reconnect
        logic, so looping is skipped for them.

        stop_flag: optional callable; stream exits when it returns True
        (used to cleanly shut down the background thread this normally runs
        in).
        """
        frame_interval = 1.0 / target_fps if target_fps else None

        while True:
            for idx, frame in self.source.frames():
                if stop_flag and stop_flag():
                    return
                t_start = time.time()

                detections = self.detector.detect(frame)
                tracked = self.tracker.update(detections, idx)
                frame_h, frame_w = frame.shape[:2]

                labels = []
                fired_this_frame = []  # (evt, class_name) — flushed once the
                # annotated frame exists below, so thumbnails show boxes/zones.
                for i in range(len(tracked)):
                    cls_id = int(tracked.class_id[i])
                    tid = int(tracked.tracker_id[i])
                    conf = float(tracked.confidence[i])
                    class_name = self.detector.class_name(cls_id)
                    labels.append(f"#{tid} {class_name} {conf:.2f}")

                    x1, y1, x2, y2 = tracked.xyxy[i]
                    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                    # Zones are stored as percentages — convert this frame's
                    # actual pixel centroid into that same space rather than
                    # assuming any fixed resolution (webcam frames rarely
                    # match a recorded clip's resolution).
                    cx_pct, cy_pct = cx / frame_w * 100.0, cy / frame_h * 100.0
                    for evt in self.zone_manager.update(tid, cx_pct, cy_pct, idx):
                        self.events.append(evt)
                        fired_this_frame.append((evt, class_name))

                annotated = frame.copy()
                annotated = self.box_annotator.annotate(annotated, tracked)
                annotated = self.label_annotator.annotate(annotated, tracked, labels=labels)
                annotated = self._draw_zones(annotated)
                on_frame(annotated)

                if on_event:
                    for evt, class_name in fired_this_frame:
                        on_event(evt, class_name, annotated)

                if frame_interval:
                    elapsed = time.time() - t_start
                    if elapsed < frame_interval:
                        time.sleep(frame_interval - elapsed)

            if not loop or self.source.is_stream:
                break
            self.source._open()  # reopen the file to replay it
