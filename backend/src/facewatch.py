"""
Face recognition against a watchlist (Phase 6.4) — OPTIONAL, higher-risk, and
deliberately ALERT-AND-VERIFY, never alert-and-act.

A pluggable face embedder + a small face watchlist (name -> embedding). On a
match above a tuned threshold we surface an alert carrying a confidence score
and a thumbnail for a HUMAN operator to confirm — the system never takes
automated action on a face match, and every alert is logged. Disabled unless
an embedder is supplied (e.g. face_recognition / insightface), so no face
pipeline runs by default. Tests inject `embed_fn` and entries directly.
"""

import numpy as np


def _cosine(a, b) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def _safe_crop(frame, box):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return frame[y1:y2, x1:x2]


class FaceWatch:
    """embed_fn(crop_bgr) -> 1-D embedding or None. threshold is tuned high on
    purpose: a face match only prompts human verification, it never acts."""

    def __init__(self, embed_fn=None, threshold=0.62):
        self.threshold = threshold
        self._embed = embed_fn
        self.enabled = embed_fn is not None
        self._entries: list[dict] = []  # {name, embedding, reason}

    def add_face(self, name: str, embedding, reason: str | None = None):
        self._entries.append({"name": name, "embedding": np.asarray(embedding, dtype=np.float32),
                              "reason": reason})

    def check(self, frame, person_boxes) -> list[dict]:
        """Surface a verification alert per matched person. Empty when disabled
        or the watchlist is empty."""
        if not self.enabled or not self._entries:
            return []
        events = []
        for tid, box in person_boxes:
            crop = _safe_crop(frame, box)
            if crop is None:
                continue
            emb = self._embed(crop)
            if emb is None:
                continue
            emb = np.asarray(emb, dtype=np.float32)
            best = max(
                ((e, _cosine(emb, e["embedding"])) for e in self._entries
                 if e["embedding"].shape == emb.shape),
                key=lambda t: t[1], default=(None, -1.0),
            )
            entry, sim = best
            if entry is not None and sim >= self.threshold:
                events.append({
                    "type": "face_match",
                    "severity": "warning",  # verify, don't act
                    "title": f"FACE MATCH (VERIFY) — {entry['name']}",
                    "description": (f"Possible match to watchlisted person '{entry['name']}'"
                                    f"{f' ({entry['reason']})' if entry.get('reason') else ''} on object #{tid}"
                                    f" — confidence {sim:.2f}. Requires human verification."),
                    "tracker_id": tid,
                    "confidence": round(float(sim), 3),
                    "name": entry["name"],
                    "requires_verification": True,
                })
        return events
