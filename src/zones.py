"""
Virtual fence / zone intrusion layer.

Deliberately NOT ML — this is geometry on top of tracked centroids. Cheap,
explainable, and reliable, which is exactly what you want for a security
alert: a judge or an operator can look at a polygon on a map and understand
exactly why an alert fired.
"""

from dataclasses import dataclass, field
from enum import Enum


class ZoneEventType(Enum):
    ENTERED = "entered"
    EXITED = "exited"


@dataclass
class ZoneEvent:
    zone_name: str
    tracker_id: int
    event_type: ZoneEventType
    frame_idx: int


@dataclass
class Zone:
    """A restricted polygon (e.g. the virtual fence line/area)."""
    name: str
    polygon: list  # list of (x, y) points, image coordinates
    # A centroid sitting right on the polygon edge jitters in/out frame to
    # frame (tracker box noise, panning motion, etc.) — without debouncing
    # this produces an entered/exited flood instead of one clean crossing.
    # Require `debounce_frames` consecutive frames of the new state before
    # it's accepted as a real crossing.
    debounce_frames: int = 3
    # tracker_id -> confirmed (debounced) inside state
    _inside_state: dict = field(default_factory=dict)
    # tracker_id -> (candidate_state, consecutive_count) pending confirmation
    _pending: dict = field(default_factory=dict)

    def contains(self, x, y):
        return _point_in_polygon(x, y, self.polygon)

    def update(self, tracker_id, cx, cy, frame_idx):
        """Call once per tracked object per frame. Returns a ZoneEvent if a
        *debounced* crossing just happened, else None."""
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

    def update(self, tracker_id, cx, cy, frame_idx):
        """Runs all zones for one tracked centroid; returns list of ZoneEvents."""
        events = []
        for zone in self.zones:
            evt = zone.update(tracker_id, cx, cy, frame_idx)
            if evt:
                events.append(evt)
        return events
