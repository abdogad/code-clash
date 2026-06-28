/* The single screen state machine (useReducer). One AppState, one reducer.

   - The websocket is the source of truth for opponent state, judging, timing and
     Elo: WS events flow in as INGEST_EVENT and are the only thing that mutates live
     duel state. The client never decides outcomes.
   - Lobby/queue list data (leaderboard, history) is REST-driven and screen-local,
     so it lives in the screens; this reducer holds only the cross-screen machine +
     the duel/result slices.
   - Cosmetic animations aren't modeled here; they never gate a transition. */

import type {
  Attempt,
  FinishedPayload,
  JudgingProgressPayload,
  Lang,
  Me,
  Outcome,
  Player,
  Problem,
  Screen,
  SubmissionPayload,
  VerdictPayload,
  WsEnvelope,
} from "./types";

/** Live judging progress (tests passed so far / total), or null when no
 *  submission is in flight or the test count isn't known yet. */
export type Progress = { passed: number; total: number } | null;

export interface DuelSlice {
  matchId: string;
  oppId: number;
  startedAt: number; // ms epoch, from GET /api/matches (server-owned start)
  players: Record<string, Player>;
  problem: Problem | null;
  lang: Lang;
  submitting: boolean;
  myProgress: Progress; // your live per-test judging progress
  myAttempts: Attempt[];
  oppConnected: boolean;
  oppStatus: string; // synthesized from connect/submission/verdict events
  oppJudging: boolean;
  oppProgress: Progress; // opponent's live per-test judging progress
  oppAttempts: Attempt[];
}

export interface ResultSlice {
  outcome: Outcome;
  won: boolean;
  draw: boolean;
  reason: string;
  youName: string;
  oppName: string;
  you: { from: number; to: number } | null;
  opp: { from: number; to: number } | null;
  solveTime: number; // seconds
  problemTitle: string;
}

export interface AppState {
  screen: Screen;
  me: Me | null;
  youRating: number; // carried across screens; updated after a result
  duel: DuelSlice | null;
  result: ResultSlice | null;
}

export const initialState: AppState = {
  screen: "auth",
  me: null,
  youRating: 0,
  duel: null,
  result: null,
};

export type Action =
  | { type: "SET_SCREEN"; screen: Screen }
  | { type: "SET_ME"; me: Me }
  | { type: "LOGOUT" }
  | {
      type: "ENTER_DUEL";
      matchId: string;
      oppId: number;
      startedAt: number;
      players: Record<string, Player>;
      problem: Problem;
    }
  | { type: "SET_LANG"; lang: Lang }
  | { type: "SET_SUBMITTING"; submitting: boolean }
  | { type: "INGEST_EVENT"; event: WsEnvelope }
  | { type: "LEAVE_DUEL" };

/** seconds elapsed since the (server-owned) duel start. */
function elapsed(duel: DuelSlice): number {
  return Math.max(0, Math.floor((Date.now() - duel.startedAt) / 1000));
}

function attemptFrom(p: VerdictPayload, at: number, id: number): Attempt {
  return {
    id,
    verdict: p.verdict,
    testsPassed: p.tests_passed,
    testsTotal: p.tests_total,
    at,
  };
}

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "SET_SCREEN":
      return { ...state, screen: action.screen };

    case "SET_ME":
      return { ...state, me: action.me, youRating: action.me.rating };

    case "LOGOUT":
      return { ...initialState };

    case "ENTER_DUEL":
      return {
        ...state,
        screen: "duel",
        result: null,
        duel: {
          matchId: action.matchId,
          oppId: action.oppId,
          startedAt: action.startedAt,
          players: action.players,
          problem: action.problem,
          lang: state.duel?.lang ?? "python",
          submitting: false,
          myProgress: null,
          myAttempts: [],
          oppConnected: false,
          oppStatus: "Reading the problem…",
          oppJudging: false,
          oppProgress: null,
          oppAttempts: [],
        },
      };

    case "SET_LANG":
      if (!state.duel) return state;
      return { ...state, duel: { ...state.duel, lang: action.lang } };

    case "SET_SUBMITTING":
      if (!state.duel) return state;
      return { ...state, duel: { ...state.duel, submitting: action.submitting } };

    case "INGEST_EVENT":
      return ingest(state, action.event);

    case "LEAVE_DUEL":
      return { ...state, duel: null };

    default:
      return state;
  }
}

/* --- websocket reducer: the only place live duel state changes ----------- */

function ingest(state: AppState, e: WsEnvelope): AppState {
  const duel = state.duel;
  if (!duel) return state;
  const me = state.me;

  switch (e.type) {
    case "player_connected": {
      const p = e.payload as { user_id: number };
      if (p.user_id !== duel.oppId) return state;
      const oppStatus =
        duel.oppStatus === "Reading the problem…" ? "In the arena" : duel.oppStatus;
      return { ...state, duel: { ...duel, oppConnected: true, oppStatus } };
    }

    case "player_disconnected": {
      const p = e.payload as { user_id: number };
      if (p.user_id !== duel.oppId) return state;
      return {
        ...state,
        duel: {
          ...duel,
          oppConnected: false,
          oppJudging: false,
          oppStatus: "Disconnected — forfeit timer running",
        },
      };
    }

    case "submission": {
      const p = e.payload as SubmissionPayload;
      if (me && p.user_id === me.id) {
        return { ...state, duel: { ...duel, submitting: true, myProgress: null } };
      }
      return {
        ...state,
        duel: {
          ...duel,
          oppJudging: true,
          oppStatus: "Submitted · judging…",
          oppProgress: null,
        },
      };
    }

    case "judging_progress": {
      const p = e.payload as JudgingProgressPayload;
      const prog = { passed: p.tests_passed, total: p.tests_total };
      if (me && p.user_id === me.id) {
        return { ...state, duel: { ...duel, myProgress: prog } };
      }
      return { ...state, duel: { ...duel, oppProgress: prog } };
    }

    case "verdict": {
      const p = e.payload as VerdictPayload;
      const attempt = attemptFrom(p, elapsed(duel), e.seq);
      if (me && p.user_id === me.id) {
        return {
          ...state,
          duel: {
            ...duel,
            submitting: false,
            myProgress: null,
            myAttempts: [attempt, ...duel.myAttempts],
          },
        };
      }
      const oppStatus =
        p.verdict === "AC" ? "Solved it." : `Got ${p.verdict} — back to it`;
      return {
        ...state,
        duel: {
          ...duel,
          oppJudging: false,
          oppStatus,
          oppProgress: null,
          oppAttempts: [attempt, ...duel.oppAttempts],
        },
      };
    }

    case "finished":
      return finish(state, duel, e.payload as FinishedPayload);

    // "started" carries players/problem_id we already loaded via REST — no-op.
    default:
      return state;
  }
}

function finish(state: AppState, duel: DuelSlice, p: FinishedPayload): AppState {
  const me = state.me;
  const won = !!me && p.winner_id === me.id;
  const draw = p.outcome === "DRAW" || p.outcome === "VOID";

  const mine = me ? p.ratings[String(me.id)] : null;
  const theirs = p.ratings[String(duel.oppId)];

  const result: ResultSlice = {
    outcome: p.outcome,
    won,
    draw,
    reason: p.reason,
    youName: me?.username ?? duel.players[String(me?.id)]?.username ?? "you",
    oppName: duel.players[String(duel.oppId)]?.username ?? "opponent",
    you: mine ? { from: mine.old, to: mine.new } : null,
    opp: theirs ? { from: theirs.old, to: theirs.new } : null,
    solveTime: elapsed(duel),
    problemTitle: duel.problem?.title ?? "",
  };

  return {
    ...state,
    screen: "result",
    result,
    youRating: mine ? mine.new : state.youRating,
    me: me && mine ? { ...me, rating: mine.new } : me,
  };
}
