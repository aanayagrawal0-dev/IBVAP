# API Reference

PRAHARI/IBVAP exposes an integration-ready REST + WebSocket API (FastAPI). The live,
auto-generated spec is always at **`/docs`** (Swagger UI) and **`/openapi.json`**; a snapshot is
committed at [`openapi.json`](openapi.json). Base URL in dev: `http://localhost:8000`.

## Authentication

1. `POST /api/auth/login` with `{ "username", "password" }` → `{ token, expires_at, user }`.
2. Send the token on every protected call:
   - JSON/HTTP: header `Authorization: Bearer <token>`.
   - MJPEG stream, WebSocket, thumbnail & export links (can't set headers): query `?token=<token>`.

Roles: **viewer < operator < admin**. Reads need a valid session; writes need **operator**;
user management + audit need **operator**/**admin**. A non-admin only sees cameras in their own
**department**.

```bash
# login and capture a token
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -s localhost:8000/api/cameras -H "Authorization: Bearer $TOKEN"
```

## Endpoints

### Auth
| Method | Path | Role | Notes |
|---|---|---|---|
| POST | `/api/auth/login` | public | issue session token |
| POST | `/api/auth/logout` | any | revoke current token |
| GET | `/api/auth/me` | any | current user |
| GET | `/api/users` | admin | list users |

### System
| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/api/health` | public | liveness + per-camera worker status |

### Cameras (registry, stream, health)
| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/api/cameras` | any | department-scoped list (+ live status + health) |
| POST | `/api/cameras` | operator | add camera (starts worker, no restart) |
| GET | `/api/cameras/{id}` | any | one camera (dept-checked) |
| PUT | `/api/cameras/{id}` | operator | edit (restarts worker if source/enabled changed) |
| DELETE | `/api/cameras/{id}` | operator | remove (stops worker) |
| POST | `/api/cameras/import` | operator | CSV bulk import |
| GET | `/api/cameras/export.csv` | operator | CSV export |
| GET | `/api/stream/{id}` | any (`?token`) | MJPEG stream (dept-checked; audited) |
| GET / POST | `/api/thermal/{id}` | any / operator | night-vision toggle |

### Zones
| Method | Path | Role |
|---|---|---|
| GET | `/api/zones/{id}` | any |
| PUT | `/api/zones/{id}` | operator |

### Watchlist
| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/api/watchlist` | any | list entries |
| POST | `/api/watchlist` | operator | add plate |
| PUT | `/api/watchlist/{id}/active` | operator | activate/pause |
| DELETE | `/api/watchlist/{id}` | operator | remove |
| POST | `/api/watchlist/simulate` | operator | demo hook: fire a match without live OCR |

### Route Reconstruction
| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/api/route/plates` | any | plates with sightings |
| GET | `/api/route/{plate}` | any | reconstructed route (`?fuse_reid`, `?use_topology`); audited |
| POST | `/api/route/sighting` | operator | demo hook: inject a sighting |

### History
| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/api/history` | any | filter by camera/severity/time, paginated |
| GET | `/api/history/thumbnail/{id}` | any (`?token`) | event thumbnail JPEG |
| POST | `/api/history/{id}/explain` | operator | on-demand LLM explanation (cached) |
| GET | `/api/history/export.csv` | operator | CSV export |

### Analytics
| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/api/analytics/summary` | any | live stats + per-camera health (backs the dashboard) |
| GET | `/api/analytics/report.pdf` | any | operational PDF report |

### Audit & Export
| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/api/audit` | operator | audit log (filter by user/action) |
| POST | `/api/export/redacted-clip` | operator | privacy-redacted clip export |

### WebSocket
| Path | Auth | Notes |
|---|---|---|
| `/ws/alerts` | `?token` | real-time alert broadcast (zones, watchlist, tamper, behavior, weapon, face) |
