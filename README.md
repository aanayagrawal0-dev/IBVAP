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
- **Video sources** — a recorded file (the default, for zero-setup demos), a laptop/USB webcam,
  or a real RTSP camera, switchable via one environment variable.
- **Simulated thermal / night-vision toggle** — a false-color heat-map overlay for low-light
  viewing, applied server-side and clearly labeled as simulated (there's no real IR sensor
  involved).
- **Live dashboard** — a Next.js frontend ("SENTINEL-X") with a live annotated video feed, a
  real-time alert feed over WebSocket, a zone-drawing tool, an event history table, and an
  analytics view.
- **Operator login gate** — a lightweight, demo-grade sign-in screen so the dashboard isn't
  wide open to anyone at the keyboard.

## What's mocked or not wired up yet

Being upfront about this so it doesn't surprise anyone during a demo or a judge's questions:

- The **Live Feed** page is the only page connected to the real backend (real MJPEG stream,
  real WebSocket alerts). It falls back to generated mock alerts after a few seconds if the
  backend isn't reachable, so the UI still demos standalone.
- **Zone Config**, **History**, and **Analytics** pages currently run on mock/static data —
  the polygon-drawing tool doesn't yet push a new zone to the backend (the backend's zone is
  hardcoded in `src/api_server.py`), and there's no persistent event log/database behind the
  history table yet.
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
    zones.py             — polygon zone + debounced enter/exit events
    pipeline.py           — wires the above together; run() writes to file, stream() serves live
    api_server.py          — FastAPI bridge: MJPEG stream, alerts WebSocket, thermal toggle
  tests/                    — smoke tests + synthetic demo clip generator
  sample_data/               — demo video clip + stills
  models/                     — YOLOv8 weights

frontend/
  app/                        — pages: /login, /live, /zone-config, /history, /analytics
  components/                  — Sidebar, VideoPanel, AppShell (auth guard), alert list, etc.
  lib/                          — config.ts (backend URL), auth.ts (demo login), mock-data.ts
```

## Running it

### Backend

```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -m uvicorn src.api_server:app --port 8000
```

Optional: pick a video source before starting (defaults to the bundled recorded clip if unset):

```powershell
$env:IBVAP_VIDEO_SOURCE = "0"                      # laptop/USB webcam
$env:IBVAP_VIDEO_SOURCE = "rtsp://192.168.1.50/..."  # real IP camera
$env:IBVAP_VIDEO_SOURCE = "sample_data/your_clip.mp4"  # your own recorded clip
```

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

## Tech stack

- **Backend**: Python, OpenCV, YOLOv8 (Ultralytics), ByteTrack (`supervision`), FastAPI, uvicorn
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
