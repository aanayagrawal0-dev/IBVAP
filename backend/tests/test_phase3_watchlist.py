"""
Phase 3 acceptance test — watchlist matching engine.

Spec acceptance: "add a plate to the watchlist, show that plate to a camera,
confirm a real-time alert fires within a few seconds." PaddleOCR isn't
installed on this box (so no live OCR), so we exercise the REAL match + alert
+ DB path via the /api/watchlist/simulate hook, which stands in for "a camera
read this plate." We also unit-test the watchlist store and the pipeline's
throttled-OCR helper directly.

Run:  python tests/test_phase3_watchlist.py     (from backend/, venv active)
"""

import os
import queue
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient

from src import watchlist


def _fresh_wl_db():
    tmp = tempfile.mkdtemp()
    watchlist._DB_PATH = os.path.join(tmp, "history.db")
    watchlist.init_db()
    return tmp


# ── store-level ──────────────────────────────────────────────────────────

def test_normalize_and_match():
    _fresh_wl_db()
    entry = watchlist.add_plate("GJ01 AB 1234", label="Stolen vehicle", severity="critical")
    assert entry["plate_normalized"] == "GJ01AB1234"

    # Differently-formatted reads still match (spaces, dashes, lowercase).
    for variant in ["gj01ab1234", "GJ-01-AB-1234", "  gj01 ab1234 "]:
        m = watchlist.check_plate(variant)
        assert m is not None and m["id"] == entry["id"], f"variant {variant!r} did not match"

    # A plate not on the list doesn't match.
    assert watchlist.check_plate("MH20XY9999") is None
    print("  [PASS] normalization makes formatting-insensitive matches; non-listed plate misses")


def test_duplicate_rejected_and_active_toggle():
    _fresh_wl_db()
    watchlist.add_plate("KA05M9999")
    try:
        watchlist.add_plate("ka-05-m-9999")  # same after normalization
        assert False, "duplicate should have raised"
    except ValueError:
        pass

    entry = watchlist.list_entries()[0]
    watchlist.set_active(entry["id"], False)
    assert watchlist.check_plate("KA05M9999") is None, "inactive entry should not match"
    watchlist.set_active(entry["id"], True)
    assert watchlist.check_plate("KA05M9999") is not None, "reactivated entry should match again"

    assert watchlist.remove(entry["id"]) is True
    assert watchlist.check_plate("KA05M9999") is None
    print("  [PASS] duplicate rejected; active toggle + remove behave correctly")


# ── pipeline throttled-OCR helper (real method, faked OCR) ─────────────────

def test_maybe_read_plate_throttle_and_cache():
    from src.pipeline import Pipeline

    p = object.__new__(Pipeline)  # bypass heavy __init__ (no YOLO)
    p.anpr_interval = 15
    p._plate_cache = {}
    p._plate_read_frame = {}

    class FakeAnpr:
        def __init__(self):
            self.calls = 0
            self.ret = None

        def extract_plate(self, *a):
            self.calls += 1
            return self.ret

    p.anpr_engine = FakeAnpr()

    # frame 0: OCR attempted, returns nothing yet.
    assert p._maybe_read_plate(1, 0, None, 0, 0, 10, 10) is None
    assert p.anpr_engine.calls == 1
    # frame 5 (< interval): NO new OCR call, cached (None) returned.
    assert p._maybe_read_plate(1, 5, None, 0, 0, 10, 10) is None
    assert p.anpr_engine.calls == 1
    # frame 20 (>= interval): OCR runs again, now returns a plate.
    p.anpr_engine.ret = "GJ01AB1234"
    assert p._maybe_read_plate(1, 20, None, 0, 0, 10, 10) == "GJ01AB1234"
    assert p.anpr_engine.calls == 2
    # frame 25 (< interval): cached plate returned without another OCR call.
    assert p._maybe_read_plate(1, 25, None, 0, 0, 10, 10) == "GJ01AB1234"
    assert p.anpr_engine.calls == 2
    print("  [PASS] _maybe_read_plate throttles OCR per tracker and caches reads")


# ── end-to-end match -> alert -> DB via the API ────────────────────────────

def _drain_alerts(api_server):
    out = []
    while True:
        try:
            out.append(api_server._alert_queue.get_nowait())
        except queue.Empty:
            return out


class _FakePipeline:
    def __init__(self, **kw):
        pass

    def stream(self, on_frame, on_event=None, on_watchlist=None, on_analytic=None,
               loop=True, target_fps=None, stop_flag=None, night_vision_flag=None):
        import numpy as np
        i = 0
        while not (stop_flag and stop_flag()):
            on_frame(np.full((48, 64, 3), (i * 5) % 255, dtype=np.uint8))
            i += 1
            time.sleep(0.02)


def test_match_fires_alert_and_logs_event():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "history.db")
    from src import api_server, history_store, camera_store, auth_store, audit_store
    history_store._DB_PATH = db
    history_store._THUMB_DIR = os.path.join(tmp, "thumbs")
    camera_store._DB_PATH = db
    watchlist._DB_PATH = db
    auth_store._DB_PATH = db
    audit_store._DB_PATH = db
    api_server.Pipeline = _FakePipeline

    with TestClient(api_server.app) as client:
        tok = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["token"]
        client.headers.update({"Authorization": f"Bearer {tok}"})
        _drain_alerts(api_server)  # clear anything from startup

        # 1. add a plate to the watchlist
        r = client.post("/api/watchlist", json={"plate": "GJ01AB1234", "label": "Stolen vehicle"})
        assert r.status_code == 201, r.text
        assert client.get("/api/watchlist").json()["entries"][0]["plate_normalized"] == "GJ01AB1234"

        # 2. "show that plate to a camera" — differently formatted, to prove
        #    normalization end to end
        r = client.post("/api/watchlist/simulate", json={"camera_id": "CAM-02", "plate": "gj 01 ab 1234"})
        body = r.json()
        assert body["matched"] is True, f"expected match, got {body}"
        assert body["plate"] == "GJ01AB1234"

        # 3. a real-time alert fired (distinct watchlist_match type)
        alerts = _drain_alerts(api_server)
        wl = [a for a in alerts if a.get("type") == "watchlist_match"]
        assert len(wl) == 1, f"expected 1 watchlist alert, got {alerts}"
        assert wl[0]["license_plate"] == "GJ01AB1234"
        assert wl[0]["camera"] == "CAM-02"
        assert wl[0]["severity"] == "critical"

        # 4. and it was persisted to history as a watchlist_match event
        events = client.get("/api/history", params={"camera_id": "CAM-02"}).json()["events"]
        hit = [e for e in events if "WATCHLIST MATCH" in e["eventType"]]
        assert len(hit) >= 1, f"watchlist event not logged: {events}"
        assert hit[0]["severity"] == "critical"

        # 5. a plate NOT on the list does not fire
        r = client.post("/api/watchlist/simulate", json={"camera_id": "CAM-02", "plate": "MH20XY0000"})
        assert r.json()["matched"] is False
        assert len([a for a in _drain_alerts(api_server) if a.get("type") == "watchlist_match"]) == 0

        print("  [PASS] add plate -> simulate sighting -> real-time watchlist alert + history event")


if __name__ == "__main__":
    print("Running Phase 3 watchlist tests…")
    test_normalize_and_match()
    test_duplicate_rejected_and_active_toggle()
    test_maybe_read_plate_throttle_and_cache()
    test_match_fires_alert_and_logs_event()
    print("\nAll Phase 3 tests passed.")
