"use client";

import { useEffect, useState } from "react";
import { Bell, Settings, User, Search, Wind, Thermometer, Eye } from "lucide-react";
import { VideoPanel } from "@/components/video-panel";
import { AnimatedAlertList } from "@/components/animated-alert-list";
import { cn } from "@/lib/utils";
import { cameras, initialAlerts, alertStream, type Alert } from "@/lib/mock-data";
import { API_BASE, WS_ALERTS_URL } from "@/lib/config";

let nextAlertId = 100;
const FALLBACK_TIMEOUT_MS = 3000;

export default function LiveFeedPage() {
  const [activeCamera, setActiveCamera] = useState(cameras[0].id);
  const [alerts, setAlerts] = useState<Alert[]>(initialAlerts);
  const [now, setNow] = useState("");
  const [backendConnected, setBackendConnected] = useState(false);

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
      ws = new WebSocket(WS_ALERTS_URL);
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

  const active = cameras.find((c) => c.id === activeCamera) ?? cameras[0];

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
          <VideoPanel
            cameraLabel={`CAM-01 / ${active.label}`}
            timestamp={now}
            streamUrl={`${API_BASE}/api/stream/${activeCamera}`}
          />

          {/* Camera thumbnails */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {cameras.map((cam) => (
              <button
                key={cam.id}
                type="button"
                onClick={() => setActiveCamera(cam.id)}
                aria-pressed={activeCamera === cam.id}
                className={cn(
                  "group rounded-md border p-2 text-left transition-colors",
                  activeCamera === cam.id
                    ? "border-safety-500 bg-safety-500/5"
                    : "border-obsidian-border bg-obsidian-900 hover:border-obsidian-600"
                )}
              >
                <div
                  className="mb-1.5 aspect-video rounded bg-gradient-to-br from-obsidian-700 to-obsidian-950"
                  aria-hidden="true"
                />
                <div className="flex items-center justify-between gap-1">
                  <span className="truncate text-[10px] font-mono font-semibold text-ink">
                    {cam.id} / {cam.label}
                  </span>
                  <span
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      cam.status === "alert" ? "bg-critical" : "bg-emerald-500"
                    )}
                    aria-label={cam.status === "alert" ? "Active alert" : "Nominal"}
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
            <div className="overflow-y-auto pr-1">
              <AnimatedAlertList alerts={alerts} />
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
