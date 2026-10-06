"""
Seed DEMO vehicle routes for the Route page (and remove them again).

Live route reconstruction needs plates read off real traffic by ANPR, which
the gateway's limited feeds rarely allow during a demo. This inserts clearly
labelled demo sightings (class "car (demo)", shown as DEMO on the map) along
real roads between cameras that have map positions, with travel times at
normal road speeds — so the route map, numbered stops, hover details, the
Re-ID fill-in and the "impossible jump" check can all be shown.

    python seed_demo_routes.py            # add the demo routes
    python seed_demo_routes.py --clear    # remove every demo sighting

Run from backend/ with the backend stopped or running (same SQLite file).
"""

import sqlite3
import sys
import time
from math import asin, cos, radians, sin, sqrt

import numpy as np

from src import camera_store, route_store

DEMO_CLASS = "car (demo)"

# plate, [(camera_id, plate_readable)], road speed km/h
ROUTES = [
    # Ahmedabad: Paldi -> Ambawadi -> Chimanbhai bridge (plate unreadable -> Re-ID) -> Visat -> ONGC -> Adalaj
    ("GJ01KX4471", [("cam04", True), ("cam13", True), ("cam01", False), ("cam05", True),
                    ("cam03", True), ("cam12", True)], 28),
    # Junagadh city loop
    ("GJ11AB2093", [("cam06", True), ("cam10", True), ("cam08", False), ("cam11", True)], 22),
    # Rajkot -> Junagadh -> Veraval (highway)
    ("GJ03MN7780", [("cam18", True), ("cam17", True), ("cam06", True), ("cam07", True)], 60),
    # Deliberate "teleport": Bilimora -> Gandhidham (~450 km) in 12 minutes -> flagged implausible
    ("GJ15CD1122", [("cam27", True), ("cam28", True), ("cam30", True)], None),
]


def _km(a, b):
    dlat, dlon = radians(b["lat"] - a["lat"]), radians(b["lon"] - a["lon"])
    h = sin(dlat / 2) ** 2 + cos(radians(a["lat"])) * cos(radians(b["lat"])) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


def clear():
    route_store.init_db()
    con = sqlite3.connect(route_store._DB_PATH)
    with con:
        n = con.execute("DELETE FROM vehicle_sightings WHERE class_name = ?", (DEMO_CLASS,)).rowcount
    con.close()
    route_store.init_db()
    print(f"removed {n} demo sightings")


def seed():
    camera_store.init_db()
    route_store.init_db()
    cams = {c["id"]: c for c in camera_store.list_cameras()}
    rng = np.random.default_rng(7)
    tracker = 900_000
    now = time.time()
    for r_idx, (plate, stops, speed) in enumerate(ROUTES):
        missing = [c for c, _ in stops if c not in cams or cams[c].get("lat") is None]
        if missing:
            print(f"skip {plate}: cameras without a map position {missing}")
            continue
        vehicle = rng.normal(size=128)
        # Work out arrival times (the route ends a few minutes ago).
        times = [0.0]
        for (a, _), (b, _) in zip(stops, stops[1:]):
            if speed is None:
                times.append(times[-1] + 12 * 60)  # impossible on purpose
            else:
                times.append(times[-1] + _km(cams[a], cams[b]) / speed * 3600 + 90)  # + stop at signals
        start = now - (r_idx + 1) * 600 - times[-1]
        for (cam_id, readable), t in zip(stops, times):
            tracker += 1
            emb = vehicle + rng.normal(scale=0.08, size=128)  # same vehicle, slightly different view
            ts = start + t
            route_store.record_sighting(cam_id, tracker, DEMO_CLASS, ts,
                                        plate=plate if readable else None,
                                        embedding=emb / np.linalg.norm(emb))
            route_store.record_sighting(cam_id, tracker, DEMO_CLASS, ts + 25)  # ~25 s in view
        print(f"seeded {plate}: {' -> '.join(c for c, _ in stops)}")
    print("done — open the Route page (demo stops are marked DEMO). Remove with: python seed_demo_routes.py --clear")


if __name__ == "__main__":
    clear() if "--clear" in sys.argv else seed()
