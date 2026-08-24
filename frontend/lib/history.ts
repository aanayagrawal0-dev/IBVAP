import { API_BASE } from "@/lib/config";
import type { Severity } from "@/lib/mock-data";

export interface HistoryEvent {
  id: number;
  timestamp: string;
  camera: string;
  eventType: string;
  description: string;
  trackerId: string;
  severity: Severity;
  thumbnailUrl: string | null;
}

export interface HistoryFilters {
  cameraId?: string;
  severity?: Severity | "all";
  sinceHours?: number;
  limit?: number;
  offset?: number;
}

function buildParams(filters: HistoryFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.cameraId && filters.cameraId !== "all") params.set("camera_id", filters.cameraId);
  if (filters.severity && filters.severity !== "all") params.set("severity", filters.severity);
  if (filters.sinceHours) params.set("since_hours", String(filters.sinceHours));
  if (filters.limit) params.set("limit", String(filters.limit));
  if (filters.offset) params.set("offset", String(filters.offset));
  return params;
}

/** Real, database-backed event history — replaces what used to be a
 * hardcoded array. Throws on a network/backend failure so the page can
 * show an honest "couldn't reach the history service" state instead of
 * silently falling back to fake data. */
export async function getHistory(
  filters: HistoryFilters = {}
): Promise<{ events: HistoryEvent[]; total: number }> {
  const res = await fetch(`${API_BASE}/api/history?${buildParams(filters)}`);
  if (!res.ok) throw new Error(`Failed to load history (${res.status})`);
  return res.json();
}

export function thumbnailUrl(event: HistoryEvent): string | null {
  return event.thumbnailUrl ? `${API_BASE}${event.thumbnailUrl}` : null;
}

/** Opens the same filtered event log as a CSV download, generated
 * server-side from the real database — used by History's "Export Log". */
export function exportHistoryCsvUrl(filters: HistoryFilters = {}): string {
  return `${API_BASE}/api/history/export.csv?${buildParams(filters)}`;
}

/** On-demand Gemini explanation for one event — only called the first time
 * a History row is expanded. The backend caches the result against the
 * event, so a second expand (this session or a page reload) comes back
 * instantly with cached: true rather than calling Gemini again. Throws
 * with the backend's message on failure (no key configured, bad key,
 * network error, etc.) so the row can show exactly what went wrong. */
export async function explainEvent(
  id: number
): Promise<{ explanation: string; cached: boolean }> {
  const res = await fetch(`${API_BASE}/api/history/${id}/explain`, { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Failed to get explanation (${res.status})`);
  }
  return data;
}
