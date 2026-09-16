"""
Cross-camera route reconstruction (Phase 4).

Given a plate number, assemble the ordered, timestamped path a vehicle took
across cameras:

  1. Seed with every sighting whose ANPR plate matches (route_store).
  2. Fuse in plate-less sightings whose appearance embedding matches a seed
     (Re-ID via reid.py cosine similarity) — so a camera that couldn't read
     the plate (bad angle/low light) still lands on the route, and the plate
     is backfilled onto it.
  3. Constrain by camera topology: a fused hop is only accepted if the
     elapsed time between cameras is physically plausible. Plausibility is
     derived from the cameras' GIS coordinates (Haversine distance vs. a
     sane ground-speed band), with an optional explicit topology.json to
     encode road-aware transition windows. This is config, not new ML, and
     is the highest-leverage guard against false cross-camera links.
  4. Order by time, collapse consecutive same-camera sightings into one
     stop, join the registry for GIS coordinates, and annotate each hop with
     its transition plausibility.
"""

import json
import os
from math import asin, cos, radians, sin, sqrt

from src import camera_store, route_store
from src.reid import _cosine_similarity
from src.route_store import embedding_from_blob
from src.watchlist import normalize_plate

# Ground-speed band used to turn distance into a plausible transit-time window.
_SPEED_MIN_KMH = 12.0    # heavy congestion / detour
_SPEED_MAX_KMH = 140.0   # highway upper bound
_SLACK = 1.5             # multiplicative slack on each end of the window
_COLOCATED_KM = 0.05     # cameras this close are treated as the same point

_DEFAULT_REID_THRESHOLD = 0.82

_TOPOLOGY_PATH = os.path.join(os.path.dirname(__file__), "..", "topology.json")


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _load_topology() -> dict:
    """Optional explicit adjacency: topology.json = {"edges":[{"a","b","min_s","max_s"}]}.
    Undirected. Absent file -> purely geometry-derived plausibility."""
    try:
        with open(_TOPOLOGY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        edges = {}
        for e in data.get("edges", []):
            key = frozenset((e["a"], e["b"]))
            edges[key] = (float(e["min_s"]), float(e["max_s"]))
        return edges
    except (OSError, ValueError, KeyError):
        return {}


def _camera_index() -> dict:
    """camera_id -> {lat, lon, name, department} from the registry."""
    idx = {}
    for c in camera_store.list_cameras():
        idx[c["id"]] = {
            "lat": c.get("lat"),
            "lon": c.get("lon"),
            "name": c.get("name"),
            "department": c.get("department"),
        }
    return idx


def transition_plausibility(cam_a, cam_b, elapsed_s, cams, edges=None) -> dict:
    """Is a hop cam_a -> cam_b in elapsed_s seconds physically plausible?"""
    edges = _load_topology() if edges is None else edges
    out = {"elapsed_s": round(elapsed_s, 1), "distance_km": None,
           "expected_s": None, "source": None, "plausible": True, "reason": ""}

    if elapsed_s < -1.0:
        out.update(plausible=False, reason="out-of-order timestamps")
        return out

    edge = edges.get(frozenset((cam_a, cam_b)))
    if edge is not None:
        lo, hi = edge
        out.update(source="config", expected_s=[round(lo, 1), round(hi, 1)],
                   plausible=(lo <= elapsed_s <= hi),
                   reason="within configured window" if lo <= elapsed_s <= hi else "outside configured window")
        return out

    a, b = cams.get(cam_a), cams.get(cam_b)
    if not a or not b or a.get("lat") is None or b.get("lat") is None:
        out.update(source="unknown", reason="no coordinates to check")
        return out

    d = _haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    out["distance_km"] = round(d, 2)
    if cam_a == cam_b or d < _COLOCATED_KM:
        out.update(source="geo", reason="same / co-located camera")
        return out

    lo = (d / _SPEED_MAX_KMH * 3600.0) / _SLACK
    hi = (d / _SPEED_MIN_KMH * 3600.0) * _SLACK
    out["expected_s"] = [round(lo, 1), round(hi, 1)]
    out["source"] = "geo"
    if elapsed_s < lo:
        out.update(plausible=False, reason="too fast for the distance")
    elif elapsed_s > hi:
        out.update(plausible=False, reason="gap too long for a single hop")
    else:
        out.update(plausible=True, reason="plausible transit time")
    return out


def _reid_hop_ok(candidate, seeds, cams, edges) -> bool:
    """A Re-ID-fused (plate-less) sighting is only accepted if it forms a
    plausible transition with the temporally-nearest confirmed sighting."""
    nearest = min(seeds, key=lambda s: abs(s["ts_epoch"] - candidate["ts_epoch"]))
    elapsed = abs(candidate["ts_epoch"] - nearest["ts_epoch"])
    return transition_plausibility(nearest["camera_id"], candidate["camera_id"], elapsed, cams, edges)["plausible"]


def reconstruct_route(plate, reid_threshold=_DEFAULT_REID_THRESHOLD,
                      fuse_reid=True, use_topology=True) -> dict:
    norm = normalize_plate(plate)
    result = {"plate": norm, "plate_query": plate, "stops": [], "meta": {}}
    if len(norm) < 3:
        return result

    sightings = route_store.all_sightings(include_embeddings=True)
    cams = _camera_index()
    edges = _load_topology()

    seeds = [s for s in sightings if s["plate_normalized"] == norm]
    if not seeds:
        result["meta"] = {"found": False}
        return result

    chosen = [{**s, "_source": "anpr", "_sim": None} for s in seeds]
    chosen_ids = {s["id"] for s in seeds}

    # ── Re-ID fusion ──
    if fuse_reid:
        seed_embs = [e for e in (embedding_from_blob(s["embedding"]) for s in seeds) if e is not None]
        if seed_embs:
            for s in sightings:
                if s["id"] in chosen_ids or s["plate_normalized"] == norm:
                    continue
                e = embedding_from_blob(s["embedding"])
                if e is None:
                    continue
                sims = [_cosine_similarity(e, se) for se in seed_embs if se.shape == e.shape]
                if not sims:
                    continue
                sim = max(sims)
                if sim < reid_threshold:
                    continue
                if use_topology and not _reid_hop_ok(s, seeds, cams, edges):
                    continue
                chosen.append({**s, "_source": "reid", "_sim": round(sim, 3), "plate_normalized": norm})
                chosen_ids.add(s["id"])

    chosen.sort(key=lambda s: (s["ts_epoch"], s["id"]))
    stops = _collapse_stops(chosen, cams)
    _annotate_transitions(stops, cams, edges if use_topology else {})
    result["stops"] = stops
    result["meta"] = _route_meta(stops)
    return result


def _collapse_stops(chosen, cams) -> list[dict]:
    """Merge consecutive same-camera sightings into a single stop."""
    stops = []
    for s in chosen:
        info = cams.get(s["camera_id"], {})
        if stops and stops[-1]["camera_id"] == s["camera_id"]:
            # extend dwell of the current stop
            stops[-1]["departure_ts_epoch"] = max(stops[-1]["departure_ts_epoch"], s["last_ts_epoch"])
            if stops[-1]["plate_source"] == "reid" and s["_source"] == "anpr":
                stops[-1]["plate_source"] = "anpr"  # prefer a confirmed read for the stop
            continue
        stops.append({
            "seq": len(stops) + 1,
            "camera_id": s["camera_id"],
            "camera_name": info.get("name"),
            "department": info.get("department"),
            "lat": info.get("lat"),
            "lon": info.get("lon"),
            "ts_epoch": s["ts_epoch"],
            "ts_iso": s["ts_iso"],
            "departure_ts_epoch": s["last_ts_epoch"],
            "tracker_id": s["tracker_id"],
            "plate_source": s["_source"],
            "reid_similarity": s["_sim"],
            "sighting_id": s["id"],
        })
    for i, st in enumerate(stops):
        st["seq"] = i + 1
    return stops


def _annotate_transitions(stops, cams, edges) -> None:
    for i in range(1, len(stops)):
        prev, cur = stops[i - 1], stops[i]
        elapsed = cur["ts_epoch"] - prev["departure_ts_epoch"]
        cur["transition"] = transition_plausibility(prev["camera_id"], cur["camera_id"], elapsed, cams, edges)
    if stops:
        stops[0]["transition"] = None


def _route_meta(stops) -> dict:
    if not stops:
        return {"found": False}
    cameras = {s["camera_id"] for s in stops}
    by_source = {"anpr": 0, "reid": 0}
    for s in stops:
        by_source[s["plate_source"]] = by_source.get(s["plate_source"], 0) + 1
    total_km = sum(
        s["transition"]["distance_km"]
        for s in stops[1:]
        if s.get("transition") and s["transition"].get("distance_km") is not None
    )
    all_plausible = all(s["transition"]["plausible"] for s in stops[1:] if s.get("transition"))
    return {
        "found": True,
        "stop_count": len(stops),
        "camera_count": len(cameras),
        "by_source": by_source,
        "total_distance_km": round(total_km, 2),
        "span_s": round(stops[-1]["ts_epoch"] - stops[0]["ts_epoch"], 1),
        "all_transitions_plausible": all_plausible,
    }
