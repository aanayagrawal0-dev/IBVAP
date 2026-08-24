"""
Virtual fence / zone intrusion, threat behavior, and behavioral analysis layer.

Combines polygon zone geometry with skeletal pose analysis (climbing/crawling),
loitering duration tracking, and approach velocity monitoring.
"""

import math
import time

# Vehicle COCO class IDs — skeletal pose checks are meaningless for these.
VEHICLE_CLASS_IDS = {2, 3, 5, 7}  # car, motorcycle, bus, truck
from dataclasses import dataclass, field
from enum import Enum


class ZoneEventType(Enum):
    ENTERED = "entered"
    EXITED = "exited"
    CLIMBING = "climbing"
    CRAWLING = "crawling"
    LOITERING = "loitering"
    APPROACH_VELOCITY_HIGH = "approach_velocity_high"


@dataclass
class ZoneEvent:
    zone_name: str
    tracker_id: int
    event_type: ZoneEventType
    frame_idx: int


def check_threat_behavior(keypoints_xy, keypoints_conf):
    """
    Evaluates 17 body keypoints for climbing and crawling behaviors.
    Returns: ZoneEventType.CLIMBING, ZoneEventType.CRAWLING, or None.

    COCO Keypoints Index Map:
    0: nose, 1: l_eye, 2: r_eye, 3: l_ear, 4: r_ear
    5: l_shoulder, 6: r_shoulder, 7: l_elbow, 8: r_elbow
    9: l_wrist, 10: r_wrist, 11: l_hip, 12: r_hip
    13: l_knee, 14: r_knee, 15: l_ankle, 16: r_ankle
    """
    if keypoints_xy is None or keypoints_conf is None:
        return None

    # Safety check: Vehicles or un-extracted keypoints have all zero confidence
    if not any(c > 0.5 for c in keypoints_conf):
        return None

    # 1. Inverted Y-Axis Climbing Check:
    # In image space, Y=0 is top of frame. Wrists higher than head => wrist_y < head_y.
    # Evaluates only high-confidence keypoints (conf > 0.5).
    head_ys = [keypoints_xy[i, 1] for i in range(5) if keypoints_conf[i] > 0.5]
    left_wrist_valid = keypoints_conf[9] > 0.5
    right_wrist_valid = keypoints_conf[10] > 0.5

    if head_ys and (left_wrist_valid or right_wrist_valid):
        min_head_y = min(head_ys)
        left_climbing = left_wrist_valid and (keypoints_xy[9, 1] < min_head_y)
        right_climbing = right_wrist_valid and (keypoints_xy[10, 1] < min_head_y)
        if left_climbing or right_climbing:
            return ZoneEventType.CLIMBING

    # 2. Crawling / Crouching Check:
    # Head vertical height close to knees or torso angle nearly horizontal.
    knee_ys = [keypoints_xy[i, 1] for i in (13, 14) if keypoints_conf[i] > 0.5]
    shoulder_ys = [keypoints_xy[i, 1] for i in (5, 6) if keypoints_conf[i] > 0.5]
    hip_ys = [keypoints_xy[i, 1] for i in (11, 12) if keypoints_conf[i] > 0.5]

    if head_ys and knee_ys:
        avg_head_y = sum(head_ys) / len(head_ys)
        avg_knee_y = sum(knee_ys) / len(knee_ys)
        vert_head_knee = avg_knee_y - avg_head_y

        if shoulder_ys and hip_ys:
            avg_shoulder_y = sum(shoulder_ys) / len(shoulder_ys)
            avg_hip_y = sum(hip_ys) / len(hip_ys)
            torso_height = max(abs(avg_hip_y - avg_shoulder_y), 1.0)
            if vert_head_knee < torso_height * 0.9 or (avg_shoulder_y >= avg_hip_y - torso_height * 0.3):
                return ZoneEventType.CRAWLING
        elif vert_head_knee < 40:
            return ZoneEventType.CRAWLING

    return None


def _polygon_centroid(polygon):
    """Returns the (cx, cy) centroid of a polygon."""
    n = len(polygon)
    if n == 0:
        return 0.0, 0.0
    sx = sum(p[0] for p in polygon)
    sy = sum(p[1] for p in polygon)
    return sx / n, sy / n


@dataclass
class Zone:
    """A restricted polygon (e.g. the virtual fence line/area)."""
    name: str
    polygon: list
    debounce_frames: int = 3
    loitering_threshold: float = 10.0  # seconds
    speed_threshold: float = 3.0  # percent-units per frame
    # tracker_id -> confirmed (debounced) inside state
    _inside_state: dict = field(default_factory=dict)
    # tracker_id -> (candidate_state, consecutive_count) pending confirmation
    _pending: dict = field(default_factory=dict)
    # tracker_id -> last_threat_event
    _threat_state: dict = field(default_factory=dict)
    # tracker_id -> entry_timestamp (wall-clock seconds)
    _entry_timestamps: dict = field(default_factory=dict)
    # tracker_id -> True if loitering alert already fired for this stay
    _loitering_fired: dict = field(default_factory=dict)
    # tracker_id -> (prev_cx, prev_cy) for velocity calculation
    _prev_centroids: dict = field(default_factory=dict)
    # tracker_id -> True if velocity alert already fired for this approach
    _velocity_fired: dict = field(default_factory=dict)

    def contains(self, x, y):
        return _point_in_polygon(x, y, self.polygon)

    def update(self, tracker_id, cx, cy, frame_idx, keypoints_xy=None, keypoints_conf=None, class_id=None):
        """Call once per tracked object per frame. Returns a ZoneEvent if a
        crossing, threat behavior, loitering, or high-velocity approach occurred."""
        raw_inside = self.contains(cx, cy)
        confirmed_inside = self._inside_state.get(tracker_id, False)

        candidate_state, streak = self._pending.get(tracker_id, (raw_inside, 0))
        if raw_inside == candidate_state:
            streak += 1
        else:
            candidate_state, streak = raw_inside, 1
        self._pending[tracker_id] = (candidate_state, streak)

        event = None
        if streak >= self.debounce_frames and candidate_state != confirmed_inside:
            event_type = ZoneEventType.ENTERED if candidate_state else ZoneEventType.EXITED
            event = ZoneEvent(self.name, tracker_id, event_type, frame_idx)
            self._inside_state[tracker_id] = candidate_state
            if candidate_state:
                self._entry_timestamps[tracker_id] = time.time()
                self._loitering_fired[tracker_id] = False
            else:
                self._entry_timestamps.pop(tracker_id, None)
                self._loitering_fired.pop(tracker_id, None)
                self._velocity_fired.pop(tracker_id, None)

        # --- Loitering Detection ---
        if raw_inside and tracker_id in self._entry_timestamps:
            elapsed = time.time() - self._entry_timestamps[tracker_id]
            if elapsed >= self.loitering_threshold and not self._loitering_fired.get(tracker_id, False):
                self._loitering_fired[tracker_id] = True
                if event is None:
                    event = ZoneEvent(self.name, tracker_id, ZoneEventType.LOITERING, frame_idx)

        # --- Approach Velocity Detection ---
        prev = self._prev_centroids.get(tracker_id)
        self._prev_centroids[tracker_id] = (cx, cy)
        if prev is not None and not raw_inside:
            dx, dy = cx - prev[0], cy - prev[1]
            speed = math.sqrt(dx * dx + dy * dy)
            if speed > self.speed_threshold:
                zone_cx, zone_cy = _polygon_centroid(self.polygon)
                dist_before = math.sqrt((prev[0] - zone_cx) ** 2 + (prev[1] - zone_cy) ** 2)
                dist_after = math.sqrt((cx - zone_cx) ** 2 + (cy - zone_cy) ** 2)
                approaching = dist_after < dist_before
                if approaching and not self._velocity_fired.get(tracker_id, False):
                    self._velocity_fired[tracker_id] = True
                    if event is None:
                        event = ZoneEvent(self.name, tracker_id, ZoneEventType.APPROACH_VELOCITY_HIGH, frame_idx)
            else:
                self._velocity_fired[tracker_id] = False

        # --- Pose Threat Behaviors (climbing/crawling) ---
        # Skip skeletal analysis entirely for vehicle targets.
        if (raw_inside or candidate_state) and (class_id is None or class_id not in VEHICLE_CLASS_IDS):
            threat = check_threat_behavior(keypoints_xy, keypoints_conf)
            if threat is not None:
                last_threat = self._threat_state.get(tracker_id)
                if last_threat != threat:
                    self._threat_state[tracker_id] = threat
                    if event is None:
                        event = ZoneEvent(self.name, tracker_id, threat, frame_idx)
            else:
                self._threat_state.pop(tracker_id, None)

        return event


def _point_in_polygon(x, y, polygon):
    """Standard ray-casting point-in-polygon test."""
    n = len(polygon)
    inside = False
    px, py = polygon[-1]
    for qx, qy in polygon:
        if ((qy > y) != (py > y)) and (
            x < (px - qx) * (y - qy) / (py - qy + 1e-12) + qx
        ):
            inside = not inside
        px, py = qx, qy
    return inside


class ZoneManager:
    def __init__(self, zones=None):
        self.zones = zones or []

    def add_zone(self, zone: Zone):
        self.zones.append(zone)

    def replace_zones(self, zones: list):
        self.zones = list(zones)

    def update(self, tracker_id, cx, cy, frame_idx, keypoints_xy=None, keypoints_conf=None, class_id=None):
        """Runs all zones for one tracked centroid and keypoints; returns list of ZoneEvents."""
        events = []
        for zone in self.zones:
            evt = zone.update(tracker_id, cx, cy, frame_idx, keypoints_xy, keypoints_conf, class_id=class_id)
            if evt:
                events.append(evt)
        return events


