import { API_BASE } from "@/lib/config";

/** A camera as the backend registry (camera_store) returns it, with live
 * runtime status merged in by the API (streaming / connectivity). This is
 * now the source of truth for the camera list across the whole app —
 * replacing the old hardcoded mock-data list. */
export interface RegistryCamera {
  id: string;
  name: string;
  department: string | null;
  lat: number | null;
  lon: number | null;
  camera_type: string; // webcam | rtsp | onvif | mjpeg | file | none
  ownership: string | null;
  source_spec: string | null;
  status: string;
  storage_details: string | null;
  enabled: boolean;
  streaming: boolean;
  connectivity: "online" | "offline" | "disabled" | "no-source";
}

export type CameraStatus = "nominal" | "offline";

/** The lightweight shape the existing Live / Zone-config / History pages
 * consume for their camera pickers and thumbnails. */
export interface CameraOption {
  id: string;
  label: string;
  status: CameraStatus;
}

/** Offline safety-net so those pages still render a sensible list if the
 * backend is briefly unreachable during local dev. The registry is the real
 * source of truth; this is only a fallback, never authoritative. */
export const FALLBACK_CAMERAS: CameraOption[] = [
  { id: "CAM-01", label: "Ashram Road", status: "nominal" },
  { id: "CAM-02", label: "Gandhinagar Secretariat", status: "nominal" },
  { id: "CAM-03", label: "Surat Ring Road", status: "nominal" },
  { id: "CAM-04", label: "Vadodara Depot", status: "offline" },
];

export function toCameraOption(c: RegistryCamera): CameraOption {
  return {
    id: c.id,
    label: c.name ?? c.id,
    status: c.connectivity === "online" ? "nominal" : "offline",
  };
}

export async function fetchCameras(): Promise<RegistryCamera[]> {
  const res = await fetch(`${API_BASE}/api/cameras`);
  if (!res.ok) throw new Error(`Failed to load cameras (${res.status})`);
  const data = await res.json();
  return data.cameras as RegistryCamera[];
}

/** Fetch the registry and adapt to the CameraOption list the shared pages
 * use, falling back to FALLBACK_CAMERAS if the backend can't be reached. */
export async function fetchCameraOptions(): Promise<CameraOption[]> {
  try {
    const cams = await fetchCameras();
    return cams.length ? cams.map(toCameraOption) : FALLBACK_CAMERAS;
  } catch {
    return FALLBACK_CAMERAS;
  }
}

export interface CameraInput {
  id?: string;
  name?: string;
  department?: string;
  lat?: number | null;
  lon?: number | null;
  ownership?: string;
  source_spec?: string;
  storage_details?: string;
  enabled?: boolean;
}

async function parseError(res: Response, fallback: string): Promise<string> {
  const data = await res.json().catch(() => ({}));
  return (data && (data.detail as string)) || `${fallback} (${res.status})`;
}

export async function createCamera(payload: CameraInput): Promise<RegistryCamera> {
  const res = await fetch(`${API_BASE}/api/cameras`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to add camera"));
  return res.json();
}

export async function updateCamera(id: string, payload: CameraInput): Promise<RegistryCamera> {
  const res = await fetch(`${API_BASE}/api/cameras/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to update camera"));
  return res.json();
}

export async function deleteCamera(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/cameras/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to delete camera"));
}

export interface ImportResult {
  added: string[];
  skipped: string[];
  errors: Array<Record<string, unknown>>;
}

export async function importCamerasCsv(csv: string): Promise<ImportResult> {
  const res = await fetch(`${API_BASE}/api/cameras/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ csv }),
  });
  if (!res.ok) throw new Error(await parseError(res, "Failed to import cameras"));
  return res.json();
}

export function exportCamerasCsvUrl(): string {
  return `${API_BASE}/api/cameras/export.csv`;
}

export const CONNECTIVITY_LABEL: Record<RegistryCamera["connectivity"], string> = {
  online: "Online",
  offline: "Offline",
  disabled: "Disabled",
  "no-source": "No source",
};
