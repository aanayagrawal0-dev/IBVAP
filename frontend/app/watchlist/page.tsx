"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Plus,
  Trash2,
  AlertTriangle,
  CheckCircle2,
  Radar,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Severity } from "@/lib/mock-data";
import {
  fetchWatchlist,
  addPlate,
  removeEntry,
  setActive,
  simulateSighting,
  type WatchlistEntry,
} from "@/lib/watchlist";
import { fetchCameraOptions, FALLBACK_CAMERAS, type CameraOption } from "@/lib/cameras";

type Banner = { kind: "ok" | "error"; message: string } | null;

const SEVERITIES: Severity[] = ["critical", "warning", "info"];

const SEV_CLS: Record<Severity, string> = {
  critical: "text-critical",
  warning: "text-warning",
  info: "text-info",
};

export default function WatchlistPage() {
  const [entries, setEntries] = useState<WatchlistEntry[]>([]);
  const [cameras, setCameras] = useState<CameraOption[]>(FALLBACK_CAMERAS);
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<Banner>(null);

  // add form
  const [plate, setPlate] = useState("");
  const [label, setLabel] = useState("");
  const [severity, setSeverity] = useState<Severity>("critical");
  const [adding, setAdding] = useState(false);

  // simulate panel
  const [simCamera, setSimCamera] = useState("");
  const [simPlate, setSimPlate] = useState("");
  const [simBusy, setSimBusy] = useState(false);
  const [simResult, setSimResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchWatchlist();
      setEntries(list);
    } catch (err) {
      setBanner({ kind: "error", message: err instanceof Error ? err.message : "Failed to load." });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    fetchCameraOptions().then((cams) => {
      if (cams.length) {
        setCameras(cams);
        setSimCamera((prev) => prev || cams[0].id);
      }
    });
  }, [load]);

  const submitAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!plate.trim()) return;
    setAdding(true);
    setBanner(null);
    try {
      const entry = await addPlate(plate.trim(), label.trim() || undefined, severity);
      setBanner({ kind: "ok", message: `Added ${entry.plate_display} to the watchlist.` });
      setPlate("");
      setLabel("");
      await load();
    } catch (err) {
      setBanner({ kind: "error", message: err instanceof Error ? err.message : "Add failed." });
    } finally {
      setAdding(false);
    }
  };

  const toggle = async (entry: WatchlistEntry) => {
    try {
      await setActive(entry.id, !entry.active);
      await load();
    } catch (err) {
      setBanner({ kind: "error", message: err instanceof Error ? err.message : "Update failed." });
    }
  };

  const remove = async (entry: WatchlistEntry) => {
    if (!window.confirm(`Remove ${entry.plate_display} from the watchlist?`)) return;
    try {
      await removeEntry(entry.id);
      await load();
    } catch (err) {
      setBanner({ kind: "error", message: err instanceof Error ? err.message : "Delete failed." });
    }
  };

  const runSimulate = async () => {
    if (!simCamera || !simPlate.trim()) return;
    setSimBusy(true);
    setSimResult(null);
    try {
      const res = await simulateSighting(simCamera, simPlate.trim());
      setSimResult(
        res.matched
          ? `MATCH — ${res.plate} on ${simCamera}. Real-time alert fired (severity: ${res.severity}). Check the Live feed's alert panel.`
          : `No match — ${res.plate} is not on the active watchlist. No alert fired.`
      );
    } catch (err) {
      setSimResult(err instanceof Error ? err.message : "Simulation failed.");
    } finally {
      setSimBusy(false);
    }
  };

  const activeCount = entries.filter((e) => e.active).length;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-obsidian-border px-6 py-4">
        <div>
          <h1 className="font-headline text-lg font-bold text-ink">Watchlist</h1>
          <p className="text-xs text-ink-dim">
            Plates of interest. Every plate read off a live feed is cross-referenced against this
            list; a match fires a real-time <span className="font-mono text-critical">watchlist_match</span> alert.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full border border-obsidian-border px-3 py-1 text-[10px] font-mono uppercase tracking-wide2 text-ink-dim">
            {entries.length} entries · {activeCount} active
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

      {banner && (
        <div
          role={banner.kind === "error" ? "alert" : "status"}
          className={cn(
            "mx-6 mt-3 flex items-center gap-2 rounded-md border px-3 py-2 text-xs font-mono",
            banner.kind === "ok"
              ? "border-emerald-500/40 text-emerald-400"
              : "border-critical/40 text-critical"
          )}
        >
          {banner.kind === "ok" ? (
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
          ) : (
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          )}
          {banner.message}
        </div>
      )}

      <div className="grid flex-1 grid-cols-1 gap-4 overflow-auto p-6 lg:grid-cols-[1fr_340px]">
        {/* Entries table */}
        <div className="overflow-x-auto rounded-lg border border-obsidian-border">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead className="bg-obsidian-900 text-[10px] uppercase tracking-wide2 text-ink-dim">
              <tr>
                {["Plate", "Reason", "Severity", "Status", ""].map((h) => (
                  <th key={h} className="px-3 py-2 font-bold">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && entries.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-ink-dim">
                    Loading watchlist…
                  </td>
                </tr>
              )}
              {!loading && entries.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-ink-dim">
                    No plates on the watchlist yet. Add one on the right.
                  </td>
                </tr>
              )}
              {entries.map((e) => (
                <tr key={e.id} className="border-t border-obsidian-border hover:bg-obsidian-800/50">
                  <td className="px-3 py-2 font-mono font-semibold text-ink">{e.plate_display}</td>
                  <td className="px-3 py-2 text-ink-muted">{e.label || "—"}</td>
                  <td className={cn("px-3 py-2 font-mono uppercase", SEV_CLS[e.severity])}>{e.severity}</td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() => toggle(e)}
                      className={cn(
                        "rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wide2",
                        e.active
                          ? "bg-emerald-500/15 text-emerald-400"
                          : "bg-obsidian-800 text-ink-dim"
                      )}
                    >
                      {e.active ? "Active" : "Paused"}
                    </button>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => remove(e)}
                      aria-label={`Delete ${e.plate_display}`}
                      className="rounded p-1 text-ink-dim hover:text-critical"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Right column: add + simulate */}
        <div className="flex flex-col gap-4 self-start">
          <form
            onSubmit={submitAdd}
            className="flex flex-col gap-3 rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4"
          >
            <h2 className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">Add plate</h2>
            <input
              value={plate}
              onChange={(e) => setPlate(e.target.value)}
              placeholder="GJ01 AB 1234"
              className={cn(inputCls, "font-mono uppercase")}
            />
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Reason (e.g. Stolen vehicle)"
              className={inputCls}
            />
            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value as Severity)}
              className={inputCls}
            >
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button
              type="submit"
              disabled={adding}
              className="flex items-center justify-center gap-1.5 rounded-md bg-safety-500 px-3 py-2 text-xs font-semibold uppercase tracking-wide2 text-obsidian-950 hover:bg-safety-600 disabled:opacity-50"
            >
              <Plus className="h-3.5 w-3.5" />
              {adding ? "Adding…" : "Add to watchlist"}
            </button>
          </form>

          <div className="flex flex-col gap-3 rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4">
            <h2 className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
              <Radar className="h-3.5 w-3.5" />
              Test a match
            </h2>
            <p className="text-[11px] text-ink-dim">
              Simulates a camera reading a plate (no physical plate / OCR model needed) and runs the
              real match + alert path.
            </p>
            <select
              value={simCamera}
              onChange={(e) => setSimCamera(e.target.value)}
              className={cn(inputCls, "font-mono")}
            >
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id} / {c.label}
                </option>
              ))}
            </select>
            <div className="flex gap-2">
              <input
                value={simPlate}
                onChange={(e) => setSimPlate(e.target.value)}
                placeholder="Plate to 'sight'"
                className={cn(inputCls, "font-mono uppercase")}
              />
              <button
                type="button"
                onClick={runSimulate}
                disabled={simBusy}
                className="shrink-0 rounded-md border border-safety-500 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-safety-500 hover:bg-safety-500/10 disabled:opacity-50"
              >
                {simBusy ? "…" : "Sight"}
              </button>
            </div>
            {entries.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {entries.slice(0, 6).map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    onClick={() => setSimPlate(e.plate_display)}
                    className="rounded-full border border-obsidian-border px-2 py-0.5 text-[10px] font-mono text-ink-dim hover:border-safety-500 hover:text-safety-500"
                  >
                    {e.plate_display}
                  </button>
                ))}
              </div>
            )}
            {simResult && (
              <p
                className={cn(
                  "rounded-md border px-2 py-1.5 text-[11px] font-mono",
                  simResult.startsWith("MATCH")
                    ? "border-critical/40 text-critical"
                    : "border-obsidian-border text-ink-muted"
                )}
              >
                {simResult}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

const inputCls =
  "w-full rounded-md border border-obsidian-border bg-obsidian-950 px-2 py-1.5 text-xs text-ink placeholder:text-ink-dim focus:border-safety-500 focus:outline-none";
