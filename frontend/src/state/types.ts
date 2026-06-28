/* Domain types, matching the backend contract (app/codeclash/*).

   WebSocket envelope (every message): { seq, type, payload }. Event types: started
   | player_connected | player_disconnected | submission | verdict | finished.
   There's no "mine vs opponent" split — compare payload.user_id to your own id. */

// "boot" is the transient state while useBoot restores a session from a stored
// token — shown instead of flashing the sign-in screen to a logged-in user.
export type Screen = "boot" | "auth" | "lobby" | "queue" | "duel" | "result";

export type Lang = "python" | "cpp";

export type Verdict = "AC" | "WA" | "TLE" | "MLE" | "RE" | "CE";

/** match_outcome enum from the schema. */
export type Outcome = "WIN" | "DRAW" | "FORFEIT" | "VOID";

export interface Me {
  id: number;
  username: string;
  rating: number;
}

export interface Player {
  username: string;
  rating: number;
}

export interface Problem {
  id: number;
  title: string;
  statement_md: string;
  time_limit_ms: number;
  band: number;
}

/** One attempt row in the YOUR/THEIR ATTEMPTS lists. */
export interface Attempt {
  id: number; // the event seq — stable React key + dedupe
  verdict: Verdict;
  testsPassed: number;
  testsTotal: number | null;
  at: number; // seconds into the duel when recorded
}

/* --- WebSocket events (server -> client) -------------------------------- */

export interface WsEnvelope<T = unknown> {
  seq: number;
  type: WsEventType;
  payload: T;
}

export type WsEventType =
  | "started"
  | "player_connected"
  | "player_disconnected"
  | "submission"
  | "judging_progress"
  | "verdict"
  | "finished";

/** Cosmetic per-test progress. Broadcast-only (seq=0), so it bypasses the
 *  seq-dedupe in useDuelSocket and never enters the durable event log. */
export interface JudgingProgressPayload {
  user_id: number;
  tests_passed: number;
  tests_total: number;
}

export interface StartedPayload {
  problem_id: number;
  players: Record<string, Player>;
}
export interface PlayerConnPayload {
  user_id: number;
}
export interface SubmissionPayload {
  user_id: number;
  attempt: number;
}
export interface VerdictPayload {
  user_id: number;
  verdict: Verdict;
  tests_passed: number;
  tests_total: number | null;
}
export interface FinishedPayload {
  outcome: Outcome;
  winner_id: number | null;
  reason: string;
  /** { "<user_id>": {old, new} } — or null per user for a VOID. */
  ratings: Record<string, { old: number; new: number } | null>;
}
