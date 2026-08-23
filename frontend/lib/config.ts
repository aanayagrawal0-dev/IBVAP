// Backend bridge server (FastAPI) location. Change this if the backend
// runs on a different host/port — e.g. when moving off localhost onto a
// real edge box.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
export const WS_ALERTS_URL =
  process.env.NEXT_PUBLIC_WS_ALERTS_URL ?? API_BASE.replace(/^http/, "ws") + "/ws/alerts";
