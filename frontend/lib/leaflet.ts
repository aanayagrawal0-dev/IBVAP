// Shared Leaflet CDN loader + dark-theme tile config, used by the Registry
// map and the Route map. Loaded at runtime (client-only) rather than bundled
// to avoid react-leaflet/SSR "window is not defined" issues and keep the
// dependency footprint at zero. This is the app itself, so a CDN is fine.

export const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
export const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";

// OpenStreetMap's standard tiles (no API key), darkened with a CSS filter to
// match the obsidian theme. CARTO's dark tiles now require an API key and
// render "API KEY REQUIRED" instead of the map.
export const DARK_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

const DARK_TILE_CSS =
  ".leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(0.9) contrast(0.9) saturate(0.6)}";

export function loadLeaflet(): Promise<any> {
  return new Promise((resolve, reject) => {
    if (!document.getElementById("dark-tiles-css")) {
      const style = document.createElement("style");
      style.id = "dark-tiles-css";
      style.textContent = DARK_TILE_CSS; // darken the map only, not markers or lines
      document.head.appendChild(style);
    }

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

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string
  ));
}
