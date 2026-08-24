import { API_BASE } from "@/lib/config";

export interface Point {
  x: number; // percentage 0-100 of frame width
  y: number; // percentage 0-100 of frame height
}

export interface ZoneDef {
  name: string;
  polygon: Point[];
}

function toWire(zones: ZoneDef[]) {
  return zones.map((z) => ({
    name: z.name,
    polygon: z.polygon.map((p) => [p.x, p.y]),
  }));
}

function fromWire(raw: { name: string; polygon: number[][] }[]): ZoneDef[] {
  return raw.map((z) => ({
    name: z.name,
    polygon: z.polygon.map(([x, y]) => ({ x, y })),
  }));
}

export async function getZones(cameraId: string): Promise<ZoneDef[]> {
  const res = await fetch(`${API_BASE}/api/zones/${encodeURIComponent(cameraId)}`);
  if (!res.ok) throw new Error(`Failed to load zones (${res.status})`);
  const data = await res.json();
  return fromWire(data.zones ?? []);
}

/** Throws with the backend's validation message on 400 (duplicate name,
 * too few points, too many zones, etc.) so the caller can show it as-is. */
export async function saveZones(
  cameraId: string,
  zones: ZoneDef[]
): Promise<{ hotReloaded: boolean }> {
  const res = await fetch(`${API_BASE}/api/zones/${encodeURIComponent(cameraId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ zones: toWire(zones) }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Failed to save zones (${res.status})`);
  }
  return { hotReloaded: Boolean(data.hot_reloaded) };
}
