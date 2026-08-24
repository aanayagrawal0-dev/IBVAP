"use client";

import { Fragment, useEffect, useState } from "react";
import {
  Download,
  SlidersHorizontal,
  ExternalLink,
  AlertTriangle,
  ChevronRight,
  Sparkles,
} from "lucide-react";
import { SeverityBadge } from "@/components/severity-badge";
import { cn } from "@/lib/utils";
import type { Severity } from "@/lib/mock-data";
import {
  getHistory,
  exportHistoryCsvUrl,
  explainEvent,
  thumbnailUrl,
  type HistoryEvent,
} from "@/lib/history";

type ExplainState =
  | { status: "loading" }
  | { status: "ready"; text: string }
  | { status: "error"; message: string };

const SEVERITY_OPTIONS: Array<Severity | "all"> = ["all", "critical", "warning", "info"];

const DATE_RANGE_TO_HOURS: Record<string, number | undefined> = {
  "Last 24 Hours": 24,
  "Last 7 Days": 24 * 7,
  "Last 30 Days": 24 * 30,
  "All Time": undefined,
};

const PAGE_SIZE = 25;

export default function HistoryPage() {
  const [severityFilter, setSeverityFilter] = useState<Severity | "all">("all");
  const [dateRange, setDateRange] = useState("Last 24 Hours");
  const [page, setPage] = useState(0);

  const [events, setEvents] = useState<HistoryEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  // Which row is expanded (accordion — one at a time) and, per event id,
  // the state of its Gemini explanation. Cached client-side too so
  // collapsing/re-expanding the same row in this session doesn't re-fetch
  // — the backend caches it as well, but this skips the round trip.
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [explanations, setExplanations] = useState<Record<number, ExplainState>>({});

  const fetchExplanation = (id: number) => {
    setExplanations((prev) => ({ ...prev, [id]: { status: "loading" } }));
    explainEvent(id)
      .then(({ explanation }) => {
        setExplanations((prev) => ({ ...prev, [id]: { status: "ready", text: explanation } }));
      })
      .catch((err: Error) => {
        setExplanations((prev) => ({
          ...prev,
          [id]: { status: "error", message: err.message || "Something went wrong." },
        }));
      });
  };

  const toggleRow = (id: number) => {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    if (!explanations[id]) fetchExplanation(id);
  };

  // Re-fetch from the real backend whenever a filter or page changes —
  // nothing here is hardcoded or client-side filtered from a static array.
  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    getHistory({
      severity: severityFilter,
      sinceHours: DATE_RANGE_TO_HOURS[dateRange],
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((data) => {
        if (cancelled) return;
        setEvents(data.events);
        setTotal(data.total);
        setStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setEvents([]);
        setTotal(0);
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [severityFilter, dateRange, page]);

  // Any filter change resets to page 1 — otherwise you can strand yourself
  // on a page past the end of a now-smaller filtered result set.
  const changeSeverity = (s: Severity | "all") => {
    setSeverityFilter(s);
    setPage(0);
  };
  const changeDateRange = (r: string) => {
    setDateRange(r);
    setPage(0);
  };

  const exportUrl = exportHistoryCsvUrl({
    severity: severityFilter,
    sinceHours: DATE_RANGE_TO_HOURS[dateRange],
  });

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min(total, page * PAGE_SIZE + events.length);
  const hasNextPage = page * PAGE_SIZE + events.length < total;

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
        <a
          href={exportUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 rounded-md bg-safety-500 px-3 py-2 text-xs font-semibold uppercase tracking-wide2 text-obsidian-950 hover:bg-safety-600"
        >
          <Download className="h-3.5 w-3.5" />
          Export Log
        </a>
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
              onChange={(e) => changeDateRange(e.target.value)}
              className="rounded border border-obsidian-border bg-obsidian-950 px-2 py-1.5 text-xs text-ink focus:outline-none"
            >
              {Object.keys(DATE_RANGE_TO_HOURS).map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
              Severity
            </span>
            <select
              value={severityFilter}
              onChange={(e) => changeSeverity(e.target.value as Severity | "all")}
              className="rounded border border-obsidian-border bg-obsidian-950 px-2 py-1.5 text-xs text-ink capitalize focus:outline-none"
            >
              {SEVERITY_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s === "all" ? "All Levels" : s}
                </option>
              ))}
            </select>
          </label>

          <span className="flex items-center gap-1.5 text-xs text-ink-dim">
            <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
            Filters apply automatically
          </span>
        </div>

        {status === "error" && (
          <div
            role="alert"
            className="flex items-center gap-2 rounded-lg border border-critical bg-critical-bg px-4 py-3 text-xs text-critical"
          >
            <AlertTriangle className="h-4 w-4 shrink-0" />
            Couldn&apos;t reach the history service. Make sure the backend is running, then
            reload.
          </div>
        )}

        {/* Table — horizontally scrollable so it never clips on narrow
            viewports, per the responsive-testing guidance (375/768px). */}
        <div className="overflow-x-auto rounded-lg border border-obsidian-border">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead className="border-b border-obsidian-border bg-obsidian-900/60 text-[10px] uppercase tracking-wide2 text-ink-dim">
              <tr>
                <th scope="col" className="w-8 px-2 py-3">
                  <span className="sr-only">Expand for AI analysis</span>
                </th>
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
              {status === "loading" && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-ink-dim">
                    Loading…
                  </td>
                </tr>
              )}
              {status === "ready" &&
                events.map((event) => {
                  const thumb = thumbnailUrl(event);
                  const isExpanded = expandedId === event.id;
                  const explain = explanations[event.id];
                  return (
                    <Fragment key={event.id}>
                      <tr
                        onClick={() => toggleRow(event.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            toggleRow(event.id);
                          }
                        }}
                        role="button"
                        tabIndex={0}
                        aria-expanded={isExpanded}
                        aria-label={`${isExpanded ? "Collapse" : "Expand"} AI analysis for ${event.eventType} on ${event.camera}`}
                        className="cursor-pointer border-b border-obsidian-border last:border-0 hover:bg-obsidian-900/40 focus:outline-none focus:bg-obsidian-900/40"
                      >
                        <td className="px-2 py-3">
                          <ChevronRight
                            className={cn(
                              "h-3.5 w-3.5 text-ink-dim transition-transform",
                              isExpanded && "rotate-90 text-safety-500"
                            )}
                            aria-hidden="true"
                          />
                        </td>
                        <td className="px-4 py-3 font-mono text-ink-muted whitespace-nowrap">
                          {event.timestamp}
                        </td>
                        <td className="px-4 py-3">
                          {thumb ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={thumb}
                              alt={`Frame captured when ${event.eventType.toLowerCase()} fired`}
                              className="h-8 w-12 rounded object-cover"
                            />
                          ) : (
                            <div
                              className="h-8 w-12 rounded bg-gradient-to-br from-obsidian-700 to-obsidian-950"
                              aria-hidden="true"
                              title="No thumbnail captured for this event"
                            />
                          )}
                        </td>
                        <td className="px-4 py-3 text-ink">{event.eventType}</td>
                        <td className="px-4 py-3 font-mono text-ink-muted">{event.camera}</td>
                        <td className="px-4 py-3">
                          <SeverityBadge severity={event.severity} />
                        </td>
                        <td className="px-4 py-3">
                          {thumb ? (
                            <a
                              href={thumb}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              aria-label={`Open full-size frame for event ${event.id}`}
                              className="text-ink-dim hover:text-safety-500"
                            >
                              <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                          ) : (
                            <span className="text-ink-dim/40">
                              <ExternalLink className="h-3.5 w-3.5" />
                            </span>
                          )}
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr className="border-b border-obsidian-border last:border-0 bg-obsidian-950/50">
                          <td colSpan={7} className="px-6 py-4">
                            <div className="flex items-start gap-3">
                              <Sparkles
                                className="mt-0.5 h-4 w-4 shrink-0 text-safety-500"
                                aria-hidden="true"
                              />
                              <div className="min-w-0 flex-1">
                                <p className="mb-1.5 text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
                                  Gemini Analysis
                                </p>
                                {(!explain || explain.status === "loading") && (
                                  <p className="text-xs text-ink-dim">Asking Gemini…</p>
                                )}
                                {explain?.status === "error" && (
                                  <div className="flex flex-wrap items-center gap-2 text-xs text-critical">
                                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                                    <span>{explain.message}</span>
                                    <button
                                      type="button"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        fetchExplanation(event.id);
                                      }}
                                      className="underline hover:no-underline"
                                    >
                                      Retry
                                    </button>
                                  </div>
                                )}
                                {explain?.status === "ready" && (
                                  <p className="text-xs leading-relaxed text-ink">
                                    {explain.text}
                                  </p>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              {status === "ready" && events.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-ink-dim">
                    No events match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between">
          <p className="text-[10px] uppercase tracking-wide2 text-ink-dim">
            {total === 0
              ? "Showing 0 of 0 events"
              : `Showing ${rangeStart}-${rangeEnd} of ${total} events`}
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded border border-obsidian-border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide2 text-ink-muted hover:text-ink disabled:opacity-30 disabled:hover:text-ink-muted"
            >
              Prev
            </button>
            <button
              type="button"
              onClick={() => setPage((p) => p + 1)}
              disabled={!hasNextPage}
              className="rounded border border-obsidian-border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide2 text-ink-muted hover:text-ink disabled:opacity-30 disabled:hover:text-ink-muted"
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
