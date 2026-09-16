"""
Phase 5 acceptance test — security, RBAC, audit, redaction.

Acceptance items verified:
  - real backend session validation (login -> token; no token -> 401),
  - two different role logins see different camera/department access,
  - role gating (a viewer cannot perform operator actions),
  - a plate search shows up in the audit log,
  - an exported clip has non-target (person) regions blurred.

Run:  python tests/test_phase5_security.py     (from backend/, venv active)
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

from src import auth_store, redaction


# ── auth store ────────────────────────────────────────────────────────────

def test_password_hashing():
    h = auth_store._hash_password("s3cret")
    assert h != "s3cret" and "$" in h
    assert auth_store._verify_password("s3cret", h) is True
    assert auth_store._verify_password("wrong", h) is False
    print("  [PASS] passwords are salted+hashed and verify correctly")


# ── redaction (pure) ──────────────────────────────────────────────────────

def test_redact_boxes_blurs_region_only():
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 256, (120, 120, 3), dtype=np.uint8)  # high-frequency
    box = (30, 30, 90, 90)
    before = cv2.Laplacian(frame[30:90, 30:90], cv2.CV_64F).var()

    out = redaction.redact_boxes(frame.copy(), [box])
    after = cv2.Laplacian(out[30:90, 30:90], cv2.CV_64F).var()

    assert after < before * 0.4, f"region not blurred enough ({after} vs {before})"
    # Outside the box is untouched.
    assert np.array_equal(frame[0:20, 0:20], out[0:20, 0:20])
    print(f"  [PASS] redact_boxes pixelated the region ({before:.0f} -> {after:.0f}), left the rest intact")


def test_redact_clip_over_video():
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "in.mp4")
    out = os.path.join(tmp, "out.mp4")
    rng = np.random.default_rng(1)
    w = cv2.VideoWriter(src, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (96, 96))
    for _ in range(8):
        w.write(rng.integers(0, 256, (96, 96, 3), dtype=np.uint8))
    w.release()

    # boxes_fn blurs the whole frame (stands in for "all persons").
    info = redaction.redact_clip(src, out, boxes_fn=lambda f: [(0, 0, f.shape[1], f.shape[0])], max_frames=5)
    assert info["frames"] > 0 and os.path.exists(out) and os.path.getsize(out) > 0
    assert info["redacted_regions"] == info["frames"]
    print(f"  [PASS] redact_clip wrote {info['frames']} redacted frames")


# ── API: auth + RBAC + audit ──────────────────────────────────────────────

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


def _client():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "history.db")
    from src import api_server, history_store, camera_store, watchlist, route_store, audit_store
    for mod in (history_store, camera_store, watchlist, route_store, auth_store, audit_store):
        mod._DB_PATH = db
    history_store._THUMB_DIR = os.path.join(tmp, "thumbs")
    api_server.Pipeline = _FakePipeline
    return TestClient(api_server.app)


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"login {username} failed: {r.status_code} {r.text}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_auth_required_and_login():
    with _client() as client:
        # No token -> 401.
        assert client.get("/api/cameras").status_code == 401
        # Bad creds -> 401.
        assert client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
        # Good creds -> token that works.
        hdr = _login(client, "admin", "admin123")
        assert client.get("/api/cameras", headers=hdr).status_code == 200
        me = client.get("/api/auth/me", headers=hdr).json()["user"]
        assert me["role"] == "admin" and me["department"] == "*"
        print("  [PASS] backend session validation: no token 401, bad creds 401, valid token works")


def test_department_rbac():
    with _client() as client:
        admin = _login(client, "admin", "admin123")
        rakesh = _login(client, "rakesh", "traffic123")   # operator, Traffic Police
        meena = _login(client, "meena", "viewer123")      # viewer, Home Guard

        admin_cams = {c["id"] for c in client.get("/api/cameras", headers=admin).json()["cameras"]}
        traffic_cams = {c["id"] for c in client.get("/api/cameras", headers=rakesh).json()["cameras"]}
        hg_cams = {c["id"] for c in client.get("/api/cameras", headers=meena).json()["cameras"]}

        assert admin_cams == {"CAM-01", "CAM-02", "CAM-03", "CAM-04"}, admin_cams
        assert traffic_cams == {"CAM-01", "CAM-03"}, traffic_cams   # Traffic Police only
        assert hg_cams == {"CAM-02"}, hg_cams                       # Home Guard only
        assert traffic_cams != hg_cams

        # Cross-department camera access is denied.
        assert client.get("/api/cameras/CAM-02", headers=rakesh).status_code == 403  # Traffic -> HG cam
        assert client.get("/api/cameras/CAM-01", headers=meena).status_code == 403   # HG -> Traffic cam
        # And the live stream is department-gated too (403 returns before streaming).
        assert client.get("/api/stream/CAM-01", headers=meena).status_code == 403
        print(f"  [PASS] RBAC: admin={sorted(admin_cams)}, traffic={sorted(traffic_cams)}, home-guard={sorted(hg_cams)}")


def test_role_gating():
    with _client() as client:
        meena = _login(client, "meena", "viewer123")   # viewer
        rakesh = _login(client, "rakesh", "traffic123")  # operator
        # Viewer cannot add to the watchlist.
        assert client.post("/api/watchlist", headers=meena, json={"plate": "GJ01AB1111"}).status_code == 403
        # Operator can.
        assert client.post("/api/watchlist", headers=rakesh, json={"plate": "GJ01AB1111"}).status_code == 201
        print("  [PASS] role gating: viewer blocked from operator write, operator allowed")


def test_audit_logs_plate_search():
    with _client() as client:
        rakesh = _login(client, "rakesh", "traffic123")
        # Perform a plate search (route reconstruction).
        client.get("/api/route/GJ07ZZ4321", headers=rakesh)
        # It must appear in the audit log.
        entries = client.get("/api/audit", headers=rakesh).json()["entries"]
        searches = [e for e in entries if e["action"] == "search_plate" and e["username"] == "rakesh"]
        assert searches and searches[0]["target"] == "GJ07ZZ4321", entries[:5]
        # login was audited too.
        assert any(e["action"] == "login" and e["username"] == "rakesh" for e in entries)
        print("  [PASS] audit log captured the plate search (and the login) with the actor")


if __name__ == "__main__":
    print("Running Phase 5 security/RBAC/audit/redaction tests…")
    test_password_hashing()
    test_redact_boxes_blurs_region_only()
    test_redact_clip_over_video()
    test_auth_required_and_login()
    test_department_rbac()
    test_role_gating()
    test_audit_logs_plate_search()
    print("\nAll Phase 5 tests passed.")
