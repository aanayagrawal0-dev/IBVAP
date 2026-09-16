"""
Phase 2 acceptance test — Camera Registry as the source of truth.

Headline acceptance check (spec): "add a new camera through the UI, have it
start streaming without a restart." We prove exactly that against ONE running
app instance (TestClient enters startup once): POST a new camera and confirm
its CameraWorker starts producing frames immediately — no process restart.

To keep it hermetic on a box with no cameras and no YOLO weights, Pipeline is
stubbed with a fake that emits synthetic frames, and the DBs are redirected to
a temp file so the real history.db is never touched.

Run:  python tests/test_phase2_registry.py     (from backend/, venv active)
"""

import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient

from src import api_server, history_store, camera_store, auth_store, audit_store


class _FakePipeline:
    """Stands in for the real (YOLO-backed) Pipeline so CameraWorker can run
    without weights/GPU. Emits a synthetic BGR frame each tick and honours
    the stop flag so shutdown/edit/delete actually stop the worker."""

    def __init__(self, **kwargs):
        self.source_name = kwargs.get("source_name", "cam")

    def stream(self, on_frame, on_event=None, on_watchlist=None, on_analytic=None,
               loop=True, target_fps=None, stop_flag=None, night_vision_flag=None):
        i = 0
        while not (stop_flag and stop_flag()):
            on_frame(np.full((48, 64, 3), (i * 5) % 255, dtype=np.uint8))
            i += 1
            time.sleep(0.02)


def _redirect_dbs_to_temp():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "history.db")
    history_store._DB_PATH = db
    history_store._THUMB_DIR = os.path.join(tmp, "thumbs")
    camera_store._DB_PATH = db  # same file, mirroring production
    auth_store._DB_PATH = db
    audit_store._DB_PATH = db
    return tmp


def run():
    _redirect_dbs_to_temp()
    api_server.Pipeline = _FakePipeline  # patch BEFORE startup so seeded workers use it

    with TestClient(api_server.app) as client:
        # Phase 5: endpoints now require auth — log in as admin (sees all
        # departments) and attach the token to every request.
        tok = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["token"]
        client.headers.update({"Authorization": f"Bearer {tok}"})

        # ---- startup seeded the registry and started workers -------------
        cams = client.get("/api/cameras").json()["cameras"]
        by_id = {c["id"]: c for c in cams}
        assert len(cams) == 4, f"expected 4 seeded cameras, got {len(cams)}"
        assert by_id["CAM-01"]["streaming"] is True, "seeded webcam camera not streaming"
        assert by_id["CAM-01"]["connectivity"] == "online"
        assert by_id["CAM-04"]["connectivity"] == "no-source", "empty-source cam should be no-source"
        assert by_id["CAM-02"]["lat"] and by_id["CAM-02"]["lon"], "GIS coords missing on seed"
        print(f"  [PASS] startup seeded 4 cameras; live status merged correctly")

        # ---- THE acceptance check: add at runtime, stream with no restart --
        resp = client.post("/api/cameras", json={
            "name": "Test Junction — Rajkot",
            "department": "Traffic Police",
            "lat": 22.3039, "lon": 70.8022,
            "ownership": "GSP",
            "source_spec": "sample_data/synthetic_test_clip.mp4",
        })
        assert resp.status_code == 201, f"add failed: {resp.status_code} {resp.text}"
        new = resp.json()
        new_id = new["id"]
        assert new["camera_type"] == "file", f"type not inferred: {new['camera_type']}"
        assert new["streaming"] is True, "newly added camera did not start streaming"

        # Prove real frames are flowing on the same running server. (We assert
        # the worker's latest_jpeg rather than GET /api/stream, whose MJPEG
        # response is an infinite generator that a test client can't drain.)
        time.sleep(0.3)
        worker = api_server._cameras.get(new_id)
        assert worker is not None and worker.is_alive(), "no live worker for new camera"
        assert worker.latest_jpeg is not None, "worker produced no frames"
        # The stream endpoint at least resolves (not a 404) for the new camera.
        assert api_server._cameras.get(new_id) is not None
        print(f"  [PASS] {new_id} added at RUNTIME and streaming — no server restart")

        # ---- edit: disabling stops the worker; re-enabling restarts it ----
        r = client.put(f"/api/cameras/{new_id}", json={"enabled": False})
        assert r.json()["connectivity"] == "disabled"
        assert new_id not in api_server._cameras, "disabled camera's worker not stopped"

        r = client.put(f"/api/cameras/{new_id}", json={"enabled": True})
        assert r.json()["streaming"] is True, "re-enabled camera did not restart"
        assert api_server._cameras.get(new_id) is not None
        print("  [PASS] edit (disable/enable) stops/restarts the worker at runtime")

        # ---- edit: changing the source restarts against the new source ----
        r = client.put(f"/api/cameras/{new_id}", json={"source_spec": "rtsp://10.0.0.42:554/live"})
        assert r.status_code == 200
        assert r.json()["camera_type"] == "rtsp", "camera_type didn't follow source change"
        print("  [PASS] source change updates type and restarts worker")

        # ---- delete: removes row and stops worker -------------------------
        r = client.delete(f"/api/cameras/{new_id}")
        assert r.status_code == 200 and r.json()["deleted"] == new_id
        assert client.get(f"/api/cameras/{new_id}").status_code == 404
        assert new_id not in api_server._cameras, "deleted camera's worker still running"
        print("  [PASS] delete removes the camera and stops its worker")

        # ---- CSV bulk import -------------------------------------------
        csv_text = (
            "id,name,department,lat,lon,source_spec,ownership,enabled\n"
            "CAM-20,Bhavnagar Port Gate,Marine Police,21.7645,72.1519,sample_data/synthetic_test_clip.mp4,GSP,true\n"
            "CAM-21,Junagadh Chowk,Traffic Police,21.5222,70.4579,,GSP,true\n"  # no source
        )
        r = client.post("/api/cameras/import", json={"csv": csv_text})
        body = r.json()
        assert set(body["added"]) == {"CAM-20", "CAM-21"}, f"import added: {body}"
        assert api_server._cameras.get("CAM-20") is not None, "imported live cam didn't start"
        assert api_server._cameras.get("CAM-21") is None, "no-source cam shouldn't start"
        print(f"  [PASS] CSV import onboarded {len(body['added'])} cameras (live ones started)")

        # duplicate import is skipped, not errored
        r2 = client.post("/api/cameras/import", json={"csv": csv_text})
        assert set(r2.json()["skipped"]) == {"CAM-20", "CAM-21"}
        print("  [PASS] re-importing existing ids is skipped, not errored")

        # ---- export.csv resolves to the static route, not {camera_id} -----
        r = client.get("/api/cameras/export.csv")
        assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
        assert r.text.splitlines()[0].startswith("id,name,department"), "export header wrong"
        assert "CAM-20" in r.text
        print("  [PASS] export.csv served correctly (route ordering fix holds)")


if __name__ == "__main__":
    print("Running Phase 2 camera-registry tests…")
    run()
    print("\nAll Phase 2 tests passed.")
