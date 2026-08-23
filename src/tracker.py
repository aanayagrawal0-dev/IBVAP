"""
Tracking layer — assigns persistent IDs to detections across frames using
ByteTrack. Persistent IDs are the foundation everything downstream needs:
dwell-time loitering, velocity-toward-fence, line-crossing counts, etc. all
depend on knowing "this is the same object as three frames ago," not just
"there is an object here."
"""

import supervision as sv


class Tracker:
    def __init__(self, frame_rate=25):
        self.tracker = sv.ByteTrack(frame_rate=frame_rate)
        # tracker_id -> list of (frame_idx, cx, cy) centroid history
        self.history = {}
        self.max_history = 90  # ~3-4s of trajectory at typical frame rates

    def update(self, detections, frame_idx):
        """Feed one frame's detections in, get back the same detections
        annotated with persistent tracker_id, and update centroid history."""
        tracked = self.tracker.update_with_detections(detections)

        for i in range(len(tracked)):
            tid = int(tracked.tracker_id[i])
            x1, y1, x2, y2 = tracked.xyxy[i]
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0

            hist = self.history.setdefault(tid, [])
            hist.append((frame_idx, cx, cy))
            if len(hist) > self.max_history:
                hist.pop(0)

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
