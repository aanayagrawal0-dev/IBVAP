"use client";

import { useEffect, useState } from "react";
import { Thermometer } from "lucide-react";
import { cn } from "@/lib/utils";
import { API_BASE } from "@/lib/config";

function BoundingBox({
  top,
  left,
  width,
  height,
  label,
  color = "#4ADE80",
}: {
  top: string;
  left: string;
  width: string;
  height: string;
  label: string;
  color?: string;
}) {
  return (
    <div
      className="absolute border-2"
      style={{ top, left, width, height, borderColor: color }}
      aria-hidden="true"
    >
      <span
        className="absolute -top-5 left-0 whitespace-nowrap px-1 text-[10px] font-mono font-bold"
        style={{ backgroundColor: color, color: "#08090B" }}
      >
        {label}
      </span>
    </div>
  );
}

/**
 * Fallback scene shown when there's no reachable backend stream (or none
 * configured). Boxes/zone overlay here are mock — when a real stream loads
 * instead, those are already baked into the annotated JPEG the backend
 * sends, so this component isn't rendered at all in that case.
 */
function PlaceholderScene() {
  return (
    <>
      <div
        className="absolute inset-0"
        style={{
          background:
            "linear-gradient(180deg, #3a2f2a 0%, #6b4a35 35%, #8a5a3a 48%, #2a1f1a 60%, #14100d 100%)",
          filter: "saturate(0.7) contrast(1.05)",
        }}
      />
      <div
        className="absolute inset-x-0 bottom-0 h-1/2 opacity-30"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.15) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.15) 1px, transparent 1px)",
          backgroundSize: "40px 40px",
          transform: "perspective(300px) rotateX(55deg)",
          transformOrigin: "bottom",
        }}
      />
      <svg
        className="absolute bottom-[18%] left-[8%] h-1/3 w-auto opacity-80"
        viewBox="0 0 40 80"
        fill="#14100d"
        aria-hidden="true"
      >
        <rect x="16" y="30" width="8" height="50" />
        <rect x="10" y="0" width="20" height="30" />
        <rect x="6" y="0" width="28" height="4" />
      </svg>

      <BoundingBox top="52%" left="30%" width="6%" height="16%" label="#12 PERSON 0.91" color="#4ADE80" />
      <BoundingBox top="58%" left="42%" width="12%" height="14%" label="#05 VEHICLE 0.88" color="#4ADE80" />

      <div
        className="absolute right-0 top-0 h-full w-[38%] border-l-2 border-critical"
        style={{
          background:
            "repeating-linear-gradient(135deg, rgba(255,59,48,0.15) 0 10px, rgba(255,59,48,0.05) 10px 20px)",
        }}
        aria-hidden="true"
      >
        <span
          className="absolute left-2 top-2 text-[10px] font-mono font-bold uppercase tracking-wide2 text-critical"
          style={{ writingMode: "vertical-rl" }}
        >
          Restricted Zone
        </span>
      </div>
    </>
  );
}

export function VideoPanel({
  cameraId,
  cameraLabel,
  timestamp,
  streamUrl,
}: {
  /** Which camera this panel is showing, e.g. "CAM-01" — used to hit that
   * camera's own /api/thermal/{cameraId} endpoint. Each camera has fully
   * independent thermal state on the backend now. */
  cameraId: string;
  cameraLabel: string;
  timestamp: string;
  /** MJPEG endpoint, e.g. http://localhost:8000/api/stream/CAM-01. Omit or
   * let it fail to load and this falls back to the placeholder scene. */
  streamUrl?: string;
}) {
  const [streamFailed, setStreamFailed] = useState(false);
  const [thermalOn, setThermalOn] = useState(false);
  const [thermalPending, setThermalPending] = useState(false);
  const showRealStream = Boolean(streamUrl) && !streamFailed;

  // The parent also remounts this component on camera switch (key=
  // {activeCamera}), but resetting here too means this still behaves
  // correctly if VideoPanel is ever reused without that key.
  useEffect(() => {
    setStreamFailed(false);
    setThermalOn(false);
  }, [cameraId]);

  // Reflect the backend's current thermal state on load (and whenever the
  // stream (re)connects) so the button never lies if it was toggled from
  // elsewhere — another tab, another camera switch, or a page refresh.
  useEffect(() => {
    if (!showRealStream) return;
    let cancelled = false;
    fetch(`${API_BASE}/api/thermal/${encodeURIComponent(cameraId)}`)
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled) setThermalOn(Boolean(data.enabled));
      })
      .catch(() => {
        // Backend not reachable for this GET — leave the toggle as-is;
        // the button is disabled anyway when showRealStream is false.
      });
    return () => {
      cancelled = true;
    };
  }, [showRealStream, cameraId]);

  const toggleThermal = async () => {
    if (!showRealStream || thermalPending) return;
    const next = !thermalOn;
    setThermalPending(true);
    setThermalOn(next); // optimistic — most toggles succeed instantly
    try {
      const res = await fetch(`${API_BASE}/api/thermal/${encodeURIComponent(cameraId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (!res.ok) throw new Error(`thermal toggle failed: ${res.status}`);
      const data = await res.json();
      setThermalOn(Boolean(data.enabled));
    } catch {
      setThermalOn(!next); // revert the optimistic flip on failure
    } finally {
      setThermalPending(false);
    }
  };

  return (
    <div className="relative overflow-hidden rounded-lg border border-obsidian-border bg-obsidian-950 aspect-video">
      {showRealStream ? (
        // Backend already draws boxes/zone overlay into the JPEG server-side
        // (see src/pipeline.py's annotators), so no client-side overlay here.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={streamUrl}
          alt={`Live annotated feed — ${cameraLabel}`}
          className="absolute inset-0 h-full w-full object-cover"
          onError={() => setStreamFailed(true)}
        />
      ) : (
        <PlaceholderScene />
      )}

      {/* Top overlay bar */}
      <div className="absolute inset-x-0 top-0 flex items-center justify-between p-3">
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1 rounded bg-critical px-1.5 py-0.5 text-[10px] font-mono font-bold text-obsidian-950">
            <span className="h-1.5 w-1.5 rounded-full bg-obsidian-950 animate-pulse2" />
            {showRealStream ? "LIVE" : "OFFLINE — DEMO FEED"}
          </span>
          <span className="text-[11px] font-mono text-ink">{cameraLabel}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggleThermal}
            disabled={!showRealStream || thermalPending}
            aria-pressed={thermalOn}
            aria-label={
              thermalOn
                ? "Switch off simulated thermal view"
                : "Switch on simulated thermal view for low-light conditions"
            }
            title={
              showRealStream
                ? "Simulated thermal view — false-color night-vision overlay, not a real IR sensor"
                : "Connect a live backend feed to use thermal view"
            }
            className={cn(
              "flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono font-bold transition-colors",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-safety-500",
              thermalOn
                ? "bg-safety-500 text-obsidian-950"
                : "bg-obsidian-950/70 text-ink-muted hover:text-ink",
              (!showRealStream || thermalPending) && "cursor-not-allowed opacity-50"
            )}
          >
            <Thermometer className="h-3 w-3" aria-hidden="true" />
            THERMAL {thermalOn ? "ON" : "OFF"}
          </button>
          <span className="text-[10px] font-mono text-ink-dim">{timestamp}</span>
        </div>
      </div>
      <div className="absolute left-3 top-9 rounded bg-obsidian-950/70 px-1 py-0.5 text-[9px] font-mono text-critical backdrop-blur-sm">
        AI-ANALYTICS: {showRealStream ? "ACTIVE" : "STANDBY"}
        {thermalOn && " · THERMAL: SIMULATED FALSE-COLOR"}
      </div>

      {/* Bottom telemetry bar */}
      <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-obsidian-950/70 px-3 py-1.5 text-[10px] font-mono text-ink-dim backdrop-blur-sm">
        <span>FPS: 59.9</span>
        <span>LAT: 31.4285° N&nbsp;&nbsp;LON: 106.4719° W</span>
        <span>SYS.TEMP: 42°C</span>
      </div>
    </div>
  );
}
