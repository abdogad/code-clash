import { useApp, useDispatch } from "../../state/DuelContext";
import { Avatar } from "../../components/Avatar";
import { RatingCounter } from "../../components/RatingCounter";
import { api } from "../../hooks/api";
import { clock, initial } from "../../lib/format";
import styles from "./Result.module.css";

type Change = { from: number; to: number } | null;

export function Result() {
  const { result } = useApp();
  const dispatch = useDispatch();
  if (!result) return null;

  const { won, draw } = result;
  const accent = won ? "var(--you)" : draw ? "var(--gold)" : "var(--opp)";
  const glow = won
    ? "rgba(90,162,255,.22)"
    : draw
      ? "rgba(240,184,64,.2)"
      : "rgba(240,98,95,.22)";
  const title = draw ? "DRAW" : won ? "VICTORY" : "DEFEAT";
  const sub = draw
    ? "Time cap reached — no winner."
    : result.outcome === "FORFEIT"
      ? won
        ? "Your opponent forfeited."
        : "You forfeited the duel."
      : won
        ? "You submitted the first accepted solution."
        : "Your opponent got there first.";
  const verdict = won && result.outcome === "WIN" ? "AC" : "—";

  const toLobby = () => {
    dispatch({ type: "LEAVE_DUEL" });
    dispatch({ type: "SET_SCREEN", screen: "lobby" });
  };

  // Re-queue from the result screen. Like Lobby.findOpponent, await the join
  // before the queue screen polls, or its first /queue/status reads "idle".
  const findNext = async () => {
    dispatch({ type: "LEAVE_DUEL" });
    const res = await api("/queue/join", {});
    if (!res.ok) return; // 401 -> global handler signs out; other errors: stay put
    dispatch({ type: "SET_SCREEN", screen: "queue" });
  };

  return (
    <div className={styles.screen}>
      <div className={styles.result}>
        <div
          className={styles.glow}
          style={{ background: `radial-gradient(circle, ${glow}, transparent 65%)` }}
        />
        <div className={styles.kicker}>DUEL COMPLETE</div>
        <h1
          className={styles.title}
          style={{ color: accent, textShadow: `0 0 40px ${glow}` }}
        >
          {title}
        </h1>
        <p className={styles.sub}>{sub}</p>

        <div className={styles.card}>
          <div className={styles.elo}>
            <EloTile side="you" name={result.youName} change={result.you} />
            <EloTile side="opp" name={result.oppName} change={result.opp} />
          </div>
          <div className={styles.meta}>
            <Meta label="SOLVE TIME" value={clock(result.solveTime)} />
            <Meta label="VERDICT" value={verdict} good={verdict === "AC"} />
            <Meta label="PROBLEM" value={result.problemTitle || "—"} />
          </div>
        </div>

        <div className={styles.actions}>
          <button className={styles.primary} onClick={toLobby}>
            Back to arena
          </button>
          <button className={styles.ghost} onClick={findNext}>
            Find next opponent
          </button>
        </div>
      </div>
    </div>
  );
}

function EloTile({
  side,
  name,
  change,
}: {
  side: "you" | "opp";
  name: string;
  change: Change;
}) {
  return (
    <div
      className={`${styles.tile} ${side === "you" ? styles.tileYou : styles.tileOpp}`}
    >
      <div className={styles.tileHead}>
        <Avatar side={side} size="result" letter={initial(name)} />
        <span className={styles.name}>{name}</span>
      </div>
      {change ? (
        <>
          <div className={styles.nums}>
            <RatingCounter
              className={styles.value}
              from={change.from}
              to={change.to}
            />
            <Delta from={change.from} to={change.to} />
          </div>
          <div className={styles.was}>was {change.from}</div>
        </>
      ) : (
        <>
          <div className={styles.nums}>
            <span className={styles.value}>—</span>
          </div>
          <div className={styles.was}>unchanged</div>
        </>
      )}
    </div>
  );
}

function Delta({ from, to }: { from: number; to: number }) {
  const d = to - from;
  return (
    <span className={`${styles.delta} ${d >= 0 ? styles.up : styles.down}`}>
      {d > 0 ? `+${d}` : `${d}`}
    </span>
  );
}

function Meta({
  label,
  value,
  good = false,
}: {
  label: string;
  value: string;
  good?: boolean;
}) {
  return (
    <div className={styles.metaItem}>
      <div className={`${styles.metaNum} ${good ? styles.metaNumGood : ""}`}>
        {value}
      </div>
      <div className={styles.metaLabel}>{label}</div>
    </div>
  );
}
