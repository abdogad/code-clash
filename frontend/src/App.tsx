/* App — screen switch over the state machine (Auth, Lobby, Matchmaking, Duel,
   Result). The Dev* affordances at the bottom are DEV-only (import.meta.env.DEV):
   skip login, jump into a demo duel, and inject WS events to walk the live states. */

import { Suspense, lazy, useEffect, useRef } from "react";
import type { ReactNode } from "react";
import type { Screen, WsEventType } from "./state/types";
import { useApp, useDispatch } from "./state/DuelContext";
import { useBoot } from "./hooks/useBoot";
import { ChunkErrorBoundary } from "./components/ChunkErrorBoundary";
import { Auth } from "./screens/Auth/Auth";
import { Lobby } from "./screens/Lobby/Lobby";
import styles from "./App.module.css";

// Heavy screens are code-split: the Duel screen pulls in CodeMirror + language
// modes (~600 KB) the login/lobby don't need, so it downloads only when the
// player duels. Vite emits a chunk per dynamic import.
const importDuel = () => import("./screens/Duel/Duel");
const Matchmaking = lazy(() =>
  import("./screens/Matchmaking/Matchmaking").then((m) => ({ default: m.Matchmaking })),
);
const Duel = lazy(() => importDuel().then((m) => ({ default: m.Duel })));
const Result = lazy(() =>
  import("./screens/Result/Result").then((m) => ({ default: m.Result })),
);

export function App() {
  const state = useApp();
  const dispatch = useDispatch();
  useBoot(dispatch);

  // Prefetch the heavy Duel chunk once the player reaches the lobby (or while a
  // returning session boots — they may land straight in a match), so entering a
  // duel never waits on CodeMirror.
  useEffect(() => {
    if (state.screen === "lobby" || state.screen === "boot") void importDuel();
  }, [state.screen]);

  // The API client fires this when it clears an expired session; drop the user
  // back to sign-in from wherever they were.
  useEffect(() => {
    const onExpired = () => dispatch({ type: "LOGOUT" });
    window.addEventListener("auth-expired", onExpired);
    return () => window.removeEventListener("auth-expired", onExpired);
  }, [dispatch]);

  let view: ReactNode;
  switch (state.screen) {
    case "boot":
      view = <ScreenLoading />;
      break;
    case "auth":
      view = (
        <>
          <Auth />
          {import.meta.env.DEV && <DevSkip />}
        </>
      );
      break;
    case "lobby":
      view = (
        <>
          <Lobby />
          {import.meta.env.DEV && <DevDemo />}
        </>
      );
      break;
    case "queue":
      view = <Matchmaking />;
      break;
    case "duel":
      view = state.duel ? (
        <>
          <Duel />
          {import.meta.env.DEV && <DevPanel />}
        </>
      ) : (
        <Placeholder name="duel" />
      );
      break;
    case "result":
      view = state.result ? <Result /> : <Placeholder name="result" />;
      break;
    default:
      view = <Placeholder name="lobby" />;
  }

  return (
    <ChunkErrorBoundary>
      <Suspense fallback={<ScreenLoading />}>{view}</Suspense>
    </ChunkErrorBoundary>
  );
}

/* --- fallback shown while a lazy screen chunk downloads ----------------- */

function ScreenLoading() {
  return (
    <div className={styles.wrap}>
      <div className={styles.kicker}>◆ Loading…</div>
    </div>
  );
}

/* --- generic fallback when a screen's state is missing ----------------- */

function Placeholder({ name }: { name: Screen }) {
  const dispatch = useDispatch();
  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <div className={styles.kicker}>◆ Coming next</div>
        <h1 className={styles.title}>
          {name} <b>screen</b>
        </h1>
        <p className={styles.lead}>
          This screen isn’t built yet. Matchmaking lands in the next increment.
        </p>
        <button
          className={styles.primary}
          onClick={() => dispatch({ type: "SET_SCREEN", screen: "lobby" })}
        >
          Back to arena
        </button>
      </div>
    </div>
  );
}

/* --- dev-only affordances (import.meta.env.DEV) ------------------------ */

// Statements are pre-rendered HTML (pandoc, at import time); this demo blob
// mirrors that shape so the duel screen renders it the same way as real problems.
const DEMO_STATEMENT = `<h1>Sum of Two Numbers</h1>
<p>Read two integers <code>a</code> and <code>b</code> on one line, separated by a
space, and print their sum.</p>
<h2>Input</h2>
<p>A single line with two integers <code>a b</code>.</p>
<h2>Output</h2>
<p>Print a single integer — the value of <code>a + b</code>.</p>
<pre><code>in
2 3
out
5
</code></pre>
`;

function DevSkip() {
  const dispatch = useDispatch();
  return (
    <button
      className={styles.devSkip}
      onClick={() => {
        dispatch({ type: "SET_ME", me: { id: 1, username: "alice", rating: 1200 } });
        dispatch({ type: "SET_SCREEN", screen: "lobby" });
      }}
    >
      dev: skip login →
    </button>
  );
}

function DevDemo() {
  const dispatch = useDispatch();
  return (
    <button
      className={styles.devSkip}
      onClick={() => {
        dispatch({ type: "SET_ME", me: { id: 1, username: "alice", rating: 1200 } });
        dispatch({
          type: "ENTER_DUEL",
          matchId: "demo",
          oppId: 2,
          startedAt: Date.now(),
          players: {
            "1": { username: "alice", rating: 1200 },
            "2": { username: "bob", rating: 1180 },
          },
          problem: {
            id: 1,
            title: "Sum of Two Numbers",
            statement_md: DEMO_STATEMENT,
            time_limit_ms: 2000,
            band: 1,
          },
        });
      }}
    >
      dev: load demo duel →
    </button>
  );
}

function DevPanel() {
  const state = useApp();
  const dispatch = useDispatch();
  const seq = useRef(1000);
  const duel = state.duel;
  if (!duel || !state.me) return null;
  const meId = state.me.id;
  const oppId = duel.oppId;

  const send = (type: WsEventType, payload: unknown) =>
    dispatch({ type: "INGEST_EVENT", event: { seq: seq.current++, type, payload } });

  const ratings = {
    [String(meId)]: { old: 1200, new: 1224 },
    [String(oppId)]: { old: 1180, new: 1156 },
  };

  return (
    <div className={styles.dev}>
      <span className={styles.devLabel}>DEV · inject events</span>
      <button className={styles.btn} onClick={() => send("player_connected", { user_id: oppId })}>
        opp connect
      </button>
      <button className={styles.btn} onClick={() => send("submission", { user_id: oppId, attempt: 1 })}>
        opp submit
      </button>
      <button
        className={styles.btn}
        onClick={() => send("judging_progress", { user_id: oppId, tests_passed: 3, tests_total: 10 })}
      >
        opp 3/10
      </button>
      <button
        className={styles.btn}
        onClick={() => send("verdict", { user_id: oppId, verdict: "WA", tests_passed: 7, tests_total: 10 })}
      >
        opp WA
      </button>
      <button
        className={styles.btn}
        onClick={() => send("verdict", { user_id: oppId, verdict: "AC", tests_passed: 10, tests_total: 10 })}
      >
        opp AC
      </button>
      <button className={styles.btn} onClick={() => send("submission", { user_id: meId, attempt: 1 })}>
        you submit
      </button>
      <button
        className={styles.btn}
        onClick={() => send("judging_progress", { user_id: meId, tests_passed: 4, tests_total: 10 })}
      >
        you 4/10
      </button>
      <button
        className={styles.btn}
        onClick={() => send("verdict", { user_id: meId, verdict: "TLE", tests_passed: 4, tests_total: 10 })}
      >
        you TLE
      </button>
      <button
        className={styles.btn}
        onClick={() => send("verdict", { user_id: meId, verdict: "AC", tests_passed: 10, tests_total: 10 })}
      >
        you AC
      </button>
      <button
        className={styles.btn}
        onClick={() => send("finished", { outcome: "WIN", winner_id: meId, reason: "accepted", ratings })}
      >
        finish: win
      </button>
      <button
        className={styles.btn}
        onClick={() => send("finished", { outcome: "WIN", winner_id: oppId, reason: "accepted", ratings })}
      >
        finish: loss
      </button>
    </div>
  );
}
