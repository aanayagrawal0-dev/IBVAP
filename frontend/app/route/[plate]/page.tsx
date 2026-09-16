"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  AlertTriangle,
  MapPin,
  Camera,
  Clock,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { loadLeaflet, DARK_TILES, TILE_ATTR, escapeHtml } from "@/lib/leaflet";
import { fetchRoute, type RouteResult, type RouteStop } from "@/lib/route";

const SOURCE_COLOR: Record<RouteStop["plate_source"], string> = {
  anpr: "#22C55E", // confirmed plate read
  reid: "#F5A623", // appearance-matched (Re-ID fused)
};
const PLAUSIBLE = "#22C55E";
const IMPLAUSIBLE = "#FF3B30";

function fmtTime(iso: string) {
  return iso.replace("T", " ");
}
function fmtElapsed(s: number) {
  if (s < 90) return `${Math.round(s)}s`;
  if (s < 5400) return `${(s / 60).toFixed(1)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

export default function RouteMapPage() {
  const params = useParams<{ plate: string }>();
  const plate = Array.isArray(params.plate) ? params.plate[0] : params.plate;

  const [route, setRoute] = useState<RouteResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fuseReid, setFuseReid] = useState(true);
  const [useTopology, setUseTopology] = useState(true);
  const [mapError, setMapError] = useState(false);

  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const layersRef = useRef<any[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRoute(await fetchRoute(plate, { fuseReid, useTopology }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reconstruct route.");
    } finally {
      setLoading(false);
    }
  }, [plate, fuseReid, useTopology]);

  useEffect(() => {
    load();
  }, [load]);

  // Create the map once.
  useEffect(() => {
    let cancelled = false;
    loadLeaflet()
      .then((L) => {
        if (cancelled || mapRef.current || !mapContainerRef.current) return;
        const map = L.map(mapContainerRef.current, { center: [22.6, 72.4], zoom: 7 });
        L.tileLayer(DARK_TILES, { attribution: TILE_ATTR, maxZoom: 19 }).addTo(map);
        mapRef.current = map;
        setTimeout(() => map.invalidateSize(), 200);
      })
      .catch(() => {
        if (!cancelled) setMapError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Redraw the path whenever the route changes.
  useEffect(() => {
    const L = (window as any).L;
    const map = mapRef.current;
    if (!L || !map || !route) return;

    layersRef.current.forEach((l) => map.removeLayer(l));
    layersRef.current = [];

    const stops = route.stops.filter((s) => s.lat != null && s.lon != null);
    const latlngs: [number, number][] = stops.map((s) => [s.lat as number, s.lon as number]);

    // Draw each hop as its own segment, colored by transition plausibility.
    for (let i = 1; i < stops.length; i++) {
      const t = stops[i].transition;
      const ok = !t || t.plausible;
      const seg = L.polyline([latlngs[i - 1], latlngs[i]], {
        color: ok ? PLAUSIBLE : IMPLAUSIBLE,
        weight: 3,
        opacity: 0.9,
        dashArray: ok ? undefined : "6,7",
      }).addTo(map);
      layersRef.current.push(seg);
    }

    // Numbered markers.
    stops.forEach((s) => {
      const color = SOURCE_COLOR[s.plate_source];
      const marker = L.marker([s.lat as number, s.lon as number], {
        icon: L.divIcon({
          className: "",
          html: `<div style="display:flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:9999px;background:${color};color:#08090B;font:700 12px system-ui;border:2px solid #08090B;box-shadow:0 0 0 1px ${color}">${s.seq}</div>`,
          iconSize: [24, 24],
          iconAnchor: [12, 12],
        }),
      });
      marker.bindPopup(
        `<div style="font-family:system-ui;font-size:12px;line-height:1.5">
           <strong>#${s.seq} · ${s.camera_id}</strong> — ${escapeHtml(s.camera_name || "")}<br/>
           ${fmtTime(s.ts_iso)}<br/>
           ${s.plate_source === "reid" ? `Re-ID match (${s.reid_similarity})` : "Plate read (ANPR)"}
         </div>`
      );
      marker.addTo(map);
      layersRef.current.push(marker);
    });

    if (latlngs.length === 1) {
      map.setView(latlngs[0], 13);
    } else if (latlngs.length > 1) {
      map.fitBounds(latlngs, { padding: [50, 50], maxZoom: 13 });
    }
  }, [route]);

  const meta = route?.meta;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-obsidian-border px-6 py-4">
        <div className="flex items-center gap-3">
          <Link href="/route" className="rounded-md p-1.5 text-ink-muted hover:bg-obsidian-800 hover:text-ink">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <h1 className="font-headline text-lg font-bold text-ink">
              Route · <span className="font-mono text-safety-500">{route?.plate || plate}</span>
            </h1>
            <p className="text-xs text-ink-dim">Cross-camera path from ANPR + Re-ID, topology-checked.</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-[11px] font-mono uppercase text-ink-muted">
            <input type="checkbox" checked={fuseReid} onChange={(e) => setFuseReid(e.target.checked)} className="h-3.5 w-3.5 accent-safety-500" />
            Fuse Re-ID
          </label>
          <label className="flex items-center gap-1.5 text-[11px] font-mono uppercase text-ink-muted">
            <input type="checkbox" checked={useTopology} onChange={(e) => setUseTopology(e.target.checked)} className="h-3.5 w-3.5 accent-safety-500" />
            Topology
          </label>
          <button type="button" onClick={load} className="rounded-md border border-obsidian-border p-1.5 text-ink-muted hover:text-ink">
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          </button>
        </div>
      </header>

      {/* Meta bar */}
      {meta?.found && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-obsidian-border px-6 py-2 text-[11px] font-mono text-ink-dim">
          <span><span className="text-ink">{meta.stop_count}</span> stops · <span className="text-ink">{meta.camera_count}</span> cameras</span>
          <span>ANPR <span className="text-emerald-400">{meta.by_source?.anpr}</span> · Re-ID <span className="text-warning">{meta.by_source?.reid}</span></span>
          <span>{meta.total_distance_km} km · {meta.span_s != null ? fmtElapsed(meta.span_s) : "—"}</span>
          <span className={meta.all_transitions_plausible ? "text-emerald-400" : "text-critical"}>
            {meta.all_transitions_plausible ? "all transitions plausible" : "implausible hop(s) flagged"}
          </span>
        </div>
      )}

      <div className="grid flex-1 grid-cols-1 gap-4 overflow-hidden p-6 lg:grid-cols-[1fr_360px]">
        {/* Map */}
        <div className="relative min-h-[360px] overflow-hidden rounded-lg border border-obsidian-border">
          <div ref={mapContainerRef} className="absolute inset-0 h-full w-full bg-obsidian-800" />
          {mapError && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-obsidian-900/90 text-center text-xs text-ink-dim">
              <AlertTriangle className="h-5 w-5 text-warning" />
              Map couldn&apos;t load (no internet for the Leaflet CDN). The stop list still works.
            </div>
          )}
        </div>

        {/* Stops timeline */}
        <div className="min-h-0 overflow-y-auto rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4">
          {loading && <p className="text-xs text-ink-dim">Reconstructing…</p>}
          {error && <p className="text-xs text-critical">{error}</p>}
          {!loading && !error && route && route.stops.length === 0 && (
            <p className="text-xs text-ink-dim">
              No sightings found for this plate. Record some on the Route page, or let the live
              pipeline read it.
            </p>
          )}
          <ol className="flex flex-col gap-3">
            {route?.stops.map((s) => (
              <li key={s.sighting_id} className="relative rounded-md border border-obsidian-border bg-obsidian-950 p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-obsidian-950"
                      style={{ backgroundColor: SOURCE_COLOR[s.plate_source] }}
                    >
                      {s.seq}
                    </span>
                    <div>
                      <div className="flex items-center gap-1.5 font-mono text-xs font-semibold text-ink">
                        <Camera className="h-3 w-3 text-ink-dim" />
                        {s.camera_id}
                      </div>
                      <div className="text-[11px] text-ink-dim">{s.camera_name || "—"}</div>
                    </div>
                  </div>
                  <span
                    className={cn(
                      "rounded-full px-2 py-0.5 text-[9px] font-mono uppercase tracking-wide2",
                      s.plate_source === "anpr" ? "bg-emerald-500/15 text-emerald-400" : "bg-warning/15 text-warning"
                    )}
                  >
                    {s.plate_source === "anpr" ? "ANPR" : `Re-ID ${s.reid_similarity ?? ""}`}
                  </span>
                </div>
                <div className="mt-2 flex items-center gap-3 text-[11px] font-mono text-ink-dim">
                  <span className="flex items-center gap-1"><Clock className="h-3 w-3" />{fmtTime(s.ts_iso)}</span>
                  {s.lat != null && (
                    <span className="flex items-center gap-1"><MapPin className="h-3 w-3" />{s.lat.toFixed(3)}, {(s.lon as number).toFixed(3)}</span>
                  )}
                </div>
                {s.transition && (
                  <div
                    className={cn(
                      "mt-2 rounded border px-2 py-1 text-[10px] font-mono",
                      s.transition.plausible ? "border-emerald-500/30 text-ink-dim" : "border-critical/40 text-critical"
                    )}
                  >
                    ↑ {fmtElapsed(s.transition.elapsed_s)}
                    {s.transition.distance_km != null ? ` · ${s.transition.distance_km} km` : ""} ·{" "}
                    {s.transition.plausible ? "plausible" : `implausible — ${s.transition.reason}`}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
