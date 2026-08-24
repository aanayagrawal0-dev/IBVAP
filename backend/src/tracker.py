"""
Tracking layer — assigns persistent IDs to detections across frames using
ByteTrack, and performs cross-camera Re-Identification (Re-ID) to assign
global IDs across disjoint camera feeds.

Performance Rule:
  Re-ID embedding extraction runs ONLY when a tracker_id first appears, or
  every REID_INTERVAL frames.  Between extractions the cached global_id is
  reused so the tracking loop stays at 30+ FPS.
"""

import logging
import numpy as np
import supervision as sv

from .reid import TargetEmbedder, GlobalTargetRegistry

logger = logging.getLogger(__name__)

# How often (in frames) to re-compute the Re-ID embedding for each tracked ID.
REID_INTERVAL = 20


class Tracker:
    def __init__(self, frame_rate=25, enable_reid=True, camera_id="cam0"):
        self.tracker = sv.ByteTrack(frame_rate=frame_rate)
        # tracker_id -> list of (frame_idx, cx, cy) centroid history
        self.history = {}
        self.max_history = 90  # ~3-4s of trajectory at typical frame rates

        # ── Re-ID state ──
        self.enable_reid = enable_reid
        self.camera_id = camera_id
        self._embedder: TargetEmbedder | None = None
        self._registry: GlobalTargetRegistry | None = None
        # tracker_id -> cached global_id
        self._global_id_cache: dict[int, int] = {}
        # tracker_id -> frame_idx when embedding was last computed
        self._last_reid_frame: dict[int, int] = {}

        if enable_reid:
            self._embedder = TargetEmbedder(use_gpu=True)
            self._registry = GlobalTargetRegistry(similarity_threshold=0.70)
            logger.info("Re-ID enabled for camera %s", camera_id)

    @classmethod
    def with_shared_registry(cls, registry: GlobalTargetRegistry,
                             frame_rate=25, camera_id="cam0"):
        """Create a Tracker that shares a GlobalTargetRegistry with other cameras."""
        obj = cls(frame_rate=frame_rate, enable_reid=True, camera_id=camera_id)
        obj._registry = registry
        return obj

    def update(self, detections, frame_idx, frame_bgr=None):
        """Feed one frame's detections in, get back the same detections
        annotated with persistent tracker_id, update centroid history, and
        optionally compute Re-ID global_id.

        Args:
            detections: sv.Detections from the detector.
            frame_idx:  Current frame index.
            frame_bgr:  Raw BGR frame (needed for Re-ID crop). Pass None to
                        skip Re-ID even if enabled.
        """
        tracked = self.tracker.update_with_detections(detections)

        global_ids = np.full(len(tracked), -1, dtype=np.int64)

        for i in range(len(tracked)):
            tid = int(tracked.tracker_id[i])
            x1, y1, x2, y2 = tracked.xyxy[i].astype(int)
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0

            # ── centroid history ──
            hist = self.history.setdefault(tid, [])
            hist.append((frame_idx, cx, cy))
            if len(hist) > self.max_history:
                hist.pop(0)

            # ── Re-ID (sparse) ──
            if self.enable_reid and self._embedder and self._registry and frame_bgr is not None:
                last_frame = self._last_reid_frame.get(tid, -REID_INTERVAL - 1)
                is_new = tid not in self._global_id_cache
                is_due = (frame_idx - last_frame) >= REID_INTERVAL

                if is_new or is_due:
                    # Crop and embed
                    h, w = frame_bgr.shape[:2]
                    cx1 = max(0, x1)
                    cy1 = max(0, y1)
                    cx2 = min(w, x2)
                    cy2 = min(h, y2)
                    if (cx2 - cx1) > 10 and (cy2 - cy1) > 10:
                        crop = frame_bgr[cy1:cy2, cx1:cx2]
                        vec = self._embedder.embed(crop)
                        gid = self._registry.query(vec, camera_id=self.camera_id)
                        self._global_id_cache[tid] = gid
                        self._last_reid_frame[tid] = frame_idx

                global_ids[i] = self._global_id_cache.get(tid, -1)

        # Attach global_ids to detections data dict
        if self.enable_reid:
            if tracked.data is None:
                tracked.data = {}
            tracked.data["global_id"] = global_ids

        return tracked

    def trajectory(self, tracker_id):
        return self.history.get(int(tracker_id), [])

    def velocity(self, tracker_id):
        """Rough (vx, vy) in px/frame over the tracked object's recent history."""
        hist = self.trajectory(tracker_id)
        if len(hist) < 2:
            return 0.0, 0.0
        (f0, x0, y0), (f1, x1, y1) = hist[0], hist[-1]
        dframes = max(f1 - f0, 1)
        return (x1 - x0) / dframes, (y1 - y0) / dframes

    def global_id(self, tracker_id):
        """Return the cached global_id for a local tracker_id, or -1."""
        return self._global_id_cache.get(int(tracker_id), -1)

