/**
 * Demo-grade operator gate.
 *
 * IMPORTANT: this is a client-side convenience gate ONLY — it keeps casual
 * / accidental access off the dashboard during a demo (e.g. someone else
 * picking up the laptop). It is explicitly NOT production security:
 *   - Credentials are hardcoded in this bundle, not checked server-side.
 *   - The FastAPI backend enforces no auth of its own; anyone who can reach
 *     its port directly can read the stream/alerts regardless of this gate.
 *   - Session state lives in localStorage and can be cleared/forged by
 *     anyone with devtools access.
 * A real deployment needs server-side auth (e.g. OAuth/SSO + backend-
 * enforced tokens) before this ever touches a real border post.
 */

export type OperatorSession = {
  operatorId: string;
  loginAt: string; // ISO timestamp
};

const SESSION_KEY = "ibvap_operator_session";

// Hardcoded demo roster — swap for real backend-verified auth before any
// real deployment. Passcodes are intentionally simple; this is a hackathon
// demo gate, not a security boundary.
const DEMO_OPERATORS: Record<string, string> = {
  "OP-774": "sentinel2026",
  "OP-118": "border-watch",
};

export function login(operatorId: string, passcode: string): OperatorSession | null {
  const expected = DEMO_OPERATORS[operatorId.trim().toUpperCase()];
  if (!expected || expected !== passcode) return null;

  const session: OperatorSession = {
    operatorId: operatorId.trim().toUpperCase(),
    loginAt: new Date().toISOString(),
  };
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    // localStorage unavailable (e.g. private browsing edge cases) — the
    // caller still gets the session object back for in-memory use this
    // page load, it just won't survive a refresh.
  }
  return session;
}

export function logout() {
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch {
    // ignore
  }
}

export function getSession(): OperatorSession | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (typeof parsed?.operatorId === "string") return parsed as OperatorSession;
    return null;
  } catch {
    return null;
  }
}
