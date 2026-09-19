export type Severity = "critical" | "warning" | "info";

export interface Alert {
  id: string;
  severity: Severity;
  title: string;
  description: string;
  camera: string;
  timestamp: string;
}

// The camera list is no longer hardcoded here — the Camera Registry
// (backend camera_store + /api/cameras) is the source of truth, surfaced
// through lib/cameras.ts. See the Live / Zone-config / History pages.

export const initialAlerts: Alert[] = [
  {
    id: "a1",
    severity: "critical",
    title: "FENCE BREACH",
    description: "Multiple thermal signatures detected near Sector C fence line.",
    camera: "CAM-03",
    timestamp: "2s ago",
  },
  {
    id: "a2",
    severity: "warning",
    title: "VEHICLE DETECTED",
    description: "Unidentified vehicle stopped on access road. Conf. 0.88",
    camera: "CAM-04",
    timestamp: "45s ago",
  },
  {
    id: "a3",
    severity: "info",
    title: "SYS COMM",
    description: "Routine telemetry sync completed successfully.",
    camera: "SYS-CORE",
    timestamp: "3m ago",
  },
  {
    id: "a4",
    severity: "info",
    title: "MOTION",
    description: "Small animal detected CAM-02.",
    camera: "CAM-02",
    timestamp: "8m ago",
  },
];

// Pool of alerts the live feed page cycles in over time, to demonstrate
// the animated-list behavior without needing a real backend feed yet.
export const alertStream: Omit<Alert, "id" | "timestamp">[] = [
  {
    severity: "critical",
    title: "FENCE BREACH",
    description: "Tracked object crossed restricted-zone boundary, Sector C.",
    camera: "CAM-03",
  },
  {
    severity: "warning",
    title: "LOITERING",
    description: "Dwell time exceeded 45s near perimeter, CAM-01.",
    camera: "CAM-01",
  },
  {
    severity: "warning",
    title: "VEHICLE DETECTED",
    description: "Vehicle approaching access road at elevated speed.",
    camera: "CAM-04",
  },
  {
    severity: "info",
    title: "PERSON DETECTED",
    description: "Human tracked, ID #17, confidence 0.93.",
    camera: "CAM-02",
  },
  {
    severity: "info",
    title: "NIGHT MOTION",
    description: "Low-light enhancement engaged, motion confirmed.",
    camera: "CAM-01",
  },
];

// Event history and analytics are no longer mocked here — the History page
// reads real database-backed events (lib/history.ts) and the Analytics page
// reads live stats + camera health from /api/analytics/summary (lib/analytics.ts).
// The remaining exports above (initialAlerts / alertStream) are only the
// offline fallback for the Live alert feed when the WebSocket is unreachable.
