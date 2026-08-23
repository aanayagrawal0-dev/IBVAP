export type Severity = "critical" | "warning" | "info";

export interface Alert {
  id: string;
  severity: Severity;
  title: string;
  description: string;
  camera: string;
  timestamp: string;
}

export interface Camera {
  id: string;
  label: string;
  status: "nominal" | "alert" | "offline";
}

export interface HistoryEvent {
  id: string;
  timestamp: string;
  camera: string;
  eventType: string;
  trackerId: string;
  severity: Severity;
}

export const cameras: Camera[] = [
  { id: "CAM-01", label: "OUTPOST DUSK", status: "nominal" },
  { id: "CAM-02", label: "PASS", status: "nominal" },
  { id: "CAM-03", label: "FENCE LINE", status: "alert" },
  { id: "CAM-04", label: "GATE", status: "nominal" },
];

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

export const historyEvents: HistoryEvent[] = [
  {
    id: "e1",
    timestamp: "2026-08-23 23:14:05 UTC",
    camera: "CAM-SZ-04",
    eventType: "Perimeter Breach",
    trackerId: "#42",
    severity: "critical",
  },
  {
    id: "e2",
    timestamp: "2026-08-23 22:45:12 UTC",
    camera: "CAM-NZ-12",
    eventType: "Motion Detected — Zone B",
    trackerId: "#38",
    severity: "warning",
  },
  {
    id: "e3",
    timestamp: "2026-08-23 21:00:00 UTC",
    camera: "SYS-CORE",
    eventType: "Routine Diagnostic Complete",
    trackerId: "—",
    severity: "info",
  },
  {
    id: "e4",
    timestamp: "2026-08-23 19:33:45 UTC",
    camera: "CAM-EZ-01",
    eventType: "Signal Lost — Tamper Suspected",
    trackerId: "—",
    severity: "critical",
  },
  {
    id: "e5",
    timestamp: "2026-08-23 18:12:31 UTC",
    camera: "CAM-03",
    eventType: "Vehicle Classified — Truck",
    trackerId: "#31",
    severity: "info",
  },
  {
    id: "e6",
    timestamp: "2026-08-23 17:58:02 UTC",
    camera: "CAM-01",
    eventType: "Loitering — Dwell 62s",
    trackerId: "#29",
    severity: "warning",
  },
];

export const breachTrend = Array.from({ length: 30 }, (_, i) => ({
  day: i + 1,
  breaches: Math.round(120 + Math.random() * 380),
}));

export const activityDensity = {
  hours: ["00h", "04h", "08h", "12h", "16h", "20h", "24h"],
  days: ["Mon", "Wed", "Fri", "Sun"],
  // value 0-1 intensity, deterministic-looking pseudo pattern
  matrix: [
    [0.6, 0.3, 0.2, 0.7, 0.5, 0.8, 0.4],
    [0.4, 0.5, 0.3, 0.9, 0.6, 0.3, 0.5],
    [0.7, 0.2, 0.4, 0.5, 0.8, 0.6, 0.3],
    [0.3, 0.6, 0.5, 0.4, 0.7, 0.5, 0.6],
  ],
};

export const stats = [
  { label: "TOTAL DETECTIONS", value: "4,892", delta: "+12% vs last 24h", positive: true },
  { label: "AVG RESPONSE TIME", value: "1.4s", delta: "-0.2s vs last 24h", positive: true },
  { label: "SYSTEM CONFIDENCE", value: "99.8%", delta: null, positive: true },
];
