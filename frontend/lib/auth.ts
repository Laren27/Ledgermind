// Auth for the LedgerMind frontend.
//
// Token is stored in localStorage — this is a real standalone Next.js app
// (not a claude.ai artifact, where localStorage is disallowed), so this is
// fine for a solo-user portfolio project. If this ever needs to be
// hardened (multi-user, production), move to an httpOnly cookie set by
// the backend instead — localStorage is readable by any script on the
// page, which is a real XSS exposure surface you'd want to close before
// this has real users.

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TOKEN_KEY = "ledgermind_token";

export interface AuthUser {
  role: "viewer" | "analyst" | "admin";
  tenantId: string;
}

export interface StoredSession extends AuthUser {
  accessToken: string;
  expiresAt: number; // epoch ms
}

export async function login(email: string, password: string): Promise<StoredSession> {
  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  if (!res.ok) {
    if (res.status === 401) {
      throw new Error("Invalid email or password.");
    }
    throw new Error(`Login failed (${res.status})`);
  }

  const data = await res.json();
  // TokenResponse: { access_token, token_type, role, tenant_id, expires_in_hours }
  const session: StoredSession = {
    accessToken: data.access_token,
    role: data.role,
    tenantId: data.tenant_id,
    expiresAt: Date.now() + data.expires_in_hours * 60 * 60 * 1000,
  };

  if (typeof window !== "undefined") {
    localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
  }

  return session;
}

export function getSession(): StoredSession | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(TOKEN_KEY);
  if (!raw) return null;

  try {
    const session: StoredSession = JSON.parse(raw);
    if (Date.now() >= session.expiresAt) {
      localStorage.removeItem(TOKEN_KEY);
      return null;
    }
    return session;
  } catch {
    return null;
  }
}

export function logout(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem(TOKEN_KEY);
  }
}
