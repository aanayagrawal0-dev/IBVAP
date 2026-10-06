"use client";

import { useEffect, useState } from "react";
import { Bell, Settings, User, Search, Wind, Thermometer, Eye, ShieldAlert } from "lucide-react";
import { VideoPanel } from "@/components/video-panel";
import { AnimatedAlertList } from "@/components/animated-alert-list";
import { cn } from "@/lib/utils";
import { initialAlerts, alertStream, type Alert } from "@/lib/mock-data";
import { API_BASE, WS_ALERTS_URL } from "@/lib/config";
import {
  fetchCameraOptions,
  loadLiveCamera,
  snapshotUrl,
  FALLBACK_CAMERAS,
  type CameraOption,
} from "@/lib/cameras";
import { withToken } from "@/lib/auth";
import { exportRedactedClip } from "@/lib/audit";

let nextAlertId = 100;
const FALLBACK_TIMEOUT_MS = 3000;
const SNAPSHOT_REFRESH_MS = 30_000;

export default function LiveFeedPage() {
  const [cameras, setCameras] = useState<CameraOption[]>(FALLBACK_CAMERAS);
  const [activeCamera, setActiveCamera] = useState(FALLBACK_CAMERAS[0].id);
  const [alerts, setAlerts] = useState<Alert[]>(initialAlerts);
  const [now, setNow] = useState("");
  const [backendConnected, setBackendConnected] = useState(false);
  // Empty set = "All cameras". Otherwise only alerts whose `camera` field
  // is in this set are shown — supports single-camera, multi-camera, or
  // all-camera filtering of the (potentially very busy, multi-camera)
  // live alert feed.
  const [cameraFilter, setCameraFilter] = useState<Set<string>>(new Set());
  const [exportState, setExportState] = useState<"idle" | "exporting" | "error">("idle");
  // Bumped every SNAPSHOT_REFRESH_MS: reloads snapshot tiles + camera status.
  const [tick, setTick] = useState(0);
  const [loadingLive, setLoadingLive] = useState<string | null>(null);

  const handleExportRedacted = async () => {
    setExportState("exporting");
    try {
      await exportRedactedClip(activeCamera, 4);
      setExportState("idle");
    } catch {
      setExportState("error");
      setTimeout(() => setExportState("idle"), 4000);
    }
  };

  // Registry drives the camera list — fetched on mount and on every refresh
  // tick so live/standby flags stay current (falls back to the built-in
  // list only if the backend is unreachable).
  useEffect(() => {
    let cancelled = false;
    fetchCameraOptions().then((all) => {
      // Live cameras and standby (snapshot) cameras; disabled / unselected
      // gateway cameras have neither a stream nor snapshots.
      const cams = all.filter((c) => c.status === "nominal");
      if (cancelled || cams.length === 0) return;
      setCameras(cams);
      setActiveCamera((prev) => {
        if (cams.some((c) => c.id === prev)) return prev;
        return (cams.find((c) => c.live) ?? cams[0]).id;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  // Client-only clock — avoids a server/client render mismatch from
  // formatting a live timestamp during SSR.
  useEffect(() => {
    const tick = () => setNow(new Date().toISOString().slice(0, 19).replace("T", " ") + " UTC");
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  // Real alert feed: subscribes to the FastAPI bridge's WebSocket. If the
  // backend isn't running (or the connection drops), falls back to the
  // mock generator after a short grace period so the UI still demos.
  useEffect(() => {
    let fellBack = false;
    let fallbackInterval: ReturnType<typeof setInterval> | null = null;

    const startFallback = () => {
      if (fellBack) return;
      fellBack = true;
      setBackendConnected(false);
      fallbackInterval = setInterval(() => {
        const next = alertStream[Math.floor(Math.random() * alertStream.length)];
        setAlerts((prev) =>
          [{ ...next, id: `stream-${nextAlertId++}`, timestamp: "just now" }, ...prev].slice(0, 8)
        );
      }, 9000);
    };

    const connectTimeout = setTimeout(startFallback, FALLBACK_TIMEOUT_MS);

    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(withToken(WS_ALERTS_URL));
      ws.onopen = () => {
        clearTimeout(connectTimeout);
        setBackendConnected(true);
      };
      ws.onmessage = (event) => {
        try {
          const alert: Alert = JSON.parse(event.data);
          setAlerts((prev) => [alert, ...prev].slice(0, 8));
        } catch {
          // ignore malformed frames rather than crash the page
        }
      };
      ws.onerror = startFallback;
      ws.onclose = startFallback;
    } catch {
      startFallback();
    }

    return () => {
      clearTimeout(connectTimeout);
      if (fallbackInterval) clearInterval(fallbackInterval);
      ws?.close();
    };
  }, []);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), SNAPSHOT_REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  const active = cameras.find((c) => c.id === activeCamera) ?? cameras[0];

  const goLive = async (camId: string) => {
    setLoadingLive(camId);
    try {
      await loadLiveCamera(camId);
      setActiveCamera(camId);
      setTick((t) => t + 1); // refresh live/standby flags right away
    } catch {
      // stays on its snapshot; the button can be retried
    } finally {
      setLoadingLive(null);
    }
  };

  const filteredAlerts =
    cameraFilter.size === 0 ? alerts : alerts.filter((a) => cameraFilter.has(a.camera));

  const toggleCameraFilter = (camId: string) => {
    setCameraFilter((prev) => {
      const next = new Set(prev);
      if (next.has(camId)) {
        next.delete(camId);
      } else {
        next.add(camId);
      }
      return next;
    });
  };

  return (
    <div className="flex h-screen flex-col">
      {/* Top bar */}
      <header className="flex items-center justify-between border-b border-obsidian-border px-6 py-3">
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-safety-500">
            <span className="h-1.5 w-1.5 rounded-full bg-safety-500 animate-pulse2" />
            SYSTEM ONLINE
          </span>
          <span className="text-xs font-mono text-ink-dim">SECTOR: ALPHA-TANGO</span>
        </div>
        <div className="flex items-center gap-4">
          <label className="relative">
            <span className="sr-only">Search feeds</span>
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-dim"
              aria-hidden="true"
            />
            <input
              type="search"
              placeholder="Search feeds..."
              className="w-52 rounded-md border border-obsidian-border bg-obsidian-900 py-1.5 pl-8 pr-3 text-xs text-ink placeholder:text-ink-dim focus:outline-none"
            />
          </label>
          <button
            type="button"
            onClick={handleExportRedacted}
            disabled={exportState === "exporting"}
            title={`Export a privacy-redacted clip of ${activeCamera} (bystanders auto-blurred)`}
            className={cn(
              "flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-wide2 disabled:opacity-60",
              exportState === "error"
                ? "border-critical/50 text-critical"
                : "border-obsidian-border text-ink-muted hover:border-safety-500 hover:text-safety-500"
            )}
          >
            <ShieldAlert className="h-3.5 w-3.5" />
            {exportState === "exporting" ? "Redacting…" : exportState === "error" ? "Export failed" : "Redacted clip"}
          </button>
          <button
            type="button"
            aria-label="Notifications, 1 unread"
            className="relative rounded-md p-1.5 text-ink-muted hover:bg-obsidian-800 hover:text-ink"
          >
            <Bell className="h-4 w-4" />
            <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-safety-500" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Settings"
            className="rounded-md p-1.5 text-ink-muted hover:bg-obsidian-800 hover:text-ink"
          >
            <Settings className="h-4 w-4" />
          </button>
          <button
            type="button"
            aria-label="Operator profile"
            className="rounded-md p-1.5 text-ink-muted hover:bg-obsidian-800 hover:text-ink"
          >
            <User className="h-4 w-4" />
          </button>
        </div>
      </header>

      {/* Main content */}
      <div className="grid flex-1 grid-cols-1 gap-4 overflow-hidden p-6 lg:grid-cols-[1fr_320px]">
        <div className="flex min-h-0 flex-col gap-4">
          {active?.live ? (
            <VideoPanel
              // Remount on camera switch: each camera has its own stream,
              // thermal state, and load/error status, so a fresh component
              // instance is simpler and safer than manually resetting every
              // piece of that state in place.
              key={activeCamera}
              cameraId={activeCamera}
              cameraLabel={`${activeCamera} / ${active.label} · LIVE${active.ai ? " · AI" : ""}`}
              aiActive={active.ai ?? true}
              timestamp={now}
              streamUrl={withToken(`${API_BASE}/api/stream/${activeCamera}`)}
            />
          ) : (
            <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-obsidian-border bg-obsidian-950">
              <img
                key={`${activeCamera}-${tick}`}
                src={snapshotUrl(activeCamera, tick)}
                alt={`Latest snapshot of ${activeCamera}`}
                className="h-full w-full object-contain"
                onError={(e) => ((e.target as HTMLElement).style.visibility = "hidden")}
              />
              <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/70 to-transparent p-3">
                <span className="text-[10px] font-mono font-semibold uppercase tracking-wide2 text-ink">
                  {activeCamera} / {active?.label} ·{" "}
                  {active?.ai === false ? "Not loaded" : "Snapshot (refreshes every 30s)"}
                </span>
              </div>
              <div className="absolute inset-0 flex items-center justify-center">
                <button
                  type="button"
                  onClick={() => goLive(activeCamera)}
                  disabled={loadingLive !== null}
                  className="rounded-md border border-safety-500 bg-safety-500/20 px-4 py-2 text-xs font-mono font-semibold uppercase tracking-wide2 text-safety-500 hover:bg-safety-500/30 disabled:opacity-50"
                >
                  {loadingLive === activeCamera
                    ? "Loading live… (a few seconds)"
                    : active?.ai === false
                      ? "Load live feed"
                      : "Load live + AI"}
                </button>
              </div>
            </div>
          )}

          {/* Camera thumbnails */}
          <div className="grid min-h-0 grid-cols-2 gap-3 overflow-y-auto pr-1 sm:grid-cols-4 xl:grid-cols-5">
            {cameras.map((cam) => (
              <button
                key={cam.id}
                type="button"
                onClick={() => {
                  // Clicking a tile loads that camera live (the previous one
                  // goes back to standby).
                  setActiveCamera(cam.id);
                  if (!cam.live && loadingLive === null) goLive(cam.id);
                }}
                aria-pressed={activeCamera === cam.id}
                className={cn(
                  "group rounded-md border p-2 text-left transition-colors",
                  activeCamera === cam.id
                    ? "border-safety-500 bg-safety-500/5"
                    : "border-obsidian-border bg-obsidian-900 hover:border-obsidian-600"
                )}
              >
                <div className="relative mb-1.5 aspect-video overflow-hidden rounded bg-gradient-to-br from-obsidian-700 to-obsidian-950">
                  <img
                    key={`${cam.id}-${tick}`}
                    src={snapshotUrl(cam.id, tick)}
                    alt=""
                    className="h-full w-full object-cover"
                    onError={(e) => ((e.target as HTMLElement).style.visibility = "hidden")}
                  />
                  <span
                    className={cn(
                      "absolute left-1 top-1 rounded px-1 text-[8px] font-mono font-bold uppercase",
                      cam.live ? "bg-emerald-500/90 text-black" : "bg-black/70 text-ink-dim"
                    )}
                  >
                    {cam.live ? "Live" : cam.ai === false ? "Click to load" : "Snapshot"}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-1">
                  <span className="truncate text-[10px] font-mono font-semibold text-ink">
                    {cam.id} / {cam.label}
                  </span>
                  <span
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      cam.status === "nominal" ? "bg-emerald-500" : "bg-ink-dim"
                    )}
                    aria-label={cam.status === "nominal" ? "Online" : "Offline"}
                  />
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Right sidebar */}
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto">
          <section
            aria-labelledby="env-heading"
            className="rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4"
          >
            <h2
              id="env-heading"
              className="mb-3 text-[10px] font-bold uppercase tracking-wide2 text-ink-dim"
            >
              Environmental Data
            </h2>
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="flex items-start gap-2">
                <Wind className="mt-0.5 h-3.5 w-3.5 text-ink-dim" aria-hidden="true" />
                <div>
                  <p className="text-ink-dim">WIND SPD / DIR</p>
                  <p className="font-mono font-semibold text-ink">14kts NW</p>
                </div>
              </div>
              <div className="flex items-start gap-2">
                <Thermometer className="mt-0.5 h-3.5 w-3.5 text-ink-dim" aria-hidden="true" />
                <div>
                  <p className="text-ink-dim">TEMP</p>
                  <p className="font-mono font-semibold text-ink">38°C</p>
                </div>
              </div>
              <div className="col-span-2 flex items-start gap-2">
                <Eye className="mt-0.5 h-3.5 w-3.5 text-ink-dim" aria-hidden="true" />
                <div>
                  <p className="text-ink-dim">VISIBILITY</p>
                  <p className="font-mono font-semibold text-safety-500">LOW</p>
                </div>
              </div>
            </div>
          </section>

          <section
            aria-labelledby="alerts-heading"
            className="flex min-h-0 flex-1 flex-col rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4"
          >
            <div className="mb-3 flex items-center justify-between">
              <h2
                id="alerts-heading"
                className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim"
              >
                Live Alerts
              </h2>
              <span
                className={cn(
                  "text-[9px] font-mono uppercase",
                  backendConnected ? "text-emerald-400" : "text-ink-dim"
                )}
              >
                {backendConnected ? "● backend" : "○ demo data"}
              </span>
            </div>
            <div
              role="group"
              aria-label="Filter alerts by camera"
              className="mb-3 flex flex-wrap gap-1.5"
            >
              <button
                type="button"
                onClick={() => setCameraFilter(new Set())}
                aria-pressed={cameraFilter.size === 0}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-[9px] font-mono font-semibold uppercase tracking-wide2 transition-colors",
                  cameraFilter.size === 0
                    ? "border-safety-500 bg-safety-500/15 text-safety-500"
                    : "border-obsidian-border bg-obsidian-900 text-ink-dim hover:border-obsidian-600 hover:text-ink"
                )}
              >
                All
              </button>
              {cameras.map((cam) => {
                const isActive = cameraFilter.has(cam.id);
                return (
                  <button
                    key={cam.id}
                    type="button"
                    onClick={() => toggleCameraFilter(cam.id)}
                    aria-pressed={isActive}
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-[9px] font-mono font-semibold uppercase tracking-wide2 transition-colors",
                      isActive
                        ? "border-safety-500 bg-safety-500/15 text-safety-500"
                        : "border-obsidian-border bg-obsidian-900 text-ink-dim hover:border-obsidian-600 hover:text-ink"
                    )}
                  >
                    {cam.id}
                  </button>
                );
              })}
            </div>
            <div className="overflow-y-auto pr-1">
              {filteredAlerts.length > 0 ? (
                <AnimatedAlertList alerts={filteredAlerts} />
              ) : (
                <p className="py-6 text-center text-[10px] font-mono uppercase tracking-wide2 text-ink-dim">
                  No alerts for this selection
                </p>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
