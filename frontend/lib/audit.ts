import { API_BASE } from "@/lib/config";
import { apiFetch } from "@/lib/auth";

export interface AuditEntry {
  id: number;
  ts_iso: string;
  username: string;
  role: string | null;
  action: string;
  target: string | null;
  detail: string | null;
}

export async function fetchAudit(
  opts: { limit?: number; offset?: number; username?: string; action?: string } = {}
): Promise<{ entries: AuditEntry[]; total: number }> {
  const p = new URLSearchParams();
  if (opts.limit) p.set("limit", String(opts.limit));
  if (opts.offset) p.set("offset", String(opts.offset));
  if (opts.username) p.set("username", opts.username);
  if (opts.action) p.set("action", opts.action);
  const res = await apiFetch(`${API_BASE}/api/audit?${p.toString()}`);
  if (!res.ok) throw new Error(`Failed to load audit log (${res.status})`);
  return res.json();
}

/** POST a redacted-clip export and trigger a browser download of the mp4. */
export async function exportRedactedClip(cameraId: string, maxSeconds = 4): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/export/redacted-clip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ camera_id: cameraId, max_seconds: maxSeconds }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error((data && (data.detail as string)) || `Export failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `redacted_${cameraId}.mp4`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
