# IBVAP — Intelligent Border Video Analytics Platform

**Smart India Hackathon 2026**

IBVAP turns existing border-security CCTV into an AI-assisted surveillance system: it detects
people and vehicles in a video feed, tracks them across frames, raises alerts when something
crosses into a restricted "virtual fence" zone, and shows all of it on a live operations
dashboard — without needing new camera hardware.

## What's working right now

- **Detection & tracking** — Two-Model Top-Down Architecture: YOLOv11 (`ultralytics`) detects people/vehicles, and YOLOv11-Pose runs on cropped person bboxes;
  ByteTrack (`supervision`) assigns each one a persistent ID so it can be followed across
  frames instead of re-detected from scratch every time.
- **Real AI Night Vision Enhancement** — a Zero-DCE++ deep-learning illumination engine dynamically brightens pitch-black video feeds under `torch.no_grad()` in FP16 precision. Includes an automatic high-speed LAB-space CLAHE fallback for standard CPU environments.
- **Spatial-Temporal & Threat Behavior Rules** — geometric climbing/crawling pose checks (evaluating wrist-to-head and torso-to-knee ratios), loitering dwell timers (firing if a target remains in a zone >10s), and approach velocity vector checks.
- **Cross-Camera Target Re-Identification (Re-ID)** — an integrated `GlobalTargetRegistry` that uses sparse OSNet feature embeddings to match identities across disjointed camera feeds, backed by an automatic HSV color histogram fallback.
- **Hardware Acceleration** — automated hardware detection compiling models into TensorRT (FP16) `.engine` artifacts for NVIDIA CUDA or OpenVINO execution directories for Intel CPUs, protected by thread synchronization locks.
- **Virtual fence / zone intrusion** — a configurable polygon zone; entering/exiting it fires a
  debounced alert (a 3-frame confirmation window stops flicker at the polygon edge from
  spamming alerts).
- **Multiple independent live cameras** — each camera (CAM-01 through CAM-04, or more) runs its
  own detection pipeline in its own thread: its own video source (recorded file, laptop/USB
  webcam, or RTSP camera), its own tracker state, and its own zone list. CAM-01 defaults to your
  webcam so there's always one genuinely live feed; each camera's source is independently
  configurable via its own environment variable (see "Running it" below). Switching cameras in
  the Live Feed page shows that camera's actual video and actual zones — a camera with no
  source configured just shows the offline placeholder instead of hanging.
- **Live dashboard** — a Next.js frontend ("PRAHARI") with a live annotated video feed, a
  real-time alert feed over WebSocket, a zone-drawing tool, an event history table, and an
  analytics view.
- **Event history with real thumbnails, backed by a database** — every zone-crossing event
  (across every camera) is written to a SQLite database (`backend/history.db`) the instant it
  fires, along with a JPEG thumbnail of the actual annotated frame that triggered it (boxes and
  zone overlay included) saved to `backend/history_thumbnails/`. The History page reads this
  live through `GET /api/history` — real pagination, and severity/date-range filters that
  actually query the database — instead of a hardcoded list. **Export Log** downloads the
  currently filtered log as a CSV, generated server-side from the same database.
- **Operational report generation** — **Generate Report** on the Analytics page calls
  `GET /api/analytics/report.pdf`, which builds a PDF (summary counts by severity/camera, time
  range covered, and the recent event log) straight from the history database and downloads it.
- **Gemini-powered event explanations** — click any row in the History table and it expands to
  show a plain-language explanation from Gemini 2.5 Flash: what the event means, what's visible in the
  captured thumbnail, and whether the assigned severity looks reasonable. Generated on demand
  (nothing is called automatically for every logged event) and cached in the database once
  generated, so re-expanding the same row — or a teammate opening it later — doesn't re-spend API
  quota. Needs your own `GEMINI_API_KEY` (see "Running it" below); without one, expanding a row
  shows a clear "not configured" message instead of failing silently.
- **Multi-zone, multi-camera zone configuration** — draw any number of restricted-area polygons
  per camera (not just one), saved to disk (`backend/zone_config.json`) and loaded through
  `GET/PUT /api/zones/{camera_id}`. Whichever camera is currently live picks up a saved change
  immediately — no server restart — and every other camera's zones sit ready for the moment
  that feed is connected. Zones are stored as **percentages (0-100) of frame width/height**, not
  pixels, and converted to each frame's actual pixel coordinates on the fly — so a zone drawn
  against the Zone Config preview lines up correctly on the real feed no matter that camera's
  native resolution (a 960x540 recorded clip, a 1280x720 webcam, or anything else).
- **Alert filtering by camera** — the Live Alerts panel has a row of filter chips ("All" plus one
  per camera) above the list, so a busy multi-camera feed doesn't turn into a wall of alerts.
  Select one camera to see only its alerts, select several for a combined view, or leave "All"
  selected (the default) to see everything.
- **Operator login gate** — a lightweight, demo-grade sign-in screen so the dashboard isn't
  wide open to anyone at the keyboard.

## What's mocked or not wired up yet

Being upfront about this so it doesn't surprise anyone during a demo or a judge's questions:

- The **Live Feed**, **Zone Config**, and **History** pages are connected to the real backend
  (real MJPEG stream, real WebSocket alerts, real zone persistence, real event database). Live
  Feed falls back to generated mock alerts after a few seconds if the backend isn't reachable, so
  the UI still demos standalone; History instead shows an explicit "couldn't reach the history
  service" message rather than silently showing fake data.
- The **Analytics** page's summary stat cards, 30-day breach trend chart, and activity heatmap
  are still mock/static data — genuine trend analysis needs history to accumulate over real time,
  which a fresh demo database won't have yet. **Generate Report** is the one part of that page
  wired to the real database (see above) — it exports whatever has actually been logged so far,
  which may be a short list on a freshly started backend.
- Only cameras with a **source actually configured** run live — CAM-01/02/03 have defaults
  (webcam / bundled clip / bundled clip), CAM-04 doesn't, so it shows the offline placeholder
  until you give it a source. The repo ships with several recorded demo clips in `sample_data/`, so CAM-02 and CAM-03
  can play them (as fully independent decodes) — point them at your own footage or different demo clips via their env vars for visually distinct feeds. Running several concurrent YOLO pipelines is real CPU work; if your machine struggles, disable a camera by clearing its env var rather than running all four.
- **ANPR (license plate recognition)** and **face detection** are on the roadmap but not built.
- The **login gate is client-side only** — credentials are hardcoded in the frontend bundle and
  the backend API itself doesn't check any token. It's a convenience gate for a demo, not
  production security.

## Architecture

```text
                     ┌────────────────────────┐
  Video source ───▶ │   backend (Python)     │
  (file / webcam /  │  ingestion → YOLOv11 →  │
   RTSP)            │  ByteTrack → zone logic │
                    │  FastAPI bridge server  │
                    └───────────┬────────────┘
                                │
                MJPEG stream    │   WebSocket alerts
                (/api/stream)   │   (/ws/alerts)
                                ▼
                    ┌────────────────────────┐
                    │  frontend (Next.js)    │
                    │  PRAHARI dashboard     │
                    └────────────────────────┘
```

See `IBVAP_Technical_Architecture.md` for the fuller design writeup.

## Project structure

```text
backend/
  src/
    ingestion.py      — video source wrapper (file / webcam / RTSP), auto-reconnect
    detector.py        — YOLOv11 and YOLOv11-Pose Two-Model detector, filters to person/vehicle classes
    tracker.py          — ByteTrack wrapper, keeps trajectory history
    zero_dce.py          — Zero-DCE++ neural illumination network & CLAHE fallback
    reid.py              — TargetEmbedder & cross-camera GlobalTargetRegistry
    export_models.py      — Hardware auto-detection & TensorRT/OpenVINO exporter
    zones.py             — polygon zone + debounced enter/exit events, multi-zone ZoneManager
    zone_store.py          — JSON-file persistence for per-camera zone configs
    history_store.py         — SQLite persistence for the event history log + thumbnails
    gemini_explainer.py        — calls Gemini to explain one history event, on demand
    pipeline.py                  — wires the above together; run() writes to file, stream() serves live
    api_server.py                  — FastAPI bridge: MJPEG stream, alerts WebSocket,
                                      zone config CRUD, history query/export, PDF report, Gemini explain
  tests/                          — smoke tests + synthetic demo clip generator
  sample_data/                     — bundled demo video clips (drone, fence climbing, etc.)
  models/                           — YOLOv11 and YOLOv11-Pose weights / TensorRT engines
  zone_config.json                   — saved zones per camera (created on first run)
  history.db                          — event history database (created on first run)
  history_thumbnails/                  — one JPEG per logged event (created on first run)

frontend/
  app/                        — pages: /login, /live, /zone-config, /history, /analytics
  components/                  — Sidebar, VideoPanel, AppShell (auth guard), alert list, etc.
  lib/                          — config.ts (backend URL), auth.ts (demo login), zones.ts (zone
                                   config API client), history.ts (history query/export client),
                                   mock-data.ts
```

## Running it

### Backend

```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -m uvicorn src.api_server:app --port 8000
```

Each camera has its own source, set independently via its own env var before starting the
server — `IBVAP_CAM01_SOURCE`, `IBVAP_CAM02_SOURCE`, `IBVAP_CAM03_SOURCE`, `IBVAP_CAM04_SOURCE`.
Leave one unset/empty and that camera just isn't run (offline placeholder in the UI). Defaults:
CAM-01 → your webcam (`0`), CAM-02/03 → the bundled demo clips, CAM-04 → off.

```powershell
# PowerShell (Windows)
$env:IBVAP_CAM01_SOURCE = "0"                                     # laptop/USB webcam
$env:IBVAP_CAM02_SOURCE = "sample_data/demo_fence_climb.mp4"      # bundled fence climbing demo
$env:IBVAP_CAM03_SOURCE = "sample_data/demo_drone.mp4"            # bundled drone view demo
$env:IBVAP_CAM04_SOURCE = ""                                      # leave off / clear to disable
```

```zsh
# zsh/bash (macOS/Linux)
export IBVAP_CAM01_SOURCE="0"
export IBVAP_CAM02_SOURCE="sample_data/demo_fence_climb.mp4"
export IBVAP_CAM03_SOURCE="sample_data/demo_drone.mp4"
unset IBVAP_CAM04_SOURCE
```

A bare integer (`"0"`, `"1"`, ...) means a webcam device index, an `rtsp://` URL means a real IP
camera, and anything else is treated as a file path.

### Gemini event explanations (optional)

Only needed if you want to use the "click a History row to expand its AI analysis" feature —
everything else works fine without it. Get a key from
[Google AI Studio](https://aistudio.google.com/apikey) — it should look like `AIza...`, not a
Google Cloud OAuth client ID or a service-account JSON file, which need a different setup
entirely and won't work here.

**Easiest: a `.env` file**, so you set it once instead of `export`-ing it in every new terminal:

```zsh
cd backend
cp .env.example .env
```

Then open `.env` and paste your key in after `GEMINI_API_KEY=`. It's loaded automatically the
next time you start the backend — `.env` is already covered by `.gitignore` conventions in this
project's setup guidance, so make sure it's excluded in yours too before committing (see the
`.env.example` file itself, which has no real secret in it and is safe to commit as a template
for teammates).

**Or, the manual way** — exporting it in the same terminal you launch the backend from, every
time:

```zsh
# zsh/bash (macOS/Linux)
export GEMINI_API_KEY="your-key-here"
```

```powershell
# PowerShell (Windows)
$env:GEMINI_API_KEY = "your-key-here"
```

A shell-exported value always wins over `.env` if both are set, so switching keys temporarily
(e.g. to test a different one) doesn't require editing the file.

Without a key at all, expanding a row shows a clear "GEMINI_API_KEY is not set" message instead
of failing silently — nothing else on the page is affected. If you have a key set and still get
an error when expanding a row, the message includes a specific hint for the failure Google
returned (wrong key type, quota exceeded, etc.) rather than just the raw error text. The model
used defaults to `gemini-2.5-flash`; override it with `GEMINI_MODEL` (in `.env` or exported) if
you want a different one. Each explanation is generated once (on first expand) and cached in
`history.db`, so it only calls the API once per event no matter how many times it's viewed
afterward.

### Frontend

```powershell
cd frontend
npm install   # first time only
npm run dev
```

Open `http://localhost:3000`. You'll land on the login screen first.

### Demo login

| Operator ID | Passcode |
|---|---|
| `OP-774` | `prahari2026` |
| `OP-118` | `border-watch` |

(Defined in `frontend/lib/auth.ts` — change or add operators there.)

### Zone configuration

Open **Zone Config** in the sidebar, pick a camera from the dropdown (CAM-01 through CAM-04 —
add more by picking a new ID; nothing needs pre-registering), click **New Zone**, then click on
the frame to place vertices (3+ points), and **Save All**. Repeat "New Zone" as many times as
you want per camera — each gets its own color and its own name. Saving is per-camera: switching
cameras loads that camera's own zone list, completely separate from the others.

Any camera that's actually running (has a source configured — see above) hot-reloads immediately
when you save its zones, no restart needed. Saving zones for a camera with no source configured
still works and persists — they'll apply the moment you give that camera a source and restart
the backend.

### History & reports

Every zone-crossing event, from every running camera, is logged automatically — nothing to turn
on. Open **History** to see it: a real, paginated table backed by `backend/history.db`, complete
with a thumbnail of the actual frame that triggered each event. **Date Range** and **Severity**
filter by re-querying the database (not filtering an in-page list), and **Export Log** downloads
whatever's currently filtered as a CSV.

**Generate Report** on the **Analytics** page produces a PDF — summary counts by severity and by
camera, the time range covered, and a table of recent events — built the moment you click it from
whatever's in the database so far. On a freshly started backend with no events yet, it still
generates, just with an empty/near-empty log; let the system run for a bit (or walk in front of
the webcam) to see a fuller report.

Click any row to expand it and get a Gemini explanation of that specific event — see "Gemini
event explanations" above for the one-time API key setup.

If you already had a `venv` set up before this update, run `pip install -r requirements.txt`
again — recent features added three new dependencies (`fpdf2` for the PDF report, `google-genai`
for the Gemini explanations, `python-dotenv` for the `.env` file support above).

## Tech stack

- **Backend**: Python, OpenCV, YOLOv11 and YOLOv11-Pose (Ultralytics), ByteTrack (`supervision`), FastAPI, uvicorn,
  SQLite (event history), fpdf2 (PDF report generation), google-genai (Gemini explanations),
  python-dotenv (`.env` config)
- **Frontend**: Next.js 14 (App Router), TypeScript, Tailwind CSS, framer-motion, recharts
- **Bridge**: MJPEG over HTTP for video, WebSocket for alert events

## Known caveats

- The bundled clips in `sample_data/` (e.g., `demo_fence_climb.mp4`, `demo_drone.mp4`) are provided for testing different surveillance angles. Use webcam mode (see above) for a live demonstration.
- `npm audit` flags some known Next.js 14.x advisories that are primarily server-side attack
  vectors (middleware, server actions); given this runs on localhost for a demo, we judged the
  risk of a mid-hackathon major-version jump to Next 16 higher than the risk of the advisories
  themselves. Revisit before any real deployment.
- Gemini explanations call out to Google's servers, so expanding a row takes a couple of seconds
  the first time (cached instantly after) and needs internet access + a valid, unexhausted
  `GEMINI_API_KEY` — offline, or over quota, it'll show an error with a Retry link rather than
  fail silently.