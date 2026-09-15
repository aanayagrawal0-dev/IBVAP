import { API_BASE } from "@/lib/config";
import type { Severity } from "@/lib/mock-data";

export interface WatchlistEntry {
  id: number;
  kind: string;
  plate_normalized: string;
  plate_display: string;
  label: string | null;
  severity: Severity;
  active: boolean;
  added_at: number;
}

async function parseError(res: Response, fallback: string): Promise<string> {
  const data = await res.json().catch(() => ({}));
  return (data && (data.detail as string)) || `${fallback} (${res.status})`;
}

export async function fetchWatchlist(): Promise<WatchlistEntry[]> {
  const res = await fetch(`${API_BASE}/api/watchlist`);
  if (!res.ok) throw new Error(`Failed to load watchlist (${res.status})`);
  return (await res.json()).entries as WatchlistEntry[];
}

export async function addPlate(
  plate: string,
  label?: string,
  severity: Severity = "critical"
): Promise<WatchlistEntry> {
  const res = await fetch(`${API_BASE}/api/watchlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plate, label, severity }),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to add plate"));
  return res.json();
}

export async function setActive(id: number, active: boolean): Promise<WatchlistEntry> {
  const res = await fetch(`${API_BASE}/api/watchlist/${id}/active`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active }),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to update entry"));
  return res.json();
}

export async function removeEntry(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/watchlist/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await parseError(res, "Failed to delete entry"));
}

export interface SimulateResult {
  matched: boolean;
  plate: string;
  event_id?: number;
  severity?: string;
}

/** Demo/test hook: pretend a camera's ANPR just read `plate`, running the
 * real watchlist check + alert path on the backend. */
export async function simulateSighting(cameraId: string, plate: string): Promise<SimulateResult> {
  const res = await fetch(`${API_BASE}/api/watchlist/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ camera_id: cameraId, plate }),
  });
  if (!res.ok) throw new Error(await parseError(res, "Simulation failed"));
  return res.json();
}
