"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  RefreshCw,
  Plus,
  Upload,
  Download,
  Trash2,
  Save,
  X,
  AlertTriangle,
  CheckCircle2,
  Radio,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  fetchCameras,
  createCamera,
  updateCamera,
  deleteCamera,
  importCamerasCsv,
  exportCamerasCsvUrl,
  CONNECTIVITY_LABEL,
  type RegistryCamera,
  type CameraInput,
  type ImportResult,
} from "@/lib/cameras";

// Leaflet is loaded from CDN at runtime (client-only) rather than bundled —
// avoids react-leaflet/SSR "window is not defined" headaches and keeps the
// dependency footprint zero. This is the app itself, so a CDN is fine.
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
// Dark basemap so the map fits the obsidian theme (no API key required).
const DARK_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

const STATUS_COLOR: Record<RegistryCamera["connectivity"], string> = {
  online: "#22C55E",
  offline: "#F5A623",
  disabled: "#71717A",
  "no-source": "#3B82F6",
};

function loadLeaflet(): Promise<any> {
  return new Promise((resolve, reject) => {
    const w = window as any;
    if (w.L) return resolve(w.L);

    if (!document.querySelector(`link[href="${LEAFLET_CSS}"]`)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = LEAFLET_CSS;
      document.head.appendChild(link);
    }

    const existing = document.querySelector(`script[src="${LEAFLET_JS}"]`) as HTMLScriptElement | null;
    if (existing) {
      if (w.L) return resolve(w.L);
      existing.addEventListener("load", () => resolve((window as any).L));
      existing.addEventListener("error", reject);
      return;
    }

    const script = document.createElement("script");
    script.src = LEAFLET_JS;
    script.async = true;
    script.onload = () => resolve((window as any).L);
    script.onerror = () => reject(new Error("Failed to load Leaflet from CDN"));
    document.head.appendChild(script);
  });
}

type FormState = {
  id: string;
  name: string;
  department: string;
  lat: string;
  lon: string;
  source_spec: string;
  ownership: string;
  storage_details: string;
  enabled: boolean;
};

const EMPTY_FORM: FormState = {
  id: "",
  name: "",
  department: "",
  lat: "",
  lon: "",
  source_spec: "",
  ownership: "",
  storage_details: "",
  enabled: true,
};

type Banner = { kind: "ok" | "error"; message: string } | null;

export default function RegistryPage() {
  const [cameras, setCameras] = useState<RegistryCamera[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [banner, setBanner] = useState<Banner>(null);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [mapError, setMapError] = useState(false);

  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const overlaysRef = useRef<any[]>([]); // layer groups + control to tear down on re-render
  const controlRef = useRef<any>(null);

  const loadCameras = useCallback(async () => {
    setLoading(true);
    try {
      const cams = await fetchCameras();
      setCameras(cams);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load cameras.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCameras();
  }, [loadCameras]);

  // ---- Map: create once ---------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    loadLeaflet()
      .then((L) => {
        if (cancelled || mapRef.current || !mapContainerRef.current) return;
        const map = L.map(mapContainerRef.current, { center: [22.6, 72.4], zoom: 7 });
        L.tileLayer(DARK_TILES, { attribution: TILE_ATTR, maxZoom: 19 }).addTo(map);
        mapRef.current = map;
        setMapError(false);
        // Nudge Leaflet to recompute size once its container is laid out.
        setTimeout(() => map.invalidateSize(), 200);
      })
      .catch(() => {
        if (!cancelled) setMapError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ---- Map: (re)plot markers whenever the camera list changes -------------
  useEffect(() => {
    const L = (window as any).L;
    const map = mapRef.current;
    if (!L || !map) return;

    // Tear down previous overlays + layer control.
    if (controlRef.current) {
      map.removeControl(controlRef.current);
      controlRef.current = null;
    }
    overlaysRef.current.forEach((layer) => map.removeLayer(layer));
    overlaysRef.current = [];

    // One toggleable overlay layer per department (the "layers" requirement),
    // markers colored by connectivity status, type/ownership in the popup.
    const byDept = new Map<string, any>();
    const bounds: [number, number][] = [];

    cameras.forEach((cam) => {
      if (cam.lat == null || cam.lon == null) return;
      const dept = cam.department || "Unassigned";
      if (!byDept.has(dept)) byDept.set(dept, L.layerGroup());
      const color = STATUS_COLOR[cam.connectivity] ?? "#71717A";
      const marker = L.marker([cam.lat, cam.lon], {
        icon: L.divIcon({
          className: "",
          html: `<span style="display:block;width:15px;height:15px;border-radius:9999px;background:${color};border:2px solid #08090B;box-shadow:0 0 0 1px ${color}"></span>`,
          iconSize: [15, 15],
          iconAnchor: [7, 7],
        }),
      });
      marker.bindPopup(
        `<div style="font-family:system-ui;font-size:12px;line-height:1.5">
           <strong>${cam.id}</strong> — ${escapeHtml(cam.name)}<br/>
           Dept: ${escapeHtml(dept)}<br/>
           Type: ${cam.camera_type} &middot; ${CONNECTIVITY_LABEL[cam.connectivity]}<br/>
           Owner: ${escapeHtml(cam.ownership || "—")}
         </div>`
      );
      marker.on("click", () => beginEdit(cam));
      marker.addTo(byDept.get(dept));
      bounds.push([cam.lat, cam.lon]);
    });

    const overlays: Record<string, any> = {};
    byDept.forEach((group, dept) => {
      group.addTo(map);
      overlays[dept] = group;
      overlaysRef.current.push(group);
    });

    if (Object.keys(overlays).length > 0) {
      controlRef.current = L.control.layers(null, overlays, { collapsed: false }).addTo(map);
    }
    if (bounds.length > 0) {
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameras]);

  // ---- Form ---------------------------------------------------------------
  const beginAdd = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setBanner(null);
  };

  const beginEdit = (cam: RegistryCamera) => {
    setEditingId(cam.id);
    setForm({
      id: cam.id,
      name: cam.name ?? "",
      department: cam.department ?? "",
      lat: cam.lat != null ? String(cam.lat) : "",
      lon: cam.lon != null ? String(cam.lon) : "",
      source_spec: cam.source_spec ?? "",
      ownership: cam.ownership ?? "",
      storage_details: cam.storage_details ?? "",
      enabled: cam.enabled,
    });
    setBanner(null);
  };

  const submitForm = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setBanner(null);
    const payload: CameraInput = {
      name: form.name.trim() || undefined,
      department: form.department.trim() || undefined,
      lat: form.lat.trim() === "" ? undefined : Number(form.lat),
      lon: form.lon.trim() === "" ? undefined : Number(form.lon),
      source_spec: form.source_spec,
      ownership: form.ownership.trim() || undefined,
      storage_details: form.storage_details.trim() || undefined,
      enabled: form.enabled,
    };
    try {
      if (editingId) {
        await updateCamera(editingId, payload);
        setBanner({ kind: "ok", message: `Updated ${editingId}.` });
      } else {
        if (form.id.trim()) payload.id = form.id.trim();
        const created = await createCamera(payload);
        setBanner({
          kind: "ok",
          message: `Added ${created.id} — ${created.streaming ? "now streaming (no restart)." : "saved (no live source yet)."}`,
        });
        setEditingId(created.id);
        setForm((f) => ({ ...f, id: created.id }));
      }
      await loadCameras();
    } catch (err) {
      setBanner({ kind: "error", message: err instanceof Error ? err.message : "Save failed." });
    } finally {
      setSaving(false);
    }
  };

  const removeCamera = async (id: string) => {
    if (!window.confirm(`Remove camera ${id}? This stops its feed and deletes its registry entry.`)) return;
    try {
      await deleteCamera(id);
      if (editingId === id) beginAdd();
      setBanner({ kind: "ok", message: `Removed ${id}.` });
      await loadCameras();
    } catch (err) {
      setBanner({ kind: "error", message: err instanceof Error ? err.message : "Delete failed." });
    }
  };

  const toggleEnabled = async (cam: RegistryCamera) => {
    try {
      await updateCamera(cam.id, { enabled: !cam.enabled });
      await loadCameras();
    } catch (err) {
      setBanner({ kind: "error", message: err instanceof Error ? err.message : "Update failed." });
    }
  };

  const onImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file) return;
    setBanner(null);
    setImportResult(null);
    try {
      const text = await file.text();
      const result = await importCamerasCsv(text);
      setImportResult(result);
      setBanner({
        kind: "ok",
        message: `Imported ${result.added.length}, skipped ${result.skipped.length}.`,
      });
      await loadCameras();
    } catch (err) {
      setBanner({ kind: "error", message: err instanceof Error ? err.message : "Import failed." });
    }
  };

  const online = cameras.filter((c) => c.connectivity === "online").length;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-obsidian-border px-6 py-4">
        <div>
          <h1 className="font-headline text-lg font-bold text-ink">Camera Registry</h1>
          <p className="text-xs text-ink-dim">
            The source of truth for every onboarded camera. Add, edit or bulk-import — changes
            start or stop the live feed immediately, no server restart.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full border border-obsidian-border px-3 py-1 text-[10px] font-mono uppercase tracking-wide2 text-ink-dim">
            {cameras.length} cameras · <span className="text-emerald-400">{online} online</span>
          </span>
          <button
            type="button"
            onClick={loadCameras}
            className="flex items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-ink-muted hover:text-ink"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            Refresh
          </button>
          <a
            href={exportCamerasCsvUrl()}
            className="flex items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-ink-muted hover:text-ink"
          >
            <Download className="h-3.5 w-3.5" />
            Export CSV
          </a>
          <label className="flex cursor-pointer items-center gap-1.5 rounded-md border border-obsidian-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide2 text-ink-muted hover:text-ink">
            <Upload className="h-3.5 w-3.5" />
            Import CSV
            <input type="file" accept=".csv,text/csv" onChange={onImportFile} className="hidden" />
          </label>
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

      <div className="grid flex-1 grid-cols-1 gap-4 overflow-auto p-6 xl:grid-cols-[1fr_360px]">
        {/* Map */}
        <div className="flex min-h-[420px] flex-col gap-3">
          <div className="relative min-h-[420px] flex-1 overflow-hidden rounded-lg border border-obsidian-border">
            <div ref={mapContainerRef} className="absolute inset-0 h-full w-full bg-obsidian-800" />
            {mapError && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-obsidian-900/90 text-center text-xs text-ink-dim">
                <AlertTriangle className="h-5 w-5 text-warning" />
                Map couldn&apos;t load (no internet for the Leaflet CDN). The registry table below
                still works.
              </div>
            )}
          </div>
          {/* Legend */}
          <div className="flex flex-wrap gap-3 text-[10px] font-mono uppercase tracking-wide2 text-ink-dim">
            {(Object.keys(STATUS_COLOR) as RegistryCamera["connectivity"][]).map((k) => (
              <span key={k} className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: STATUS_COLOR[k] }} />
                {CONNECTIVITY_LABEL[k]}
              </span>
            ))}
          </div>
        </div>

        {/* Add / Edit form */}
        <form
          onSubmit={submitForm}
          className="flex flex-col gap-3 self-start rounded-lg border border-obsidian-border bg-obsidian-900/60 p-4"
        >
          <div className="flex items-center justify-between">
            <h2 className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">
              {editingId ? `Edit ${editingId}` : "Add camera"}
            </h2>
            {editingId && (
              <button
                type="button"
                onClick={beginAdd}
                className="flex items-center gap-1 text-[10px] font-mono uppercase text-ink-dim hover:text-ink"
              >
                <X className="h-3 w-3" /> New
              </button>
            )}
          </div>

          <Field label="Name">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Junction Camera — Surat"
              className={inputCls}
            />
          </Field>

          {!editingId && (
            <Field label="ID (optional — auto-assigned if blank)">
              <input
                value={form.id}
                onChange={(e) => setForm({ ...form, id: e.target.value })}
                placeholder="CAM-05"
                className={cn(inputCls, "font-mono")}
              />
            </Field>
          )}

          <Field label="Department">
            <input
              value={form.department}
              onChange={(e) => setForm({ ...form, department: e.target.value })}
              placeholder="Traffic Police"
              className={inputCls}
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Latitude">
              <input
                value={form.lat}
                onChange={(e) => setForm({ ...form, lat: e.target.value })}
                placeholder="21.1702"
                inputMode="decimal"
                className={cn(inputCls, "font-mono")}
              />
            </Field>
            <Field label="Longitude">
              <input
                value={form.lon}
                onChange={(e) => setForm({ ...form, lon: e.target.value })}
                placeholder="72.8311"
                inputMode="decimal"
                className={cn(inputCls, "font-mono")}
              />
            </Field>
          </div>

          <Field label="Source (webcam index / rtsp:// / onvif:// / http:// / file path)">
            <input
              value={form.source_spec}
              onChange={(e) => setForm({ ...form, source_spec: e.target.value })}
              placeholder="rtsp://user:pass@10.0.0.5:554/live"
              className={cn(inputCls, "font-mono")}
            />
          </Field>

          <Field label="Ownership">
            <input
              value={form.ownership}
              onChange={(e) => setForm({ ...form, ownership: e.target.value })}
              placeholder="GSP / Municipal Corp"
              className={inputCls}
            />
          </Field>

          <Field label="Storage / retention notes">
            <input
              value={form.storage_details}
              onChange={(e) => setForm({ ...form, storage_details: e.target.value })}
              placeholder="Central VMS, 30-day retention"
              className={inputCls}
            />
          </Field>

          <label className="flex items-center gap-2 text-xs text-ink-muted">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              className="h-3.5 w-3.5 accent-safety-500"
            />
            Enabled (start streaming if it has a source)
          </label>

          <button
            type="submit"
            disabled={saving}
            className="mt-1 flex items-center justify-center gap-1.5 rounded-md bg-safety-500 px-3 py-2 text-xs font-semibold uppercase tracking-wide2 text-obsidian-950 hover:bg-safety-600 disabled:opacity-50"
          >
            {editingId ? <Save className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
            {saving ? "Saving…" : editingId ? "Save changes" : "Add camera"}
          </button>

          {importResult && importResult.errors.length > 0 && (
            <p className="text-[10px] font-mono text-warning">
              Import had {importResult.errors.length} row error(s) — see server logs.
            </p>
          )}
        </form>

        {/* Table (full width under the grid) */}
        <div className="xl:col-span-2">
          <div className="overflow-x-auto rounded-lg border border-obsidian-border">
            <table className="w-full min-w-[820px] text-left text-xs">
              <thead className="bg-obsidian-900 text-[10px] uppercase tracking-wide2 text-ink-dim">
                <tr>
                  {["Camera", "Department", "Type", "Status", "Coordinates", "Source", ""].map((h) => (
                    <th key={h} className="px-3 py-2 font-bold">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loading && cameras.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-3 py-6 text-center text-ink-dim">
                      Loading registry…
                    </td>
                  </tr>
                )}
                {loadError && (
                  <tr>
                    <td colSpan={7} className="px-3 py-6 text-center text-critical">
                      {loadError}
                    </td>
                  </tr>
                )}
                {cameras.map((cam) => (
                  <tr
                    key={cam.id}
                    className={cn(
                      "border-t border-obsidian-border hover:bg-obsidian-800/50",
                      editingId === cam.id && "bg-safety-500/5"
                    )}
                  >
                    <td className="px-3 py-2">
                      <div className="font-mono font-semibold text-ink">{cam.id}</div>
                      <div className="text-ink-dim">{cam.name}</div>
                    </td>
                    <td className="px-3 py-2 text-ink-muted">{cam.department || "—"}</td>
                    <td className="px-3 py-2 font-mono text-ink-muted">{cam.camera_type}</td>
                    <td className="px-3 py-2">
                      <span className="flex items-center gap-1.5">
                        <span
                          className="h-2 w-2 rounded-full"
                          style={{ backgroundColor: STATUS_COLOR[cam.connectivity] }}
                        />
                        {cam.streaming && <Radio className="h-3 w-3 text-emerald-400" />}
                        <span className="text-ink-muted">{CONNECTIVITY_LABEL[cam.connectivity]}</span>
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-ink-dim">
                      {cam.lat != null && cam.lon != null ? `${cam.lat.toFixed(4)}, ${cam.lon.toFixed(4)}` : "—"}
                    </td>
                    <td className="max-w-[220px] truncate px-3 py-2 font-mono text-ink-dim" title={cam.source_spec || ""}>
                      {cam.source_spec || "—"}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => toggleEnabled(cam)}
                          className="rounded border border-obsidian-border px-2 py-1 text-[10px] uppercase text-ink-muted hover:text-ink"
                        >
                          {cam.enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          type="button"
                          onClick={() => beginEdit(cam)}
                          className="rounded border border-obsidian-border px-2 py-1 text-[10px] uppercase text-ink-muted hover:border-safety-500 hover:text-safety-500"
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => removeCamera(cam.id)}
                          aria-label={`Delete ${cam.id}`}
                          className="rounded p-1 text-ink-dim hover:text-critical"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

const inputCls =
  "w-full rounded-md border border-obsidian-border bg-obsidian-950 px-2 py-1.5 text-xs text-ink placeholder:text-ink-dim focus:border-safety-500 focus:outline-none";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-bold uppercase tracking-wide2 text-ink-dim">{label}</span>
      {children}
    </label>
  );
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string
  ));
}
