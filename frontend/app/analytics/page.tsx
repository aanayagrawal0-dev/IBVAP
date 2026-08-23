"use client";

import { Activity, Clock, ShieldCheck } from "lucide-react";
import { BentoCard, BentoGrid } from "@/components/bento-grid";
import { BreachTrendChart } from "@/components/breach-trend-chart";
import { ActivityHeatmap } from "@/components/activity-heatmap";
import { stats } from "@/lib/mock-data";

const STAT_ICONS = [Activity, Clock, ShieldCheck];

export default function AnalyticsPage() {
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
            className="rounded-md bg-safety-500 px-3 py-2 text-xs font-semibold uppercase tracking-wide2 text-obsidian-950 hover:bg-safety-600"
          >
            Generate Report
          </button>
        </div>
      </header>

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
