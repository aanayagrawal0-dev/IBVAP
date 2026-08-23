"use client";

import { useCallback, useRef, useState } from "react";
import { Plus, Save, X, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface Point {
  x: number; // percentage 0-100
  y: number;
}

const DEFAULT_ZONE: Point[] = [
  { x: 62, y: 8 },
  { x: 96, y: 8 },
  { x: 96, y: 92 },
  { x: 62, y: 92 },
];

export default function ZoneConfigPage() {
  const [points, setPoints] = useState<Point[]>(DEFAULT_ZONE);
  const [zoneName, setZoneName] = useState("restricted-zone");
  const [adding, setAdding] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [saved, setSaved] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const toPercent = useCallback((clientX: number, clientY: number): Point => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: Math.min(100, Math.max(0, ((clientX - rect.left) / rect.width) * 100)),
      y: Math.min(100, Math.max(0, ((clientY - rect.top) / rect.height) * 100)),
    };
  }, []);

  const handleCanvasClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!adding) return;
    setPoints((prev) => [...prev, toPercent(e.clientX, e.clientY)]);
    setSaved(false);
  };

  const handlePointerMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (dragIndex === null) return;
    const p = toPercent(e.clientX, e.clientY);
    setPoints((prev) => prev.map((pt, i) => (i === dragIndex ? p : pt)));
    setSaved(false);
  };

  const polygonStr = points.map((p) => `${p.x},${p.y}`).join(" ");

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-obsidian-border px-6 py-4">
        <h1 className="font-headline text-lg font-bold text-ink">Zone Configuration</h1>
        <p className="text-xs text-ink-dim">
          Define the virtual fence / restricted-area polygon for CAM-03 · Fence Line.
        </p>
      </header>

      <div className="flex flex-1 flex-col gap-4 overflow-auto p-6">
        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-obsidian-border bg-obsidian-900/60 p-3">
          <label className="flex items-center gap-2 text-xs">
            <span className="text-ink-dim">Zone name</span>
            <input
              value={zoneName}
              onChange={(e) => setZoneName(e.target.value)}
              className="rounded border border-obsidian-border bg-obsidian-950 px-2 py-1 text-xs font-mono text-ink focus:outline-none"
            />
          </label>

          <div className="mx-1 h-5 w-px bg-obsidian-border" aria-hidden="true" />

          <button
            type="button"
            onClick={() => setAdding((v) => !v)}
            aria-pressed={adding}
            className={cn(
              "flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2",
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
            onClick={() => setPoints([])}
            className="flex items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-ink-muted hover:text-critical hover:border-critical"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clear
          </button>

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setPoints(DEFAULT_ZONE);
                setSaved(false);
              }}
              className="flex items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-ink-muted hover:text-ink"
            >
              <X className="h-3.5 w-3.5" />
              Cancel
            </button>
            <button
              type="button"
              disabled={points.length < 3}
              onClick={() => setSaved(true)}
              className="flex items-center gap-1.5 rounded-md bg-safety-500 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-obsidian-950 hover:bg-safety-600 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Save className="h-3.5 w-3.5" />
              Save Zone
            </button>
          </div>
        </div>

        {saved && (
          <p role="status" className="text-xs font-mono text-emerald-400">
            Saved "{zoneName}" with {points.length} vertices. (Local only — wire to POST
            /api/zones to persist.)
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
            {points.length >= 2 && (
              <polygon
                points={polygonStr}
                fill="rgba(255,59,48,0.15)"
                stroke="#FF3B30"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            )}
          </svg>

          {points.map((p, i) => (
            <button
              key={i}
              type="button"
              aria-label={`Vertex ${i + 1}, drag to reposition`}
              onMouseDown={(e) => {
                e.stopPropagation();
                setDragIndex(i);
              }}
              className="absolute h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-critical bg-obsidian-950 hover:scale-125 focus-visible:scale-125"
              style={{ left: `${p.x}%`, top: `${p.y}%` }}
            />
          ))}

          {points.length === 0 && (
            <p className="absolute inset-0 flex items-center justify-center text-xs text-ink-dim">
              Click "Add Point" then click on the frame to start drawing the zone.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
