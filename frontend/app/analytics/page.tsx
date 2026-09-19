"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity, AlertTriangle, ShieldAlert, Radio, Camera, FileDown, RefreshCw,
} from "lucide-react";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from "recharts";
import { cn } from "@/lib/utils";
import { API_BASE } from "@/lib/config";
import { apiFetch } from "@/lib/auth";
import { fetchAnalyticsSummary, type AnalyticsSummary } from "@/lib/analytics";
import { CONNECTIVITY_LABEL, HEALTH_LABEL } from "@/lib/cameras";

const SEVERITY_COLOR: Record<string, string> = { critical: "#FF3B30", warning: "#F5A623", info: "#8B8B93" };
const CONN_COLOR: Record<string, string> = {
  online: "#22C55E", offline: "#F5A623", disabled: "#71717A", "no-source": "#3B82F6",
};
const RANGES: { label: string; hours?: number }[] = [
  { label: "24h", hours: 24 },
  { label: "7d", hours: 24 * 7 },
  { label: "30d", hours: 24 * 30 },
  { label: "All", hours: undefined },
];

export default function AnalyticsPage() {
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rangeIdx, setRangeIdx] = useState(3); // All
  const [reportState, setReportState] = useState<"idle" | "generating" | "error">("idle");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchAnalyticsSummary(RANGES[rangeIdx].hours));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load analytics.");
    } finally {
      setLoading(false);
    }
  }, [rangeIdx]);

  useEffect(() => {
    load();
  }, [load]);

  const generateReport = async () => {
    setReportState("generating");
    try {
      const res = await apiFetch(`${API_BASE}/api/analytics/report.pdf`);
      if (!res.ok) throw new Error(`Report failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `ibvap_report_${Date.now()}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setReportState("idle");
    } catch {
      setReportState("error");
      setTimeout(() => setReportState("idle"), 4000);
    }
  };

  const stats = data?.stats;
  const cams = data?.cameras;
  const sev = stats?.by_severity ?? {};
  const severityData = ["critical", "warning", "info"].map((k) => ({
    name: k[0].toUpperCase() + k.slice(1), key: k, value: sev[k] ?? 0,
  }));
  const cameraData = Object.entries(stats?.by_camera ?? {})
    .map(([id, n]) => ({ id, n }))
    .sort((a, b) => b.n - a.n)
    .slice(0, 12);
  const online = cams?.by_connectivity?.online ?? 0;
  const tampered = cams?.tampered ?? [];

  return (
    <div className="flex h-screen flex-col overflow-y-auto">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-obsidian-border px-6 py-5">
        <div>
          <h1 className="font-headline text-2xl font-bold text-ink">Operational Analytics</h1>
          <p className="text-xs text-ink-dim">
            Live camera health and event metrics — computed from the real event log, not mock data.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex overflow-hidden rounded-md border border-obsidian-border">
            {RANGES.map((r, i) => (
              <button
                key={r.label}
                type="button"
                onClick={() => setRangeIdx(i)}
                className={cn(
                  "px-3 py-1.5 text-[11px] font-mono uppercase tracking-wide2",
                  i === rangeIdx ? "bg-safety-500 text-obsidian-950" : "text-ink-muted hover:text-ink"
                )}
              >
                {r.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={load}
            className="flex items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-ink-muted hover:text-ink"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            Refresh
          </button>
          <button
            type="button"
            onClick={generateReport}
            disabled={reportState === "generating"}
            className="flex items-center gap-1.5 rounded-md bg-safety-500 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-obsidian-950 hover:bg-safety-600 disabled:opacity-60"
          >
            <FileDown className="h-3.5 w-3.5" />
            {reportState === "generating" ? "Generating…" : reportState === "error" ? "Failed" : "Report PDF"}
          </button>
        </div>
      </header>

      <div className="flex flex-col gap-4 p-6">
        {error && (
          <p className="flex items-center gap-2 text-xs text-critical">
            <AlertTriangle className="h-3.5 w-3.5" /> {error}
          </p>
        )}

        {tampered.length > 0 && (
          <div className="flex items-center gap-2 rounded-md border border-critical/40 bg-critical/10 px-3 py-2 text-xs font-mono text-critical">
            <ShieldAlert className="h-4 w-4 shrink-0" />
            {tampered.length} camera(s) flagged: {tampered.map((t) => `${t.id} (${HEALTH_LABEL[t.issue] || t.issue})`).join(", ")}
          </div>
        )}

        {/* KPI tiles */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Kpi icon={Activity} label="Total events" value={stats?.total ?? 0} />
          <Kpi icon={AlertTriangle} label="Critical" value={sev.critical ?? 0} tint="#FF3B30" />
          <Kpi icon={AlertTriangle} label="Warning" value={sev.warning ?? 0} tint="#F5A623" />
          <Kpi icon={Radio} label="Cameras online" value={`${online}/${cams?.total ?? 0}`} tint="#22C55E" />
          <Kpi icon={ShieldAlert} label="Tampered" value={tampered.length} tint={tampered.length ? "#FF3B30" : "#22C55E"} />
        </div>

        {/* Charts */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel title="Events by severity">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={severityData} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                <XAxis dataKey="name" tick={{ fill: "#71717A", fontSize: 11 }} axisLine={{ stroke: "#232327" }} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fill: "#71717A", fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} contentStyle={{ background: "#111113", border: "1px solid #232327", borderRadius: 8, fontSize: 12 }} />
                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                  {severityData.map((d) => <Cell key={d.key} fill={SEVERITY_COLOR[d.key]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Panel>

          <Panel title="Events by camera (top 12)">
            {cameraData.length === 0 ? (
              <p className="py-16 text-center text-xs text-ink-dim">No events in this range.</p>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={cameraData} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                  <XAxis dataKey="id" tick={{ fill: "#71717A", fontSize: 10 }} axisLine={{ stroke: "#232327" }} tickLine={false} interval={0} angle={-30} textAnchor="end" height={50} />
                  <YAxis allowDecimals={false} tick={{ fill: "#71717A", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} contentStyle={{ background: "#111113", border: "1px solid #232327", borderRadius: 8, fontSize: 12 }} />
                  <Bar dataKey="n" fill="#FF5C00" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </Panel>
        </div>

        {/* Per-camera health grid (Phase 6.1 → replaces the old mock heatmap) */}
        <Panel title="Camera health & status">
          {loading && !data ? (
            <p className="py-8 text-center text-xs text-ink-dim">Loading…</p>
          ) : (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {(cams?.list ?? []).map((c) => {
                const issue = c.health?.issue;
                const color = issue ? "#FF3B30" : CONN_COLOR[c.connectivity] ?? "#71717A";
                return (
                  <div key={c.id} className="rounded-md border border-obsidian-border bg-obsidian-950 p-3">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-1.5 font-mono text-xs font-semibold text-ink">
                        <Camera className="h-3 w-3 text-ink-dim" />
                        {c.id}
                      </span>
                      <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
                    </div>
                    <div className="mt-1 truncate text-[11px] text-ink-dim">{c.name || "—"}</div>
                    <div className="mt-2 flex items-center justify-between text-[10px] font-mono uppercase tracking-wide2">
                      <span className="text-ink-muted">{CONNECTIVITY_LABEL[c.connectivity]}</span>
                      <span className={issue ? "text-critical" : "text-emerald-400"}>
                        {issue ? (HEALTH_LABEL[issue] || issue) : (c.health?.state === "warming_up" ? "Calibrating" : "OK")}
                      </span>
                    </div>
                  </div>
                );
              })}
              {(cams?.list ?? []).length === 0 && (
                <p className="col-span-full py-8 text-center text-xs text-ink-dim">No cameras visible for your department.</p>
              )}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

function Kpi({ icon: Icon, label, value, tint }: { icon: React.ComponentType<{ className?: string }>; label: string; value: React.ReactNode; tint?: string }) {
  return (
    <div className="rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4" />
        <span className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">{label}</span>
      </div>
      <div className="mt-2 font-headline text-2xl font-bold" style={{ color: tint || "#FAFAFA" }}>{value}</div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4">
      <h2 className="mb-3 text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">{title}</h2>
      {children}
    </section>
  );
}
