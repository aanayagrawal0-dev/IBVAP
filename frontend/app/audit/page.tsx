"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, ClipboardList, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import { fetchAudit, type AuditEntry } from "@/lib/audit";

// Actions that change data or expose footage get a warmer tint.
const SENSITIVE = new Set([
  "search_plate", "view_camera", "export_redacted_clip", "export_history_csv",
  "export_cameras_csv", "watchlist_add", "watchlist_delete", "camera_delete",
]);

const ACTION_LABEL: Record<string, string> = {
  login: "Signed in",
  logout: "Signed out",
  login_failed: "Failed sign-in",
  view_camera: "Viewed camera",
  search_plate: "Searched plate",
  watchlist_add: "Added to watchlist",
  watchlist_delete: "Removed from watchlist",
  watchlist_set_active: "Toggled watchlist entry",
  camera_create: "Added camera",
  camera_update: "Edited camera",
  camera_delete: "Deleted camera",
  zones_update: "Updated zones",
  export_redacted_clip: "Exported redacted clip",
  export_history_csv: "Exported history CSV",
  export_cameras_csv: "Exported cameras CSV",
};

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchAudit({ limit: 200, action: actionFilter || undefined });
      setEntries(data.entries);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load audit log.");
    } finally {
      setLoading(false);
    }
  }, [actionFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const actions = Array.from(new Set(entries.map((e) => e.action)));

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-obsidian-border px-6 py-4">
        <div>
          <h1 className="flex items-center gap-2 font-headline text-lg font-bold text-ink">
            <ClipboardList className="h-5 w-5 text-safety-500" /> Audit Log
          </h1>
          <p className="text-xs text-ink-dim">
            Who viewed which camera, searched which plate, changed the watchlist, or exported footage.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            className="rounded-md border border-obsidian-border bg-obsidian-950 px-2 py-1.5 text-xs text-ink focus:outline-none"
          >
            <option value="">All actions</option>
            {actions.map((a) => (
              <option key={a} value={a}>
                {ACTION_LABEL[a] || a}
              </option>
            ))}
          </select>
          <span className="rounded-full border border-obsidian-border px-3 py-1 text-[10px] font-mono uppercase tracking-wide2 text-ink-dim">
            {total} entries
          </span>
          <button
            type="button"
            onClick={load}
            className="flex items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-ink-muted hover:text-ink"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            Refresh
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-auto p-6">
        {error && (
          <p className="flex items-center gap-2 text-xs text-critical">
            <AlertTriangle className="h-3.5 w-3.5" /> {error}
          </p>
        )}
        <div className="overflow-x-auto rounded-lg border border-obsidian-border">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="bg-obsidian-900 text-[10px] uppercase tracking-wide2 text-ink-dim">
              <tr>
                {["Time", "User", "Role", "Action", "Target", "Detail"].map((h) => (
                  <th key={h} className="px-3 py-2 font-bold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && entries.length === 0 && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-ink-dim">Loading…</td></tr>
              )}
              {!loading && entries.length === 0 && !error && (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-ink-dim">No audit entries yet.</td></tr>
              )}
              {entries.map((e) => (
                <tr key={e.id} className="border-t border-obsidian-border hover:bg-obsidian-800/40">
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-ink-dim">{e.ts_iso.replace("T", " ")}</td>
                  <td className="px-3 py-2 font-mono text-ink">{e.username}</td>
                  <td className="px-3 py-2 text-ink-muted">{e.role || "—"}</td>
                  <td className="px-3 py-2">
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wide2",
                        e.action === "login_failed"
                          ? "bg-critical/15 text-critical"
                          : SENSITIVE.has(e.action)
                          ? "bg-warning/15 text-warning"
                          : "bg-obsidian-800 text-ink-muted"
                      )}
                    >
                      {ACTION_LABEL[e.action] || e.action}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-ink-muted">{e.target || "—"}</td>
                  <td className="px-3 py-2 text-ink-dim">{e.detail || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
