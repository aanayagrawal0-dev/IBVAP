"""
Phase 4 acceptance test — cross-camera route reconstruction.

Spec acceptance: "given a plate seen on 3+ cameras, produce a correct,
ordered, timestamped route on the map." We verify:
  - the ordered, coordinate-joined route from plate sightings,
  - Re-ID fusion: a camera that never read the plate but whose appearance
    embedding matches is added to the route and the plate backfilled,
  - the camera-topology guard: an embedding match at a geographically
    impossible time is rejected,
  - the transition-plausibility function directly,
  - the end-to-end API path (inject sightings -> GET /api/route/{plate}).

Run:  python tests/test_phase4_route.py     (from backend/, venv active)
"""

import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient

from src import camera_store, route_store, route

# A short corridor of real Gujarat coordinates (~45-75 km hops) plus a far
# decoy camera in Rajkot (~230 km off-corridor).
CAMS = {
    "CAM-A": (23.0300, 72.5800, "Ahmedabad Ashram Rd"),
    "CAM-B": (22.6900, 72.8600, "Nadiad Bypass"),
    "CAM-C": (22.3100, 73.1800, "Vadodara Ring Rd"),
    "CAM-D": (21.7000, 72.9800, "Bharuch Toll"),
    "CAM-FAR": (22.3000, 70.8000, "Rajkot Chowk"),
}

_rng = np.random.default_rng(7)


def _unit(v):
    return (v / (np.linalg.norm(v) + 1e-8)).astype(np.float32)


def _base_vec():
    return _unit(_rng.standard_normal(128).astype(np.float32))


def _near(v, noise=0.02):
    return _unit(v + _rng.standard_normal(128).astype(np.float32) * noise)


def _fresh_db():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "history.db")
    camera_store._DB_PATH = db
    route_store._DB_PATH = db
    camera_store.init_db()
    route_store.init_db()
    for cid, (lat, lon, name) in CAMS.items():
        camera_store.add_camera(cid, {"name": name, "lat": lat, "lon": lon, "department": "Traffic Police"})
    return tmp


def test_transition_plausibility():
    _fresh_db()
    cams = {cid: {"lat": lat, "lon": lon} for cid, (lat, lon, _n) in CAMS.items()}

    # A -> B (~45 km) in 50 min: plausible.
    assert route.transition_plausibility("CAM-A", "CAM-B", 3000, cams, {})["plausible"] is True
    # A -> FAR (~230 km) in 100 s: physically impossible (too fast).
    tp = route.transition_plausibility("CAM-A", "CAM-FAR", 100, cams, {})
    assert tp["plausible"] is False and "too fast" in tp["reason"]
    # A -> B in 55 hours: gap too long for a single hop.
    assert route.transition_plausibility("CAM-A", "CAM-B", 200000, cams, {})["plausible"] is False
    # out-of-order timestamps.
    assert route.transition_plausibility("CAM-A", "CAM-B", -500, cams, {})["plausible"] is False
    # same camera: always fine.
    assert route.transition_plausibility("CAM-A", "CAM-A", 5, cams, {})["plausible"] is True
    print("  [PASS] transition plausibility: geo-derived transit window + guards")


def test_route_with_reid_fusion_and_topology():
    _fresh_db()
    plate_vec = _base_vec()
    t0 = 1_000_000.0

    # Seeds: plate read on 3 cameras along the corridor, plausible timings.
    route_store.record_sighting("CAM-A", 1, "car", t0 + 0,    plate="GJ05CD1234", embedding=_near(plate_vec))
    route_store.record_sighting("CAM-B", 2, "car", t0 + 3000, plate="GJ05CD1234", embedding=_near(plate_vec))
    route_store.record_sighting("CAM-C", 3, "car", t0 + 6000, plate="GJ05CD1234", embedding=_near(plate_vec))

    # Re-ID candidate: CAM-D, NO plate, but matching appearance, plausible time.
    route_store.record_sighting("CAM-D", 4, "car", t0 + 9000, plate=None, embedding=_near(plate_vec))

    # Decoy 1: random appearance, no plate -> must NOT fuse.
    route_store.record_sighting("CAM-A", 5, "car", t0 + 1000, plate=None, embedding=_base_vec())

    # Decoy 2: matching appearance BUT at a far camera 100 s after CAM-A ->
    # topology must reject the teleport even though the embedding matches.
    route_store.record_sighting("CAM-FAR", 6, "car", t0 + 100, plate=None, embedding=_near(plate_vec))

    r = route.reconstruct_route("gj05 cd 1234")
    stops = r["stops"]
    seq = [(s["camera_id"], s["plate_source"]) for s in stops]

    # 3+ cameras, correctly ordered, with the fused CAM-D appended.
    assert [c for c, _ in seq] == ["CAM-A", "CAM-B", "CAM-C", "CAM-D"], seq
    assert seq[0][1] == "anpr" and seq[3][1] == "reid", seq
    assert stops[3]["reid_similarity"] is not None and stops[3]["reid_similarity"] >= 0.82
    # Plate backfilled onto the fused stop.
    assert all(s.get("lat") is not None for s in stops), "GIS coords not joined"
    # Decoys excluded.
    assert "CAM-FAR" not in [s["camera_id"] for s in stops], "topology failed to reject teleport"
    # Every corridor transition is plausible.
    assert r["meta"]["all_transitions_plausible"] is True
    assert r["meta"]["by_source"] == {"anpr": 3, "reid": 1}
    assert r["meta"]["camera_count"] == 4
    print(f"  [PASS] route: {' -> '.join(c for c,_ in seq)}  (CAM-D via Re-ID, CAM-FAR rejected)")

    # Turning topology OFF would let the teleport in — proves the guard matters.
    r2 = route.reconstruct_route("GJ05CD1234", use_topology=False)
    assert "CAM-FAR" in [s["camera_id"] for s in r2["stops"]], "topology-off should admit the teleport"
    print("  [PASS] topology guard is load-bearing (teleport admitted only when disabled)")


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


def test_route_api_end_to_end():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "history.db")
    from src import api_server, history_store, watchlist, auth_store, audit_store
    history_store._DB_PATH = db
    history_store._THUMB_DIR = os.path.join(tmp, "thumbs")
    camera_store._DB_PATH = db
    watchlist._DB_PATH = db
    route_store._DB_PATH = db
    auth_store._DB_PATH = db
    audit_store._DB_PATH = db
    api_server.Pipeline = _FakePipeline

    with TestClient(api_server.app) as client:
        tok = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["token"]
        client.headers.update({"Authorization": f"Bearer {tok}"})
        # "Show" the same plate to 3 seeded cameras (which have GIS coords).
        for cam in ("CAM-01", "CAM-02", "CAM-03"):
            r = client.post("/api/route/sighting", json={"camera_id": cam, "plate": "GJ12AB3456"})
            assert r.status_code == 200 and r.json()["recorded"] is True
            time.sleep(0.02)  # ensure strictly increasing arrival times

        plates = client.get("/api/route/plates").json()["plates"]
        assert any(p["plate"] == "GJ12AB3456" and p["cameras"] == 3 for p in plates), plates

        route_resp = client.get("/api/route/GJ12AB3456").json()
        stops = route_resp["stops"]
        assert [s["camera_id"] for s in stops] == ["CAM-01", "CAM-02", "CAM-03"], stops
        assert all(s["ts_iso"] for s in stops) and all(s["lat"] is not None for s in stops)
        # Ordered by time.
        assert [s["ts_epoch"] for s in stops] == sorted(s["ts_epoch"] for s in stops)
        print(f"  [PASS] API: 3-camera plate route ordered + coord-joined ({route_resp['meta']['camera_count']} cameras)")


if __name__ == "__main__":
    print("Running Phase 4 route-reconstruction tests…")
    test_transition_plausibility()
    test_route_with_reid_fusion_and_topology()
    test_route_api_end_to_end()
    print("\nAll Phase 4 tests passed.")
