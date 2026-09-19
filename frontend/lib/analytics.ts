import { API_BASE } from "@/lib/config";
import { apiFetch } from "@/lib/auth";
import type { CameraHealth } from "@/lib/cameras";

export interface AnalyticsSummary {
  stats: {
    total: number;
    by_severity: Record<string, number>;
    by_camera: Record<string, number>;
    earliest_ts: number | null;
    latest_ts: number | null;
  };
  cameras: {
    total: number;
    by_connectivity: Record<string, number>;
    tampered: { id: string; name: string | null; issue: string }[];
    list: {
      id: string;
      name: string | null;
      department: string | null;
      connectivity: "online" | "offline" | "disabled" | "no-source";
      streaming: boolean;
      health: CameraHealth | null;
    }[];
  };
}

export async function fetchAnalyticsSummary(sinceHours?: number): Promise<AnalyticsSummary> {
  const q = sinceHours ? `?since_hours=${sinceHours}` : "";
  const res = await apiFetch(`${API_BASE}/api/analytics/summary${q}`);
  if (!res.ok) throw new Error(`Failed to load analytics (${res.status})`);
  return res.json();
}
