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

from src.ingestion import open_source
from src.detector import Detector
from src.tracker import Tracker
from src.zones import ZoneManager
from src.zero_dce import ZeroDCEEnhancer
from src.anpr import ANPREngine, VEHICLE_CLASS_IDS
from src import watchlist


SKELETON_EDGES = [
    (0, 1), (0, 2), (1, 3), (2, 4),  # facial keypoints
    (5, 6),  # shoulders
    (5, 7), (7, 9),  # left arm
    (6, 8), (8, 10),  # right arm
    (5, 11), (6, 12), (11, 12),  # torso
    (11, 13), (13, 15),  # left leg
    (12, 14), (14, 16),  # right leg
]


class Pipeline:
    def __init__(self, source_uri, zone_manager: ZoneManager, weights="yolo11n-pose.pt",
                 conf_threshold=0.35, source_name="camera-1", night_vision=False):
        # open_source dispatches on the spec (webcam / RTSP / file / ONVIF /
        # HTTP-MJPEG) and returns an adapter satisfying the same frame
        # contract, so everything below is unchanged regardless of source.
        self.source = open_source(source_uri, name=source_name)
        self.detector = Detector(weights=weights, conf_threshold=conf_threshold)
        self.tracker = Tracker(frame_rate=int(self.source.fps))
        self.zone_manager = zone_manager
        self.enhancer = ZeroDCEEnhancer()
        self.night_vision = night_vision
        self.anpr_engine = ANPREngine()

        self.box_annotator = sv.BoxAnnotator(thickness=2)
        self.label_annotator = sv.LabelAnnotator(text_thickness=1, text_scale=0.5)

        self.events = []  # collected ZoneEvent log

        # ANPR/watchlist state. OCR is expensive, so we read each vehicle
        # tracker's plate at most once every `anpr_interval` frames and cache
        # the result — reused for zone-event tagging AND checked against the
        # watchlist on every fresh read. `_alerted_watchlist` de-dupes so one
        # watchlisted vehicle doesn't fire an alert on every subsequent frame.
        self.anpr_interval = 15
        self._plate_cache: dict[int, str] = {}
        self._plate_read_frame: dict[int, int] = {}
        self._alerted_watchlist: set[tuple[int, str]] = set()

    def _draw_zones(self, frame):
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

    def _draw_skeletons(self, frame, kpts_xy_all, kpts_conf_all, min_conf=0.5):
        if kpts_xy_all is None or kpts_conf_all is None:
            return frame
        for person_idx in range(len(kpts_xy_all)):
            kpts_xy = kpts_xy_all[person_idx]
            kpts_conf = kpts_conf_all[person_idx]

            for p1_idx, p2_idx in SKELETON_EDGES:
                if kpts_conf[p1_idx] > min_conf and kpts_conf[p2_idx] > min_conf:
                    pt1 = (int(kpts_xy[p1_idx, 0]), int(kpts_xy[p1_idx, 1]))
                    pt2 = (int(kpts_xy[p2_idx, 0]), int(kpts_xy[p2_idx, 1]))
                    cv2.line(frame, pt1, pt2, (0, 255, 255), 2)

            for k_idx in range(len(kpts_xy)):
                if kpts_conf[k_idx] > min_conf:
                    pt = (int(kpts_xy[k_idx, 0]), int(kpts_xy[k_idx, 1]))
                    cv2.circle(frame, pt, 3, (0, 165, 255), -1)
        return frame

    def _maybe_read_plate(self, tid, idx, frame, x1, y1, x2, y2):
        """Throttled per-vehicle ANPR: attempt OCR at most once every
        `anpr_interval` frames for a given tracker, caching the last
        successful read so downstream (zone tagging, watchlist, route
        reconstruction) always has the freshest known plate without paying
        for OCR every frame. Returns the plate string or None."""
        last = self._plate_read_frame.get(tid)
        if last is not None and (idx - last) < self.anpr_interval:
            return self._plate_cache.get(tid)
        self._plate_read_frame[tid] = idx
        plate = self.anpr_engine.extract_plate(frame, x1, y1, x2, y2)
        if plate:
            self._plate_cache[tid] = plate
            return plate
        return self._plate_cache.get(tid)

    def run(self, output_path=None, max_frames=None, print_every=25, night_vision=False):
        writer = None
        frame_count = 0
        detection_count = 0
        t0 = time.time()

        for idx, frame in self.source.frames():
            if max_frames and idx >= max_frames:
                break

            if night_vision or self.night_vision:
                frame = self.enhancer.enhance(frame)

            detections = self.detector.detect(frame)
            tracked = self.tracker.update(detections, idx, frame_bgr=frame)
            detection_count += len(tracked)
            frame_h, frame_w = frame.shape[:2]

            kpts_xy_all = tracked.data.get("keypoints_xy") if hasattr(tracked, "data") else None
            kpts_conf_all = tracked.data.get("keypoints_conf") if hasattr(tracked, "data") else None

            labels = []
            for i in range(len(tracked)):
                cls_id = int(tracked.class_id[i])
                tid = int(tracked.tracker_id[i])
                conf = float(tracked.confidence[i])
                labels.append(f"#{tid} {self.detector.class_name(cls_id)} {conf:.2f}")

                x1, y1, x2, y2 = tracked.xyxy[i]
                cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                cx_pct, cy_pct = cx / frame_w * 100.0, cy / frame_h * 100.0

                kpts_xy = kpts_xy_all[i] if kpts_xy_all is not None and i < len(kpts_xy_all) else None
                kpts_conf = kpts_conf_all[i] if kpts_conf_all is not None and i < len(kpts_conf_all) else None

                for evt in self.zone_manager.update(tid, cx_pct, cy_pct, idx, kpts_xy, kpts_conf, class_id=cls_id):
                    # Run ANPR for vehicle targets on alert frames
                    if cls_id in VEHICLE_CLASS_IDS:
                        plate = self.anpr_engine.extract_plate(frame, x1, y1, x2, y2)
                        evt.license_plate = plate
                    else:
                        evt.license_plate = None
                    self.events.append(evt)
                    plate_info = f" plate={evt.license_plate}" if evt.license_plate else ""
                    print(f"[ZONE EVENT] frame={evt.frame_idx} zone={evt.zone_name} "
                          f"tracker_id={evt.tracker_id} type={evt.event_type.value}{plate_info}")

            annotated = frame.copy()
            annotated = self.box_annotator.annotate(annotated, tracked)
            annotated = self.label_annotator.annotate(annotated, tracked, labels=labels)
            annotated = self._draw_skeletons(annotated, kpts_xy_all, kpts_conf_all)
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

    def stream(self, on_frame, on_event=None, loop=True, target_fps=None, stop_flag=None,
               night_vision_flag=None, on_watchlist=None):
        frame_interval = 1.0 / target_fps if target_fps else None

        while True:
            for idx, frame in self.source.frames():
                if stop_flag and stop_flag():
                    return
                t_start = time.time()

                is_night_vision = night_vision_flag() if callable(night_vision_flag) else self.night_vision
                if is_night_vision:
                    frame = self.enhancer.enhance(frame)

                detections = self.detector.detect(frame)
                tracked = self.tracker.update(detections, idx, frame_bgr=frame)
                frame_h, frame_w = frame.shape[:2]

                kpts_xy_all = tracked.data.get("keypoints_xy") if hasattr(tracked, "data") else None
                kpts_conf_all = tracked.data.get("keypoints_conf") if hasattr(tracked, "data") else None

                labels = []
                fired_this_frame = []
                watchlist_hits = []
                for i in range(len(tracked)):
                    cls_id = int(tracked.class_id[i])
                    tid = int(tracked.tracker_id[i])
                    conf = float(tracked.confidence[i])
                    class_name = self.detector.class_name(cls_id)
                    labels.append(f"#{tid} {class_name} {conf:.2f}")

                    x1, y1, x2, y2 = tracked.xyxy[i]
                    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                    cx_pct, cy_pct = cx / frame_w * 100.0, cy / frame_h * 100.0

                    # Continuous, throttled ANPR on vehicles — independent of
                    # zone events, so a watchlisted plate is caught simply by
                    # being seen. Each fresh read is cross-referenced against
                    # the watchlist immediately; the per-(tracker, plate)
                    # de-dupe keeps one vehicle from re-alerting every frame.
                    plate = None
                    if cls_id in VEHICLE_CLASS_IDS:
                        plate = self._maybe_read_plate(tid, idx, frame, x1, y1, x2, y2)
                        if plate:
                            entry = watchlist.check_plate(plate)
                            if entry and (tid, plate) not in self._alerted_watchlist:
                                self._alerted_watchlist.add((tid, plate))
                                watchlist_hits.append((plate, entry, tid, class_name))

                    kpts_xy = kpts_xy_all[i] if kpts_xy_all is not None and i < len(kpts_xy_all) else None
                    kpts_conf = kpts_conf_all[i] if kpts_conf_all is not None and i < len(kpts_conf_all) else None

                    for evt in self.zone_manager.update(tid, cx_pct, cy_pct, idx, kpts_xy, kpts_conf, class_id=cls_id):
                        # Tag the event with this vehicle's most recent plate
                        # read (already OCR'd above — don't re-run OCR here).
                        evt.license_plate = plate if cls_id in VEHICLE_CLASS_IDS else None
                        self.events.append(evt)
                        fired_this_frame.append((evt, class_name))

                annotated = frame.copy()
                annotated = self.box_annotator.annotate(annotated, tracked)
                annotated = self.label_annotator.annotate(annotated, tracked, labels=labels)
                annotated = self._draw_skeletons(annotated, kpts_xy_all, kpts_conf_all)
                annotated = self._draw_zones(annotated)
                on_frame(annotated)

                if on_event:
                    for evt, class_name in fired_this_frame:
                        on_event(evt, class_name, annotated)

                if on_watchlist:
                    for plate, entry, tid, cname in watchlist_hits:
                        on_watchlist(
                            {"plate": plate, "entry": entry, "tracker_id": tid, "class_name": cname},
                            annotated,
                        )

                if frame_interval:
                    elapsed = time.time() - t_start
                    if elapsed < frame_interval:
                        time.sleep(frame_interval - elapsed)

            if not loop or self.source.is_stream:
                break
            self.source._open()


