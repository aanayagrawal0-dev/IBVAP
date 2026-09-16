"""
Behavioral analytics (Phase 6.3) — retargets the existing pose-keypoint +
trajectory pipeline for law-enforcement scenarios, no new model:

  * loitering   — a person dwells with almost no movement for a while.
  * altercation — two people's boxes overlap while limbs (wrists) move fast:
                  a scuffle.
  * snatching   — a person's speed spikes right after being very close to
                  another: a grab-and-run.

All thresholds are in frame-diagonal-normalized units so they're resolution
independent. Each detector is debounced per subject so one event doesn't spam.
Inputs are exactly what the pipeline already computes each frame (tracked
boxes + COCO-17 keypoints); the analyzer keeps the small bit of temporal state
the rules need.
"""

from collections import deque

# COCO-17 keypoint indices.
L_WRIST, R_WRIST = 9, 10
_KP_CONF = 0.3


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter + 1e-9)


class BehaviorAnalyzer:
    def __init__(self, fps=15.0, frame_w=1920, frame_h=1080,
                 loiter_seconds=8.0, loiter_move=0.05,
                 altercation_wrist_speed=0.9, altercation_min_frames=3,
                 snatch_speed=1.1, snatch_prox=0.10, snatch_window_s=2.0,
                 cooldown_s=10.0):
        self.fps = max(fps, 1.0)
        self.diag = (frame_w ** 2 + frame_h ** 2) ** 0.5
        self.loiter_frames = int(loiter_seconds * self.fps)
        self.loiter_move = loiter_move
        self.altercation_wrist_speed = altercation_wrist_speed  # diag/second
        self.altercation_min_frames = altercation_min_frames
        self.snatch_speed = snatch_speed                        # diag/second
        self.snatch_prox = snatch_prox
        self.snatch_window = int(snatch_window_s * self.fps)
        self.cooldown = int(cooldown_s * self.fps)

        # tid -> state
        self._tracks: dict[int, dict] = {}
        # (a,b) -> frame_idx last seen close
        self._recent_prox: dict[tuple, int] = {}
        # (behavior, key) -> last emit frame
        self._cooldowns: dict[tuple, int] = {}

    def _norm(self, dx, dy):
        return (dx * dx + dy * dy) ** 0.5 / self.diag

    def _cooled(self, behavior, key, idx) -> bool:
        last = self._cooldowns.get((behavior, key))
        if last is not None and (idx - last) < self.cooldown:
            return False
        self._cooldowns[(behavior, key)] = idx
        return True

    def update(self, frame_idx, persons) -> list[dict]:
        """persons: list of {tid, box:(x1,y1,x2,y2), cx, cy, kpts_xy, kpts_conf}."""
        events = []
        present = set()
        for p in persons:
            tid = p["tid"]
            present.add(tid)
            st = self._tracks.setdefault(tid, {
                "first": frame_idx, "pos": deque(maxlen=self.loiter_frames + 2),
                "prev_wrist": None, "prev_c": (p["cx"], p["cy"]),
                "wrist_fast_run": 0, "loiter_emitted": False,
            })
            st["pos"].append((frame_idx, p["cx"], p["cy"]))

            # ── speed (box center) ──
            pcx, pcy = st["prev_c"]
            speed = self._norm(p["cx"] - pcx, p["cy"] - pcy) * self.fps  # diag/second
            st["prev_c"] = (p["cx"], p["cy"])
            p["_speed"] = speed

            # ── wrist speed (for altercation) ──
            wrist = _wrist_point(p)
            if wrist and st["prev_wrist"]:
                wspeed = self._norm(wrist[0] - st["prev_wrist"][0], wrist[1] - st["prev_wrist"][1]) * self.fps
                st["wrist_fast_run"] = st["wrist_fast_run"] + 1 if wspeed > self.altercation_wrist_speed else 0
            st["prev_wrist"] = wrist

            # ── loitering ──
            if not st["loiter_emitted"] and (frame_idx - st["first"]) >= self.loiter_frames:
                xs = [c[1] for c in st["pos"]]
                ys = [c[2] for c in st["pos"]]
                spread = self._norm(max(xs) - min(xs), max(ys) - min(ys))
                if spread < self.loiter_move and self._cooled("loitering", tid, frame_idx):
                    st["loiter_emitted"] = True
                    events.append(_event("loitering", "warning", "LOITERING DETECTED",
                                         f"Object #{tid} has dwelled with little movement for "
                                         f"{(frame_idx - st['first']) / self.fps:.0f}s.", [tid]))

        # ── pairwise: altercation + snatching ──
        plist = [p for p in persons if p.get("tid") is not None]
        for i in range(len(plist)):
            for j in range(i + 1, len(plist)):
                a, b = plist[i], plist[j]
                key = tuple(sorted((a["tid"], b["tid"])))
                dist = self._norm(a["cx"] - b["cx"], a["cy"] - b["cy"])
                overlap = _iou(a["box"], b["box"])

                # altercation: overlap + sustained fast wrists on either.
                fast = (self._tracks[a["tid"]]["wrist_fast_run"] >= self.altercation_min_frames or
                        self._tracks[b["tid"]]["wrist_fast_run"] >= self.altercation_min_frames)
                if overlap > 0.15 and fast and self._cooled("altercation", key, frame_idx):
                    events.append(_event("altercation", "critical", "POSSIBLE ALTERCATION",
                                         f"Objects #{a['tid']} and #{b['tid']} overlapping with rapid "
                                         f"limb motion — possible physical altercation.", [a["tid"], b["tid"]]))

                # snatching: were close recently, now one accelerates away.
                if dist < self.snatch_prox:
                    self._recent_prox[key] = frame_idx
                last_close = self._recent_prox.get(key)
                if last_close is not None and 0 < (frame_idx - last_close) <= self.snatch_window:
                    fastest = max(a.get("_speed", 0.0), b.get("_speed", 0.0))
                    if fastest > self.snatch_speed and self._cooled("snatching", key, frame_idx):
                        events.append(_event("snatching", "critical", "POSSIBLE SNATCHING",
                                             f"Rapid departure right after close contact between #{a['tid']} "
                                             f"and #{b['tid']} — possible snatch-and-run.", [a["tid"], b["tid"]]))

        # prune tracks that vanished
        for tid in list(self._tracks):
            if tid not in present:
                self._tracks.pop(tid, None)
        return events


def _wrist_point(p):
    kx, kc = p.get("kpts_xy"), p.get("kpts_conf")
    if kx is None or kc is None:
        return None
    best = None
    for idx in (R_WRIST, L_WRIST):
        try:
            if kc[idx] > _KP_CONF:
                best = (float(kx[idx][0]), float(kx[idx][1]))
                break
        except (IndexError, TypeError):
            continue
    return best


def _event(behavior, severity, title, description, tids) -> dict:
    return {
        "type": "behavior",
        "behavior": behavior,
        "severity": severity,
        "title": title,
        "description": description,
        "tracker_id": tids[0],
        "tracker_ids": tids,
    }
