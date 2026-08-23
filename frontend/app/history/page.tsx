"use client";

import { useMemo, useState } from "react";
import { Download, SlidersHorizontal, ExternalLink } from "lucide-react";
import { SeverityBadge } from "@/components/severity-badge";
import { historyEvents, type Severity } from "@/lib/mock-data";

const SEVERITY_OPTIONS: Array<Severity | "all"> = ["all", "critical", "warning", "info"];

export default function HistoryPage() {
  const [severityFilter, setSeverityFilter] = useState<Severity | "all">("all");
  const [dateRange, setDateRange] = useState("Last 24 Hours");

  const filtered = useMemo(
    () =>
      historyEvents.filter(
        (e) => severityFilter === "all" || e.severity === severityFilter
      ),
    [severityFilter]
  );

  return (
    <div className="flex h-screen flex-col overflow-y-auto">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-obsidian-border px-6 py-5">
        <div>
          <h1 className="font-headline text-lg font-bold text-ink">Event History Log</h1>
          <p className="text-xs text-ink-dim">
            Historical log of all detected anomalies and system events across the active
            surveillance grid.
          </p>
        </div>
        <button
          type="button"
          className="flex items-center gap-1.5 rounded-md bg-safety-500 px-3 py-2 text-xs font-semibold uppercase tracking-wide2 text-obsidian-950 hover:bg-safety-600"
        >
          <Download className="h-3.5 w-3.5" />
          Export Log
        </button>
      </header>

      <div className="flex flex-col gap-4 p-6">
        {/* Filters */}
        <div className="flex flex-wrap items-end gap-4 rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
              Date Range
            </span>
            <select
              value={dateRange}
              onChange={(e) => setDateRange(e.target.value)}
              className="rounded border border-obsidian-border bg-obsidian-950 px-2 py-1.5 text-xs text-ink focus:outline-none"
            >
              <option>Last 24 Hours</option>
              <option>Last 7 Days</option>
              <option>Last 30 Days</option>
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
              Severity
            </span>
            <select
              value={severityFilter}
              onChange={(e) => setSeverityFilter(e.target.value as Severity | "all")}
              className="rounded border border-obsidian-border bg-obsidian-950 px-2 py-1.5 text-xs text-ink capitalize focus:outline-none"
            >
              {SEVERITY_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s === "all" ? "All Levels" : s}
                </option>
              ))}
            </select>
          </label>

          <button
            type="button"
            className="flex items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-ink-muted hover:text-ink"
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
            Apply Filters
          </button>
        </div>

        {/* Table — horizontally scrollable so it never clips on narrow
            viewports, per the responsive-testing guidance (375/768px). */}
        <div className="overflow-x-auto rounded-lg border border-obsidian-border">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="border-b border-obsidian-border bg-obsidian-900/60 text-[10px] uppercase tracking-wide2 text-ink-dim">
              <tr>
                <th scope="col" className="px-4 py-3 font-bold">
                  Timestamp
                </th>
                <th scope="col" className="px-4 py-3 font-bold">
                  Thumbnail
                </th>
                <th scope="col" className="px-4 py-3 font-bold">
                  Event Type
                </th>
                <th scope="col" className="px-4 py-3 font-bold">
                  Camera ID
                </th>
                <th scope="col" className="px-4 py-3 font-bold">
                  Severity
                </th>
                <th scope="col" className="px-4 py-3 font-bold">
                  Action
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((event) => (
                <tr
                  key={event.id}
                  className="border-b border-obsidian-border last:border-0 hover:bg-obsidian-900/40"
                >
                  <td className="px-4 py-3 font-mono text-ink-muted whitespace-nowrap">
                    {event.timestamp}
                  </td>
                  <td className="px-4 py-3">
                    <div
                      className="h-8 w-12 rounded bg-gradient-to-br from-obsidian-700 to-obsidian-950"
                      aria-hidden="true"
                    />
                  </td>
                  <td className="px-4 py-3 text-ink">{event.eventType}</td>
                  <td className="px-4 py-3 font-mono text-ink-muted">{event.camera}</td>
                  <td className="px-4 py-3">
                    <SeverityBadge severity={event.severity} />
                  </td>
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      aria-label={`Open event ${event.id} details`}
                      className="text-ink-dim hover:text-safety-500"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-ink-dim">
                    No events match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <p className="text-[10px] uppercase tracking-wide2 text-ink-dim">
          Showing 1-{filtered.length} of 1,245 events
        </p>
      </div>
    </div>
  );
}
