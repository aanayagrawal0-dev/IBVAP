"""
Phase 6 acceptance test — bonus analytics.

  6.1 tampering/health: covered lens, defocus, and scene-change are each
      detected against a learned baseline.
  6.3 behavior: loitering, altercation, and snatching fire from the pose /
      trajectory data.
  6.2 weapon / 6.4 face: the two-stage crop-classify + alert-and-verify
      stages are wired and disabled without a model, and fire with a stub
      classifier/embedder.
  + integration: an analytic event flows through the worker to history + the
    alert queue, and per-camera health is surfaced on /api/cameras.

Run:  python tests/test_phase6_analytics.py     (from backend/, venv active)
"""

import os
import sys
import tempfile
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient

from src.tamper import TamperDetector
from src.behavior import BehaviorAnalyzer
from src.weapon import WeaponDetector
from src.facewatch import FaceWatch


# ── helpers ─────────────────────────────────────────────────────────────────

def _textured(base, size=120, seed=0):
    """A sharp frame (high Laplacian variance) centred on intensity `base`."""
    rng = np.random.default_rng(seed)
    noise = rng.integers(-15, 16, (size, size, 3))
    checker = np.indices((size, size)).sum(axis=0) % 2  # 1px checkerboard -> lots of edges
    frame = np.clip(base + noise + (checker[..., None] * 40 - 20), 0, 255).astype(np.uint8)
    return frame


def _warm(det, base=110, n=13):
    for i in range(n):
        det.update(_textured(base, seed=i))


# ── 6.1 tampering ────────────────────────────────────────────────────────────

def test_tamper_covered():
    det = TamperDetector()
    _warm(det)
    st = det.update(np.zeros((120, 120, 3), dtype=np.uint8))  # blacked out
    assert st["issue"] == "covered" and st["transitioned"], st
    print("  [PASS] tampering: covered lens detected")


def test_tamper_defocus():
    det = TamperDetector()
    _warm(det)
    blurred = cv2.GaussianBlur(_textured(110, seed=99), (0, 0), sigmaX=9)
    st = det.update(blurred)
    assert st["issue"] == "defocus", st
    print("  [PASS] tampering: defocus detected")


def test_tamper_scene_change():
    det = TamperDetector()
    _warm(det, base=100)                       # reference: dark-ish, sharp
    st = det.update(_textured(215, seed=7))    # bright, sharp, very different histogram
    assert st["issue"] == "scene_change", st
    print("  [PASS] tampering: scene change / camera moved detected")


# ── 6.3 behavior ─────────────────────────────────────────────────────────────

def test_behavior_loitering():
    ba = BehaviorAnalyzer(fps=10, frame_w=100, frame_h=100, loiter_seconds=1.0, loiter_move=0.05)
    events = []
    for idx in range(14):
        # essentially stationary person
        events += ba.update(idx, [{"tid": 1, "box": (40, 40, 60, 80), "cx": 50.0, "cy": 60.0,
                                    "kpts_xy": None, "kpts_conf": None}])
    assert any(e["behavior"] == "loitering" for e in events), events
    print("  [PASS] behavior: loitering detected")


def test_behavior_altercation():
    ba = BehaviorAnalyzer(fps=10, frame_w=100, frame_h=100, altercation_min_frames=3)
    kc = np.ones(17)
    events = []
    for idx in range(6):
        wx = 50 + (idx % 2) * 25  # wrist jumps 25px each frame -> fast limb motion
        kA = np.zeros((17, 2)); kA[10] = (wx, 50)
        pA = {"tid": 1, "box": (40, 30, 70, 90), "cx": 55.0, "cy": 60.0, "kpts_xy": kA, "kpts_conf": kc}
        pB = {"tid": 2, "box": (45, 30, 75, 90), "cx": 60.0, "cy": 60.0,
              "kpts_xy": np.zeros((17, 2)), "kpts_conf": kc}  # overlapping box
        events += ba.update(idx, [pA, pB])
    assert any(e["behavior"] == "altercation" for e in events), events
    print("  [PASS] behavior: altercation detected")


def test_behavior_snatching():
    ba = BehaviorAnalyzer(fps=10, frame_w=100, frame_h=100, snatch_prox=0.10, snatch_speed=1.0)
    none = {"kpts_xy": None, "kpts_conf": None}
    # frame 0: A and B essentially co-located (close contact)
    ba.update(0, [{"tid": 1, "box": (48, 48, 58, 78), "cx": 50.0, "cy": 60.0, **none},
                  {"tid": 2, "box": (49, 48, 59, 78), "cx": 51.0, "cy": 60.0, **none}])
    # frame 1: A bolts ~35px away (fast)
    events = ba.update(1, [{"tid": 1, "box": (83, 48, 93, 78), "cx": 85.0, "cy": 60.0, **none},
                           {"tid": 2, "box": (49, 48, 59, 78), "cx": 51.0, "cy": 60.0, **none}])
    assert any(e["behavior"] == "snatching" for e in events), events
    print("  [PASS] behavior: snatching detected")


# ── 6.2 weapon / 6.4 face (pluggable) ────────────────────────────────────────

def test_weapon_disabled_and_stub():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    boxes = [(1, (10, 10, 60, 90))]
    assert WeaponDetector().enabled is False
    assert WeaponDetector().detect(frame, boxes) == []  # no model -> no-op

    wd = WeaponDetector(classify_fn=lambda crop: [("knife", 0.91)])
    ev = wd.detect(frame, boxes)
    assert len(ev) == 1 and ev[0]["type"] == "weapon" and ev[0]["confidence"] >= 0.9
    print("  [PASS] weapon: disabled without a model; fires via the two-stage classifier")


def test_face_disabled_and_stub():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    boxes = [(1, (10, 10, 60, 90))]
    assert FaceWatch().enabled is False
    assert FaceWatch().check(frame, boxes) == []  # no embedder -> no-op

    vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    fw = FaceWatch(embed_fn=lambda crop: vec, threshold=0.6)
    fw.add_face("Wanted Person", vec, reason="absconder")
    ev = fw.check(frame, boxes)
    assert len(ev) == 1 and ev[0]["type"] == "face_match" and ev[0]["requires_verification"] is True
    print("  [PASS] face: disabled without a model; alert-and-verify match via stub embedder")


# ── integration: analytic event -> history + alert; health on /api/cameras ───

class _FakePipeline:
    def __init__(self, **kw):
        pass

    def stream(self, on_frame, on_event=None, on_watchlist=None, on_analytic=None,
               loop=True, target_fps=None, stop_flag=None, night_vision_flag=None):
        i = 0
        while not (stop_flag and stop_flag()):
            on_frame(np.full((48, 64, 3), (i * 5) % 255, dtype=np.uint8))
            i += 1
            time.sleep(0.02)


def test_analytic_integration():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "history.db")
    from src import api_server, history_store, camera_store, watchlist, route_store, auth_store, audit_store
    for mod in (history_store, camera_store, watchlist, route_store, auth_store, audit_store):
        mod._DB_PATH = db
    history_store._THUMB_DIR = os.path.join(tmp, "thumbs")
    api_server.Pipeline = _FakePipeline

    with TestClient(api_server.app) as client:
        tok = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["token"]
        hdr = {"Authorization": f"Bearer {tok}"}

        # /api/cameras now surfaces per-camera health.
        cams = client.get("/api/cameras", headers=hdr).json()["cameras"]
        assert all("health" in c for c in cams), "health not surfaced on cameras"

        # Push a synthetic tamper analytic through the worker's callback.
        worker = api_server._cameras["CAM-02"]
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        worker._on_analytic(
            {"type": "tamper", "issue": "covered", "severity": "critical",
             "title": "CAMERA TAMPERING — LENS COVERED", "description": "test", "tracker_id": 0},
            frame,
        )
        events = client.get("/api/history", headers=hdr, params={"camera_id": "CAM-02"}).json()["events"]
        assert any("TAMPERING" in e["eventType"] for e in events), events
        assert isinstance(worker.health(), dict)
        print("  [PASS] integration: analytic event logged to history; health on /api/cameras")


if __name__ == "__main__":
    print("Running Phase 6 analytics tests…")
    test_tamper_covered()
    test_tamper_defocus()
    test_tamper_scene_change()
    test_behavior_loitering()
    test_behavior_altercation()
    test_behavior_snatching()
    test_weapon_disabled_and_stub()
    test_face_disabled_and_stub()
    test_analytic_integration()
    print("\nAll Phase 6 tests passed.")
