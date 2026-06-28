/* Tiny REST client. Same-origin in prod (FastAPI serves the SPA); in dev the Vite
   proxy forwards /api -> uvicorn:8000.

   Auth lives in httpOnly cookies the browser sends automatically — the JWT is
   never exposed to JS. We keep only a non-secret `authed` hint in localStorage so
   the UI can decide whether to *attempt* a session (JS can't read the cookie). */

import type { Me, Outcome, Player, Problem, Verdict } from "../state/types";

const AUTHED_KEY = "authed";

export const auth = {
  get isAuthed(): boolean {
    return localStorage.getItem(AUTHED_KEY) === "1";
  },
  markAuthed() {
    localStorage.setItem(AUTHED_KEY, "1");
  },
  clear() {
    localStorage.removeItem(AUTHED_KEY);
  },
};

export interface ApiResult<T> {
  ok: boolean;
  status: number;
  data: T | null;
}

/** POST /auth/refresh — swap an expired access cookie for a fresh one. Returns
    whether a usable session remains.

    Single-flighted: the refresh token rotates on every call, so two requests that
    both 401 at once must not each refresh (the second would present the revoked
    token and get logged out). Concurrent callers share one round-trip. */
let refreshInFlight: Promise<boolean> | null = null;

export function refreshSession(): Promise<boolean> {
  refreshInFlight ??= fetch("/api/auth/refresh", { method: "POST" })
    .then((res) => res.ok)
    .catch(() => false)
    .finally(() => {
      refreshInFlight = null;
    });
  return refreshInFlight;
}

export async function api<T = unknown>(
  path: string,
  body?: unknown,
  method?: string,
): Promise<ApiResult<T>> {
  const send = () =>
    fetch("/api" + path, {
      method: method ?? (body !== undefined ? "POST" : "GET"),
      headers: { "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

  let res = await send();
  // A 401 while signed in usually just means the access token expired: refresh
  // once and replay. (Skip for /auth/refresh to avoid recursing on a dead token.)
  if (res.status === 401 && auth.isAuthed && path !== "/auth/refresh") {
    if (await refreshSession()) res = await send();
  }
  // Still 401 -> the session is gone. Drop the hint and fall back to sign-in.
  // Gated on isAuthed so a bad-password 401 doesn't trigger a phantom logout.
  if (res.status === 401 && auth.isAuthed) {
    auth.clear();
    window.dispatchEvent(new Event("auth-expired"));
  }
  let data: T | null = null;
  try {
    data = (await res.json()) as T;
  } catch {
    /* empty body */
  }
  return { ok: res.ok, status: res.status, data };
}

/* --- response shapes (match the FastAPI routes) ------------------------- */

export interface AuthResponse {
  user: Me;
}

export interface MatchResponse {
  id: string;
  state: "WAITING" | "ACTIVE" | "FINISHED";
  outcome: Outcome | null;
  winner_id: number | null;
  started_at: string;
  finished_at: string | null;
  problem: Problem;
  players: Record<string, Player>;
}

export interface SubmissionResponse {
  id: string;
  verdict: Verdict | "PENDING";
}

export type QueueStatus =
  | { status: "matched"; match_id: string }
  | { status: "queued"; waited_s: number }
  | { status: "idle" };

export interface LeaderboardRow {
  rank: number;
  username: string;
  rating: number;
}

export interface MatchHistoryRow {
  id: string;
  state: "WAITING" | "ACTIVE" | "FINISHED";
  outcome: Outcome | null;
  winner_id: number | null;
  finished_at: string | null;
  title: string;
  opponent_id: number;
  opponent_name: string;
  rating_delta: number | null;
}
