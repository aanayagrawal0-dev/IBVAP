import { API_BASE } from "@/lib/config";
import { apiFetch, withToken } from "@/lib/auth";

export interface Transition {
  elapsed_s: number;
  distance_km: number | null;
  expected_s: [number, number] | null;
  source: "geo" | "config" | "unknown" | null;
  plausible: boolean;
  reason: string;
}

export interface RouteStop {
  seq: number;
  camera_id: string;
  camera_name: string | null;
  department: string | null;
  lat: number | null;
  lon: number | null;
  ts_epoch: number;
  ts_iso: string;
  departure_ts_epoch: number;
  tracker_id: number;
  plate_source: "anpr" | "reid";
  reid_similarity: number | null;
  sighting_id: number;
  transition: Transition | null;
}

export interface RouteMeta {
  found: boolean;
  stop_count?: number;
  camera_count?: number;
  by_source?: { anpr: number; reid: number };
  total_distance_km?: number;
  span_s?: number;
  all_transitions_plausible?: boolean;
}

export interface RouteResult {
  plate: string;
  plate_query: string;
  stops: RouteStop[];
  meta: RouteMeta;
}

export interface PlateSummary {
  plate: string;
  plate_display: string;
  sightings: number;
  cameras: number;
  first_ts: number;
  last_ts: number;
}

export async function fetchRoutePlates(): Promise<PlateSummary[]> {
  const res = await apiFetch(`${API_BASE}/api/route/plates`);
  if (!res.ok) throw new Error(`Failed to load plates (${res.status})`);
  return (await res.json()).plates as PlateSummary[];
}

export async function fetchRoute(
  plate: string,
  opts: { fuseReid?: boolean; useTopology?: boolean } = {}
): Promise<RouteResult> {
  const params = new URLSearchParams();
  if (opts.fuseReid === false) params.set("fuse_reid", "false");
  if (opts.useTopology === false) params.set("use_topology", "false");
  const q = params.toString();
  const res = await apiFetch(
    `${API_BASE}/api/route/${encodeURIComponent(plate)}${q ? `?${q}` : ""}`
  );
  if (!res.ok) throw new Error(`Failed to reconstruct route (${res.status})`);
  return res.json();
}

/** Demo/test hook: record a vehicle sighting as if a camera read this plate. */
export async function injectSighting(cameraId: string, plate: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/route/sighting`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ camera_id: cameraId, plate }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error((data && (data.detail as string)) || `Failed to record sighting (${res.status})`);
  }
}
