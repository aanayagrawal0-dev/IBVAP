/**
 * Operator session — now backed by REAL server-side auth (Phase 5).
 *
 * login() authenticates against the FastAPI backend, which validates the
 * password (PBKDF2, server-side) and issues an opaque session token. The token
 * is attached as `Authorization: Bearer` to JSON calls (see apiFetch) and as
 * `?token=` for URLs that can't set headers — the MJPEG stream, WebSocket, and
 * thumbnail/export links (see withToken). The backend enforces the session and
 * department RBAC on every protected route; this is no longer a client-only gate.
 */

import { API_BASE } from "@/lib/config";

export type Role = "viewer" | "operator" | "admin";

export type OperatorSession = {
  token: string;
  username: string;
  role: Role;
  department: string;
  displayName: string;
};

const SESSION_KEY = "ibvap_operator_session";
const ROLE_RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 };

export async function login(username: string, password: string): Promise<OperatorSession | null> {
  try {
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.trim(), password }),
    });
    if (!res.ok) return null;
    const data = await res.json();
    const session: OperatorSession = {
      token: data.token,
      username: data.user.username,
      role: data.user.role,
      department: data.user.department,
      displayName: data.user.display_name,
    };
    try {
      localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    } catch {
      // localStorage unavailable — caller still gets the session for this load.
    }
    return session;
  } catch {
    return null; // network / backend down
  }
}

export async function logout(): Promise<void> {
  if (getSession()) {
    try {
      await fetch(`${API_BASE}/api/auth/logout`, { method: "POST", headers: authHeader() });
    } catch {
      // best effort — clear locally regardless
    }
  }
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
    if (typeof parsed?.token === "string" && typeof parsed?.username === "string") {
      return parsed as OperatorSession;
    }
    return null;
  } catch {
    return null;
  }
}

export function getToken(): string | null {
  return getSession()?.token ?? null;
}

export function authHeader(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export function hasRole(session: OperatorSession | null, min: Role): boolean {
  return !!session && ROLE_RANK[session.role] >= ROLE_RANK[min];
}

/** Confirm the stored session is still valid server-side. Clears it on an
 * explicit 401; tolerates a network blip by keeping the local session. */
export async function validateSession(): Promise<OperatorSession | null> {
  const s = getSession();
  if (!s) return null;
  try {
    const res = await fetch(`${API_BASE}/api/auth/me`, { headers: authHeader() });
    if (res.status === 401) {
      try {
        localStorage.removeItem(SESSION_KEY);
      } catch {
        // ignore
      }
      return null;
    }
    return s;
  } catch {
    return s;
  }
}

/** Append the session token as a query param for URLs consumed by <img>,
 * <a href>, window.open or WebSocket, which can't carry an auth header. */
export function withToken(url: string): string {
  const t = getToken();
  if (!t) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
}

/** fetch() with the Authorization header injected. */
export async function apiFetch(url: string, opts: RequestInit = {}): Promise<Response> {
  return fetch(url, { ...opts, headers: { ...(opts.headers || {}), ...authHeader() } });
}
