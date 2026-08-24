# IBVAP — Intelligent Border Video Analytics Platform

**Smart India Hackathon 2026**

IBVAP turns existing border-security CCTV into an AI-assisted surveillance system: it detects
people and vehicles in a video feed, tracks them across frames, raises alerts when something
crosses into a restricted "virtual fence" zone, and shows all of it on a live operations
dashboard — without needing new camera hardware.

## What's working right now

- **Detection & tracking** — YOLOv8 (`ultralytics`) detects people/vehicles per frame;
  ByteTrack (`supervision`) assigns each one a persistent ID so it can be followed across
  frames instead of re-detected from scratch every time.
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
- **Simulated thermal / night-vision toggle** — a false-color heat-map overlay for low-light
  viewing, applied server-side and clearly labeled as simulated (there's no real IR sensor
  involved).
- **Live dashboard** — a Next.js frontend ("SENTINEL-X") with a live annotated video feed, a
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
  until you give it a source. The repo only ships one recorded demo clip, so CAM-02 and CAM-03
  both play it by default (as two fully independent decodes, not a shared/mirrored stream) —
  point them at your own footage via their env vars for visually distinct feeds. Running several
  concurrent YOLO pipelines is real CPU work; if your machine struggles, disable a camera by
  clearing its env var rather than running all four.
- **ANPR (license plate recognition)**, **face detection**, **suspicious-activity rules**
  (loitering, approach velocity), and a **dedicated night-time image-enhancement stage** (as
  opposed to the simulated thermal *display* toggle) are on the roadmap but not built.
- The **login gate is client-side only** — credentials are hardcoded in the frontend bundle and
  the backend API itself doesn't check any token. It's a convenience gate for a demo, not
  production security.

## Architecture

```
                    ┌────────────────────────┐
  Video source ───▶ │   backend (Python)     │
  (file / webcam /  │  ingestion → YOLOv8 →   │
   RTSP)            │  ByteTrack → zone logic │
                    │  FastAPI bridge server  │
                    └───────────┬────────────┘
                                │
                MJPEG stream    │   WebSocket alerts
                (/api/stream)   │   (/ws/alerts)
                                ▼
                    ┌────────────────────────┐
                    │  frontend (Next.js)    │
                    │  SENTINEL-X dashboard  │
                    └────────────────────────┘
```

See `IBVAP_Technical_Architecture.md` for the fuller design writeup.

## Project structure

```
backend/
  src/
    ingestion.py      — video source wrapper (file / webcam / RTSP), auto-reconnect
    detector.py        — YOLOv8 wrapper, filters to person/vehicle classes
    tracker.py          — ByteTrack wrapper, keeps trajectory history
    zones.py             — polygon zone + debounced enter/exit events, multi-zone ZoneManager
    zone_store.py          — JSON-file persistence for per-camera zone configs
    history_store.py         — SQLite persistence for the event history log + thumbnails
    pipeline.py                — wires the above together; run() writes to file, stream() serves live
    api_server.py                — FastAPI bridge: MJPEG stream, alerts WebSocket, thermal toggle,
                                    zone config CRUD, history query/export, PDF report generation
  tests/                          — smoke tests + synthetic demo clip generator
  sample_data/                     — demo video clip + stills
  models/                           — YOLOv8 weights
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
CAM-01 → your webcam (`0`), CAM-02/03 → the bundled demo clip, CAM-04 → off.

```powershell
# PowerShell (Windows)
$env:IBVAP_CAM01_SOURCE = "0"                          # laptop/USB webcam
$env:IBVAP_CAM02_SOURCE = "rtsp://192.168.1.50/..."    # a real IP camera
$env:IBVAP_CAM03_SOURCE = "sample_data/your_clip.mp4"  # your own recorded clip
$env:IBVAP_CAM04_SOURCE = ""                           # leave off / clear to disable
```

```zsh
# zsh/bash (macOS/Linux)
export IBVAP_CAM01_SOURCE="0"
export IBVAP_CAM02_SOURCE="rtsp://192.168.1.50/..."
export IBVAP_CAM03_SOURCE="sample_data/your_clip.mp4"
unset IBVAP_CAM04_SOURCE
```

A bare integer (`"0"`, `"1"`, ...) means a webcam device index, an `rtsp://` URL means a real IP
camera, and anything else is treated as a file path.

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
| `OP-774` | `sentinel2026` |
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

If you already had a `venv` set up before this update, run `pip install -r requirements.txt`
again — the report feature added one new dependency (`fpdf2`).

## Tech stack

- **Backend**: Python, OpenCV, YOLOv8 (Ultralytics), ByteTrack (`supervision`), FastAPI, uvicorn,
  SQLite (event history), fpdf2 (PDF report generation)
- **Frontend**: Next.js 14 (App Router), TypeScript, Tailwind CSS, framer-motion, recharts
- **Bridge**: MJPEG over HTTP for video, WebSocket for alert events

## Known caveats

- The bundled `sample_data/synthetic_test_clip.mp4` is a synthetic stand-in built by
  panning across stock stills — not real CCTV footage. Use webcam mode (see above) for a more
  convincing live demo.
- The thermal toggle is a false-color visible-light transform (`cv2.applyColorMap`), not a
  real infrared sensor — it's labeled "SIMULATED" everywhere it appears in the UI and API.
- `npm audit` flags some known Next.js 14.x advisories that are primarily server-side attack
  vectors (middleware, server actions); given this runs on localhost for a demo, we judged the
  risk of a mid-hackathon major-version jump to Next 16 higher than the risk of the advisories
  themselves. Revisit before any real deployment.
