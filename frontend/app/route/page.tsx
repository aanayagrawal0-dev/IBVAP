"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Search, Route as RouteIcon, RefreshCw, Radar, AlertTriangle, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { fetchRoutePlates, injectSighting, type PlateSummary } from "@/lib/route";
import { fetchCameraOptions, FALLBACK_CAMERAS, type CameraOption } from "@/lib/cameras";

type Banner = { kind: "ok" | "error"; message: string } | null;

export default function RouteIndexPage() {
  const router = useRouter();
  const [plates, setPlates] = useState<PlateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [banner, setBanner] = useState<Banner>(null);

  // demo injector
  const [cameras, setCameras] = useState<CameraOption[]>(FALLBACK_CAMERAS);
  const [demoPlate, setDemoPlate] = useState("");
  const [demoCams, setDemoCams] = useState<string[]>([]);
  const [injecting, setInjecting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPlates(await fetchRoutePlates());
    } catch (err) {
      setBanner({ kind: "error", message: err instanceof Error ? err.message : "Failed to load plates." });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    fetchCameraOptions().then((cams) => {
      if (cams.length) setCameras(cams);
    });
  }, [load]);

  const go = (plate: string) => {
    const p = plate.trim().toUpperCase();
    if (p) router.push(`/route/${encodeURIComponent(p)}`);
  };

  const toggleCam = (id: string) =>
    setDemoCams((prev) => (prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]));

  const runDemo = async () => {
    if (!demoPlate.trim() || demoCams.length === 0) return;
    setInjecting(true);
    setBanner(null);
    try {
      // Inject in the selected order, spaced slightly so arrival times differ.
      for (const cam of demoCams) {
        await injectSighting(cam, demoPlate.trim());
        await new Promise((r) => setTimeout(r, 120));
      }
      setBanner({
        kind: "ok",
        message: `Recorded ${demoPlate.trim().toUpperCase()} on ${demoCams.length} camera(s). Opening its route…`,
      });
      await load();
      setTimeout(() => go(demoPlate), 400);
    } catch (err) {
      setBanner({ kind: "error", message: err instanceof Error ? err.message : "Injection failed." });
    } finally {
      setInjecting(false);
    }
  };

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-obsidian-border px-6 py-4">
        <div>
          <h1 className="font-headline text-lg font-bold text-ink">Route Reconstruction</h1>
          <p className="text-xs text-ink-dim">
            Enter a plate to reconstruct its cross-camera path — fusing ANPR reads with appearance
            Re-ID and filtering by camera-topology transition times.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          className="flex items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-ink-muted hover:text-ink"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          Refresh
        </button>
      </header>

      {banner && (
        <div
          role={banner.kind === "error" ? "alert" : "status"}
          className={cn(
            "mx-6 mt-3 flex items-center gap-2 rounded-md border px-3 py-2 text-xs font-mono",
            banner.kind === "ok" ? "border-emerald-500/40 text-emerald-400" : "border-critical/40 text-critical"
          )}
        >
          {banner.kind === "ok" ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
          {banner.message}
        </div>
      )}

      <div className="grid flex-1 grid-cols-1 gap-4 overflow-auto p-6 lg:grid-cols-[1fr_340px]">
        {/* Known plates */}
        <div className="flex flex-col gap-3">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              go(query);
            }}
            className="flex gap-2"
          >
            <label className="relative flex-1">
              <span className="sr-only">Plate number</span>
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-dim" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Plate number, e.g. GJ01 AB 1234"
                className="w-full rounded-md border border-obsidian-border bg-obsidian-950 py-2 pl-10 pr-3 text-sm font-mono uppercase text-ink placeholder:text-ink-dim focus:border-safety-500 focus:outline-none"
              />
            </label>
            <button
              type="submit"
              className="flex items-center gap-1.5 rounded-md bg-safety-500 px-4 py-2 text-xs font-semibold uppercase tracking-wide2 text-obsidian-950 hover:bg-safety-600"
            >
              <RouteIcon className="h-3.5 w-3.5" />
              Reconstruct
            </button>
          </form>

          <div className="rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4">
            <h2 className="mb-3 text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
              Plates with sightings
            </h2>
            {loading && plates.length === 0 && <p className="text-xs text-ink-dim">Loading…</p>}
            {!loading && plates.length === 0 && (
              <p className="text-xs text-ink-dim">
                No vehicle sightings recorded yet. Use the demo injector on the right, or let the
                live pipeline read plates.
              </p>
            )}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {plates.map((p) => (
                <Link
                  key={p.plate}
                  href={`/route/${encodeURIComponent(p.plate)}`}
                  className="flex items-center justify-between rounded-md border border-obsidian-border bg-obsidian-950 px-3 py-2 hover:border-safety-500"
                >
                  <span className="font-mono text-sm font-semibold text-ink">{p.plate_display}</span>
                  <span className="text-[10px] font-mono uppercase text-ink-dim">
                    {p.cameras} cam · {p.sightings} sight
                  </span>
                </Link>
              ))}
            </div>
          </div>
        </div>

        {/* Demo injector */}
        <div className="flex flex-col gap-3 self-start rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4">
          <h2 className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
            <Radar className="h-3.5 w-3.5" />
            Demo: build a route
          </h2>
          <p className="text-[11px] text-ink-dim">
            Records sightings of a plate across the cameras you pick (in order), as if their ANPR
            read it — so a multi-camera route can be shown without a live OCR model.
          </p>
          <input
            value={demoPlate}
            onChange={(e) => setDemoPlate(e.target.value)}
            placeholder="Plate, e.g. GJ05 CD 1234"
            className="w-full rounded-md border border-obsidian-border bg-obsidian-950 px-2 py-1.5 text-xs font-mono uppercase text-ink placeholder:text-ink-dim focus:border-safety-500 focus:outline-none"
          />
          <div className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
              Cameras (tap in travel order)
            </span>
            <div className="flex flex-wrap gap-1.5">
              {cameras.map((c) => {
                const i = demoCams.indexOf(c.id);
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => toggleCam(c.id)}
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-[10px] font-mono uppercase tracking-wide2",
                      i >= 0
                        ? "border-safety-500 bg-safety-500/15 text-safety-500"
                        : "border-obsidian-border bg-obsidian-950 text-ink-dim hover:text-ink"
                    )}
                  >
                    {i >= 0 ? `${i + 1}· ` : ""}
                    {c.id}
                  </button>
                );
              })}
            </div>
          </div>
          <button
            type="button"
            onClick={runDemo}
            disabled={injecting || !demoPlate.trim() || demoCams.length === 0}
            className="flex items-center justify-center gap-1.5 rounded-md border border-safety-500 px-3 py-2 text-xs font-semibold uppercase tracking-wide2 text-safety-500 hover:bg-safety-500/10 disabled:opacity-50"
          >
            {injecting ? "Recording…" : `Inject & view route (${demoCams.length})`}
          </button>
        </div>
      </div>
    </div>
  );
}
