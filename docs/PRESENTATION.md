# PRAHARI — Solution Deck

*Statewide CCTV Analytics for Gujarat State Police. Slide-by-slide outline (`---` = slide break).
Speaker notes in italics.*

---

## 1 — PRAHARI
### Turning Gujarat's existing CCTV into one intelligent, searchable, accountable network.
Built on the IBVAP analytics core · Models 1 + 2, federation-ready.

*One line: we don't ask for new cameras — we make the ones already installed think.*

---

## 2 — The problem
- Tens of thousands of cameras, **many departments, many vendors/protocols**, no common brain.
- Footage is reviewed **after** the fact — hours of manual scrubbing per case.
- Cameras fail silently (covered/defocused) and no one knows.
- No unified registry, no cross-camera search, weak access control & auditability.

---

## 3 — What we built (Phases 0–8)
- **Camera Registry + GIS** — onboard any camera at runtime (manual/CSV/API), plotted on a map.
- **Heterogeneous ingestion** — RTSP, ONVIF, HTTP-MJPEG, recorded clips, one contract.
- **Watchlist engine** — continuous plate cross-referencing → real-time alerts.
- **Cross-camera route reconstruction** — ANPR + appearance Re-ID, topology-checked, on a map.
- **Security & privacy** — real auth, department RBAC, audit log, bystander redaction.
- **Bonus analytics** — tampering/health, behavior (loiter/altercation/snatch), weapon + face (verify).

*All tested; foundation timestamp/DB reliability fixed first (Phase 0).*

---

## 4 — Architecture (Model 1 + Model 2)
Cameras → **ingestion adapters** → per-camera CV pipeline (YOLO11 + pose, ByteTrack + Re-ID,
zones, ANPR, analytics) → **FastAPI** (REST + WebSocket + MJPEG) → **Next.js dashboard**;
one SQLite/WAL store (registry, events, watchlist, sightings, users, audit).

*Direct per-camera ingestion = Model 2. The adapter layer is the seam a future federation tier
(Model 3) plugs into.* → see [HLD.md](HLD.md).

---

## 5 — Live test case: it all works end to end
1. Onboard heterogeneous cameras via the registry — **no restart**.
2. Track a vehicle by registration number given at evaluation.
3. Alerts + full **timestamped, location-wise movement history**.
4. Visualize on **GIS**.
5. **Searchable** event history.
6. **Watchlist DB** — continuous cross-referencing → automatic real-time match alert.

*Every one of these maps to a built, tested capability.*

---

## 6 — Differentiator: cross-camera route reconstruction
- Query a plate → ordered, timestamped path across cameras, drawn on the map.
- **ANPR + Re-ID fusion**: cameras that couldn't read the plate still land on the route via
  appearance matching, with the plate backfilled.
- **Topology guard**: rejects physically impossible hops (teleports) using GIS-derived transit
  windows — the key accuracy lever for correlation.

---

## 7 — Differentiator: camera health & tampering
- GPU-free integrity check: **covered lens, defocus, camera moved** — detected instantly.
- Live demo: *cover a lens → alert fires immediately*; per-camera health on the dashboard.
- Solves the silent-failure problem at statewide scale.

---

## 8 — Security, privacy, auditability (built in)
- Real **backend sessions** (PBKDF2), **department RBAC** — a Traffic operator sees only Traffic
  cameras; admin sees all.
- **Audit log** — who viewed which camera, searched which plate, exported what.
- **Selective redaction** — exported clips auto-blur bystanders, keep the target visible.

---

## 9 — Demo script (8 steps)
1. Add a heterogeneous camera live → no restart.
2. GIS map with department/status layers.
3. Add a plate to the watchlist → trigger a live match → real-time alert.
4. Query a plate on 3+ cameras → reconstructed route on the map.
5. Cover a camera → tampering alert fires instantly.
6. Export a clip → bystanders redacted, target visible.
7. Show the audit entry for the search just run.
8. Close on the scale-up architecture (Model 3/4 as the evolution path).

---

## 10 — Scale to ~80,000 cameras
- **Edge-first processing**: analytics next to cameras; only events + on-demand clips travel up.
- Cuts statewide sustained uplink by **~99%+** (240 Gbps → <2 Gbps).
- Tiered: edge nodes → regional aggregators (event bus) → state DC + DR.

→ [SCALEUP.md](SCALEUP.md)

---

## 11 — Cost-benefit
- **Reuses installed cameras**, **open-source stack** (no per-seat/per-analytic licences).
- Cost shifts from recurring video backhaul to one-time edge compute.
- Benefits: faster investigations, proactive interception, silent-failure detection, compliance.

→ [COST_BENEFIT_AND_ROADMAP.md](COST_BENEFIT_AND_ROADMAP.md)

---

## 12 — Roadmap
- **Near term:** live ANPR at scale, weapon/face model fine-tuning, PostGIS, SSO.
- **Model 3 (future):** federation middleware / event bus — the adapter contract is already the seam.
- **Model 4 (future):** central VMS integration.

*Model 3/4 are explicitly future — today's build is Model 1 + Model 2, shaped for them.*

---

## 13 — Why us
- End-to-end **working system**, not slideware — every claim has a passing acceptance test.
- Honest engineering: graceful degradation, timestamp/DB integrity fixed first, weapon/face wired
  but not faked.
- **Integration-ready APIs** ([API.md](API.md)) + full design docs ([HLD.md](HLD.md)).

---

## 14 — Thank you
**PRAHARI — one intelligent, searchable, accountable CCTV network for Gujarat.**
Repo: `github.com/aanayagrawal0-dev/IBVAP` · Docs: [`docs/`](.)
