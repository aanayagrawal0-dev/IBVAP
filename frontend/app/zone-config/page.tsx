"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, Save, X, Trash2, AlertTriangle, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { cameras } from "@/lib/mock-data";
import { getZones, saveZones, type Point, type ZoneDef } from "@/lib/zones";

const ZONE_COLORS = [
  "#FF5C00", // safety orange
  "#3B82F6", // blue
  "#A855F7", // purple
  "#22C55E", // green
  "#F5A623", // amber
  "#FF3B30", // critical red
  "#06B6D4", // cyan
  "#EC4899", // pink
];

function colorFor(index: number) {
  return ZONE_COLORS[index % ZONE_COLORS.length];
}

function nextDefaultName(zones: ZoneDef[]) {
  let n = zones.length + 1;
  const taken = new Set(zones.map((z) => z.name));
  while (taken.has(`zone-${n}`)) n++;
  return `zone-${n}`;
}

type LoadState = "loading" | "loaded" | "offline";
type SaveState = { kind: "idle" } | { kind: "saving" } | { kind: "ok"; hotReloaded: boolean } | { kind: "error"; message: string };

export default function ZoneConfigPage() {
  const [cameraId, setCameraId] = useState(cameras[0].id);
  const [zones, setZones] = useState<ZoneDef[]>([]);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const containerRef = useRef<HTMLDivElement>(null);

  // Load this camera's saved zones whenever the selected camera changes.
  useEffect(() => {
    let cancelled = false;
    setLoadState("loading");
    setSelectedIndex(null);
    setSaveState({ kind: "idle" });
    getZones(cameraId)
      .then((loaded) => {
        if (cancelled) return;
        setZones(loaded);
        setSelectedIndex(loaded.length > 0 ? 0 : null);
        setLoadState("loaded");
      })
      .catch(() => {
        if (cancelled) return;
        // Backend not reachable — still let the operator draw locally, but
        // be upfront that nothing will persist until it's back.
        setZones([]);
        setLoadState("offline");
      });
    return () => {
      cancelled = true;
    };
  }, [cameraId]);

  const toPercent = useCallback((clientX: number, clientY: number): Point => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: Math.min(100, Math.max(0, ((clientX - rect.left) / rect.width) * 100)),
      y: Math.min(100, Math.max(0, ((clientY - rect.top) / rect.height) * 100)),
    };
  }, []);

  const updateSelectedPolygon = (updater: (polygon: Point[]) => Point[]) => {
    if (selectedIndex === null) return;
    setZones((prev) =>
      prev.map((z, i) => (i === selectedIndex ? { ...z, polygon: updater(z.polygon) } : z))
    );
    setSaveState({ kind: "idle" });
  };

  const handleCanvasClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!adding || selectedIndex === null) return;
    const p = toPercent(e.clientX, e.clientY);
    updateSelectedPolygon((poly) => [...poly, p]);
  };

  const handlePointerMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (dragIndex === null) return;
    const p = toPercent(e.clientX, e.clientY);
    updateSelectedPolygon((poly) => poly.map((pt, i) => (i === dragIndex ? p : pt)));
  };

  const addZone = () => {
    setZones((prev) => {
      const next = [...prev, { name: nextDefaultName(prev), polygon: [] }];
      setSelectedIndex(next.length - 1);
      return next;
    });
    setAdding(true);
    setSaveState({ kind: "idle" });
  };

  const deleteZone = (index: number) => {
    setZones((prev) => prev.filter((_, i) => i !== index));
    setSelectedIndex((prev) => {
      if (prev === null) return null;
      if (prev === index) return null;
      return prev > index ? prev - 1 : prev;
    });
    setSaveState({ kind: "idle" });
  };

  const renameZone = (index: number, name: string) => {
    setZones((prev) => prev.map((z, i) => (i === index ? { ...z, name } : z)));
    setSaveState({ kind: "idle" });
  };

  const handleSaveAll = async () => {
    setSaveState({ kind: "saving" });
    try {
      const result = await saveZones(cameraId, zones);
      setSaveState({ kind: "ok", hotReloaded: result.hotReloaded });
      setLoadState("loaded");
    } catch (err) {
      setSaveState({ kind: "error", message: err instanceof Error ? err.message : "Save failed." });
    }
  };

  const selectedZone = selectedIndex !== null ? zones[selectedIndex] : null;
  const activeCamera = cameras.find((c) => c.id === cameraId) ?? cameras[0];

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-obsidian-border px-6 py-4">
        <h1 className="font-headline text-lg font-bold text-ink">Zone Configuration</h1>
        <p className="text-xs text-ink-dim">
          Define one or more restricted-area polygons per camera. Only the camera currently
          streaming live picks up changes immediately — the rest are saved and ready for when
          that feed is connected.
        </p>
      </header>

      <div className="flex flex-1 gap-4 overflow-auto p-6">
        {/* Left: camera picker + zone list */}
        <div className="flex w-64 shrink-0 flex-col gap-4">
          <div>
            <label htmlFor="camera-select" className="mb-1.5 block text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
              Camera
            </label>
            <select
              id="camera-select"
              value={cameraId}
              onChange={(e) => setCameraId(e.target.value)}
              className="w-full rounded-md border border-obsidian-border bg-obsidian-900 px-2 py-2 text-xs font-mono text-ink focus:border-safety-500 focus:outline-none"
            >
              {cameras.map((cam) => (
                <option key={cam.id} value={cam.id}>
                  {cam.id} / {cam.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex-1 rounded-md border border-obsidian-border bg-obsidian-900/60 p-3">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
                Zones ({zones.length})
              </h2>
              <button
                type="button"
                onClick={addZone}
                className="flex items-center gap-1 rounded border border-obsidian-border px-2 py-1 text-[10px] font-semibold uppercase tracking-wide2 text-ink-muted hover:border-safety-500 hover:text-safety-500"
              >
                <Plus className="h-3 w-3" />
                New Zone
              </button>
            </div>

            {loadState === "loading" && (
              <p className="text-xs text-ink-dim">Loading saved zones…</p>
            )}

            {zones.length === 0 && loadState !== "loading" && (
              <p className="text-xs text-ink-dim">
                No zones yet for {activeCamera.id}. Click "New Zone" to start drawing one.
              </p>
            )}

            <ul className="flex flex-col gap-1">
              {zones.map((zone, i) => (
                <li key={i}>
                  <div
                    className={cn(
                      "flex items-center gap-2 rounded-md border px-2 py-1.5",
                      selectedIndex === i
                        ? "border-safety-500 bg-safety-500/5"
                        : "border-transparent hover:bg-obsidian-800"
                    )}
                  >
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: colorFor(i) }}
                      aria-hidden="true"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedIndex(i);
                        setAdding(false);
                      }}
                      className="min-w-0 flex-1 truncate text-left text-xs font-mono text-ink"
                    >
                      {zone.name || "(unnamed)"}
                      <span className="ml-1.5 text-ink-dim">{zone.polygon.length}pt</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteZone(i)}
                      aria-label={`Delete zone ${zone.name}`}
                      className="shrink-0 rounded p-1 text-ink-dim hover:text-critical"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Right: editor */}
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-obsidian-border bg-obsidian-900/60 p-3">
            <label className="flex items-center gap-2 text-xs">
              <span className="text-ink-dim">Zone name</span>
              <input
                value={selectedZone?.name ?? ""}
                disabled={selectedIndex === null}
                onChange={(e) => selectedIndex !== null && renameZone(selectedIndex, e.target.value)}
                className="rounded border border-obsidian-border bg-obsidian-950 px-2 py-1 text-xs font-mono text-ink focus:outline-none disabled:opacity-40"
              />
            </label>

            <div className="mx-1 h-5 w-px bg-obsidian-border" aria-hidden="true" />

            <button
              type="button"
              onClick={() => setAdding((v) => !v)}
              disabled={selectedIndex === null}
              aria-pressed={adding}
              className={cn(
                "flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 disabled:cursor-not-allowed disabled:opacity-40",
                adding
                  ? "border-safety-500 bg-safety-500/10 text-safety-500"
                  : "border-obsidian-border text-ink-muted hover:text-ink"
              )}
            >
              <Plus className="h-3.5 w-3.5" />
              {adding ? "Click canvas to add point" : "Add Point"}
            </button>

            <button
              type="button"
              disabled={selectedIndex === null}
              onClick={() => updateSelectedPolygon(() => [])}
              className="flex items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-ink-muted hover:text-critical hover:border-critical disabled:cursor-not-allowed disabled:opacity-40"
            >
              <X className="h-3.5 w-3.5" />
              Clear Points
            </button>

            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                disabled={zones.some((z) => z.polygon.length < 3) || saveState.kind === "saving"}
                onClick={handleSaveAll}
                title={
                  zones.some((z) => z.polygon.length < 3)
                    ? "Every zone needs at least 3 points before saving."
                    : undefined
                }
                className="flex items-center gap-1.5 rounded-md bg-safety-500 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-obsidian-950 hover:bg-safety-600 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Save className="h-3.5 w-3.5" />
                {saveState.kind === "saving" ? "Saving…" : "Save All"}
              </button>
            </div>
          </div>

          {loadState === "offline" && (
            <p role="status" className="flex items-center gap-2 text-xs font-mono text-warning">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              Backend not reachable — editing locally, but Save will fail until it's running.
            </p>
          )}

          {saveState.kind === "ok" && (
            <p role="status" className="flex items-center gap-2 text-xs font-mono text-emerald-400">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
              Saved {zones.length} zone{zones.length === 1 ? "" : "s"} for {cameraId}.
              {saveState.hotReloaded
                ? " This camera is live — the running feed picked it up immediately."
                : " This camera isn't the active live feed right now, so it'll apply the moment it is."}
            </p>
          )}

          {saveState.kind === "error" && (
            <p role="alert" className="flex items-center gap-2 text-xs font-mono text-critical">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              {saveState.message}
            </p>
          )}

          {/* Drawing canvas */}
          <div
            ref={containerRef}
            onClick={handleCanvasClick}
            onMouseMove={handlePointerMove}
            onMouseUp={() => setDragIndex(null)}
            onMouseLeave={() => setDragIndex(null)}
            className={cn(
              "relative aspect-video w-full overflow-hidden rounded-lg border border-obsidian-border",
              adding ? "cursor-crosshair" : "cursor-default"
            )}
            style={{
              background:
                "linear-gradient(180deg, #3a2f2a 0%, #6b4a35 35%, #8a5a3a 48%, #2a1f1a 60%, #14100d 100%)",
            }}
          >
            <svg
              className="absolute inset-0 h-full w-full"
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
            >
              {zones.map((zone, i) => {
                if (zone.polygon.length < 2) return null;
                const isSelected = i === selectedIndex;
                const color = colorFor(i);
                return (
                  <polygon
                    key={i}
                    points={zone.polygon.map((p) => `${p.x},${p.y}`).join(" ")}
                    fill={isSelected ? `${color}26` : `${color}14`}
                    stroke={color}
                    strokeWidth={isSelected ? 2 : 1}
                    strokeOpacity={isSelected ? 1 : 0.5}
                    vectorEffect="non-scaling-stroke"
                  />
                );
              })}
            </svg>

            {zones.map((zone, i) =>
              zone.polygon.map((p, vi) => (
                <button
                  key={`${i}-${vi}`}
                  type="button"
                  aria-label={`${zone.name} vertex ${vi + 1}${i === selectedIndex ? ", drag to reposition" : ""}`}
                  onMouseDown={(e) => {
                    if (i !== selectedIndex) return;
                    e.stopPropagation();
                    setDragIndex(vi);
                  }}
                  className={cn(
                    "absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 bg-obsidian-950",
                    i === selectedIndex
                      ? "h-3.5 w-3.5 hover:scale-125 focus-visible:scale-125"
                      : "h-2 w-2 cursor-default"
                  )}
                  style={{ left: `${p.x}%`, top: `${p.y}%`, borderColor: colorFor(i) }}
                />
              ))
            )}

            {zones.map((zone, i) => {
              if (zone.polygon.length === 0) return null;
              const top = Math.min(...zone.polygon.map((p) => p.y));
              const left = Math.min(...zone.polygon.map((p) => p.x));
              return (
                <span
                  key={i}
                  className="pointer-events-none absolute -translate-y-full whitespace-nowrap rounded px-1 py-0.5 text-[9px] font-mono font-bold uppercase"
                  style={{ left: `${left}%`, top: `${top}%`, backgroundColor: colorFor(i), color: "#08090B" }}
                >
                  {zone.name}
                </span>
              );
            })}

            {zones.length === 0 && (
              <p className="absolute inset-0 flex items-center justify-center text-xs text-ink-dim">
                Click "New Zone" to start, then "Add Point" and click on the frame to draw it.
              </p>
            )}

            {selectedIndex !== null && zones[selectedIndex]?.polygon.length === 0 && (
              <p className="absolute inset-x-0 bottom-2 text-center text-xs text-ink-dim">
                Click "Add Point" then click on the frame to place vertices for "{selectedZone?.name}".
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
