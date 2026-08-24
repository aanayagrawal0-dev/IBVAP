"use client";

import { useState } from "react";
import { Activity, AlertTriangle, Clock, ShieldCheck } from "lucide-react";
import { BentoCard, BentoGrid } from "@/components/bento-grid";
import { BreachTrendChart } from "@/components/breach-trend-chart";
import { ActivityHeatmap } from "@/components/activity-heatmap";
import { stats } from "@/lib/mock-data";
import { API_BASE } from "@/lib/config";

const STAT_ICONS = [Activity, Clock, ShieldCheck];

export default function AnalyticsPage() {
  const [reportState, setReportState] = useState<"idle" | "generating" | "error">("idle");

  // Pulls a real PDF from the backend (summary stats + recent event log,
  // built straight from the history database) rather than being a dead
  // button — triggers a normal browser download once it arrives.
  const generateReport = async () => {
    setReportState("generating");
    try {
      const res = await fetch(`${API_BASE}/api/analytics/report.pdf`);
      if (!res.ok) throw new Error(`Report generation failed (${res.status})`);
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
    }
  };

  return (
    <div className="flex h-screen flex-col overflow-y-auto">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-obsidian-border px-6 py-5">
        <div>
          <h1 className="font-headline text-2xl font-bold text-ink">Operational Analytics</h1>
          <p className="text-xs text-ink-dim">
            System-wide performance and threat detection metrics.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-2 text-[10px] font-bold uppercase tracking-wide2 text-ink-muted">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            Live System
          </span>
          <button
            type="button"
            onClick={generateReport}
            disabled={reportState === "generating"}
            className="rounded-md bg-safety-500 px-3 py-2 text-xs font-semibold uppercase tracking-wide2 text-obsidian-950 hover:bg-safety-600 disabled:cursor-wait disabled:opacity-70"
          >
            {reportState === "generating" ? "Generating…" : "Generate Report"}
          </button>
        </div>
      </header>

      {reportState === "error" && (
        <div
          role="alert"
          className="mx-6 mt-4 flex items-center gap-2 rounded-lg border border-critical bg-critical-bg px-4 py-3 text-xs text-critical"
        >
          <AlertTriangle className="h-4 w-4 shrink-0" />
          Couldn&apos;t generate the report — make sure the backend is running, then try again.
        </div>
      )}

      <div className="flex flex-col gap-4 p-6">
        <BentoGrid>
          {stats.map((stat, i) => {
            const Icon = STAT_ICONS[i];
            return (
              <BentoCard key={stat.label}>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
                    {stat.label}
                  </span>
                  <Icon className="h-4 w-4 text-ink-dim" aria-hidden="true" />
                </div>
                <p className="font-headline text-3xl font-bold text-safety-500">{stat.value}</p>
                {stat.delta && (
                  <p className="mt-1 text-[11px] font-mono text-emerald-400">{stat.delta}</p>
                )}
              </BentoCard>
            );
          })}
        </BentoGrid>

        <BentoGrid>
          <BentoCard span={2}>
            <h2 className="mb-2 text-sm font-semibold text-ink">30-Day Breach Trend Analysis</h2>
            <BreachTrendChart />
          </BentoCard>

          <BentoCard span={1}>
            <h2 className="mb-3 text-sm font-semibold text-ink">Activity Density Matrix</h2>
            <ActivityHeatmap />
          </BentoCard>
        </BentoGrid>
      </div>
    </div>
  );
}
