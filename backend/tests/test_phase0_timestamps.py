"""
Phase 0 acceptance test — event timestamp reliability & concurrency.

Stands in for the spec's "run all cameras for 30+ min, then verify every
event has a correct, orderable timestamp with no gaps or duplicates" check,
but stresses the guarantees far harder than a real run would: dozens of
threads (one per simulated camera) hammer insert_event() at once, then we
assert the exact properties route reconstruction and the audit trail depend
on:

  1. No dropped/lost writes under concurrent load  (row count == inserts)
  2. No gaps or duplicate ids                       (ids are exactly 1..N)
  3. A strict, unambiguous total order              ((ts_epoch, id) unique
                                                       & monotonic)
  4. Monotonic timestamps across a backward clock jump (NTP-style)
  5. ts_epoch and ts_iso never disagree             (same authoritative read)
  6. The connection leak is gone                    (one connect() for the
                                                       whole process)

Run:  python tests/test_phase0_timestamps.py     (from backend/, venv active)
"""

import os
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import history_store


def _fresh_db():
    """Point history_store at a brand-new temp DB and (re)initialise it.
    init_db() closes any prior connection, reopens at the new path, and
    resets the monotonic-timestamp watermark — so each test is isolated."""
    tmp = tempfile.mkdtemp()
    history_store._DB_PATH = os.path.join(tmp, "test_history.db")
    history_store._THUMB_DIR = os.path.join(tmp, "thumbs")
    history_store.init_db()
    return tmp


def _all_rows_oldest_first():
    """Every row, ordered ascending by the same (ts_epoch, id) key the real
    query uses descending — i.e. true chronological order."""
    with history_store._lock:
        conn = history_store._get_conn()
        return conn.execute(
            "SELECT id, ts_epoch, ts_iso FROM events ORDER BY ts_epoch ASC, id ASC"
        ).fetchall()


def test_no_dropped_or_reordered_under_concurrency():
    _fresh_db()

    N_CAMERAS = 24
    PER_CAMERA = 500
    expected = N_CAMERAS * PER_CAMERA

    # Raise the retention cap above the test volume so the intentional
    # oldest-row prune (a feature, not a bug) doesn't fire — this test is
    # about whether *concurrency* ever drops or reorders a write, which must
    # be verified against the full, un-pruned set. (Pruning order is checked
    # separately in test_prune_keeps_newest_in_order.)
    orig_max, orig_prune = history_store._MAX_ROWS, history_store._PRUNE_TO
    history_store._MAX_ROWS = expected + 1_000
    history_store._PRUNE_TO = expected + 1_000

    barrier = threading.Barrier(N_CAMERAS)  # release all threads at once

    def worker(cam_idx):
        barrier.wait()
        for i in range(PER_CAMERA):
            history_store.insert_event(
                camera_id=f"CAM-{cam_idx:02d}",
                event_type="entered",
                zone_name="zone-1",
                tracker_id=i,
                class_name="person",
                severity="warning",
                title="PERSON ENTERED ZONE",
                description="stress",
            )

    try:
        threads = [threading.Thread(target=worker, args=(c,)) for c in range(N_CAMERAS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = _all_rows_oldest_first()
    finally:
        history_store._MAX_ROWS, history_store._PRUNE_TO = orig_max, orig_prune

    # 1. No dropped writes.
    assert len(rows) == expected, f"expected {expected} rows, got {len(rows)}"

    # 2. No gaps, no duplicate ids: the id set is exactly 1..N.
    ids = [r["id"] for r in rows]
    assert ids == list(range(1, expected + 1)), "ids are not a contiguous 1..N sequence"

    # 3. Strict total order: (ts_epoch, id) tuples strictly increasing, so
    #    ordering is never ambiguous even when timestamps collide.
    keys = [(r["ts_epoch"], r["id"]) for r in rows]
    assert keys == sorted(keys), "(ts_epoch, id) not sorted — ordering is ambiguous"
    assert len(set(keys)) == len(keys), "duplicate (ts_epoch, id) key — not a total order"

    # 3b. ts_epoch alone is monotonic non-decreasing (clamp held under load).
    epochs = [r["ts_epoch"] for r in rows]
    assert all(a <= b for a, b in zip(epochs, epochs[1:])), "ts_epoch went backward under load"

    # 5. ts_iso is derived from the SAME read as ts_epoch (second resolution).
    for r in rows:
        want = datetime.fromtimestamp(r["ts_epoch"]).isoformat(timespec="seconds")
        assert r["ts_iso"] == want, f"ts_iso {r['ts_iso']} != {want} derived from ts_epoch"

    print(f"  [PASS] {expected} concurrent inserts: no drops, no gaps, strict order")


def test_backward_clock_jump_is_clamped():
    _fresh_db()

    # Simulate NTP stepping the wall clock: the 3rd event's "now" is in the
    # past relative to the 2nd. The stored ts_epoch must not regress.
    supplied = [1_000.0, 1_000.5, 999.0, 1_001.0]
    for ts in supplied:
        history_store.insert_event(
            camera_id="CAM-00",
            event_type="entered",
            zone_name="z",
            tracker_id=0,
            class_name="car",
            severity="warning",
            title="t",
            description="d",
            event_ts=ts,
        )

    rows = _all_rows_oldest_first()
    stored = [r["ts_epoch"] for r in rows]

    # The backward jump (999.0) is clamped up to the previous watermark 1000.5.
    assert stored == [1_000.0, 1_000.5, 1_000.5, 1_001.0], f"clamp failed: {stored}"
    # Insertion order is preserved because the id breaks the 1000.5 tie.
    assert [r["id"] for r in rows] == [1, 2, 3, 4], "backward-jump event sorted out of place"
    print("  [PASS] backward clock jump clamped, order preserved via id tiebreak")


def test_single_shared_connection_no_leak():
    # Count every sqlite3.connect() from init_db() through a batch of writes.
    # The old open-per-call code would connect thousands of times (and leak
    # each one); the shared connection must connect exactly once.
    real_connect = sqlite3.connect
    count = {"n": 0}

    def counting_connect(*args, **kwargs):
        count["n"] += 1
        return real_connect(*args, **kwargs)

    sqlite3.connect = counting_connect
    try:
        _fresh_db()  # closes any prior conn, opens exactly one
        for i in range(300):
            history_store.insert_event(
                camera_id="CAM-00",
                event_type="entered",
                zone_name="z",
                tracker_id=i,
                class_name="person",
                severity="info",
                title="t",
                description="d",
            )
    finally:
        sqlite3.connect = real_connect

    assert count["n"] == 1, f"expected exactly 1 connection, got {count['n']} (leak / re-open)"
    print(f"  [PASS] 301 operations used exactly {count['n']} connection (no leak)")


def test_prune_keeps_newest_in_order():
    _fresh_db()

    # Tiny cap so the prune fires deterministically after a known number of
    # inserts, and we can assert it drops the OLDEST and keeps the NEWEST in
    # chronological order.
    orig_max, orig_prune = history_store._MAX_ROWS, history_store._PRUNE_TO
    history_store._MAX_ROWS = 400
    history_store._PRUNE_TO = 300
    try:
        # 600 sequential inserts; prune is checked on ids that are multiples
        # of 200, so it fires once the table passes 400 rows.
        for i in range(600):
            history_store.insert_event(
                camera_id="CAM-00",
                event_type="entered",
                zone_name="z",
                tracker_id=i,
                class_name="person",
                severity="info",
                title="t",
                description=f"d{i}",
                event_ts=1_000.0 + i,  # strictly increasing, deterministic
            )
        rows = _all_rows_oldest_first()
    finally:
        history_store._MAX_ROWS, history_store._PRUNE_TO = orig_max, orig_prune

    # Table stayed within the cap and kept the most-recent rows only.
    assert len(rows) <= 400, f"prune didn't hold the cap: {len(rows)} rows"
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids), "surviving rows are out of order after prune"
    # The very newest event (id 600) must survive; the very oldest (id 1) must not.
    assert ids[-1] == 600, "newest event was pruned"
    assert 1 not in ids, "oldest event survived a prune that should have dropped it"
    print(f"  [PASS] prune held cap at {len(rows)} rows, kept newest in order")


# ── runner ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running Phase 0 timestamp/concurrency tests…")
    test_no_dropped_or_reordered_under_concurrency()
    test_backward_clock_jump_is_clamped()
    test_single_shared_connection_no_leak()
    test_prune_keeps_newest_in_order()
    print("\nAll Phase 0 tests passed.")
