import { Logo } from "../../components/Logo";
import { PlayerChip } from "../../components/PlayerChip";
import type { Progress } from "../../state/duelMachine";
import type { Player } from "../../state/types";
import { clock2 } from "../../lib/format";
import styles from "./DuelHud.module.css";

export const DUEL_CAP_SECONDS = 1800; // 30:00 cap (server-enforced)

/* A thin live track under each player's chip — the at-a-glance "who's ahead"
   that replaces the old opponent side-rail in the Arena layout. Fills test-by-
   test while that player is judging; idle otherwise. */
function ProgressPip({
  judging,
  progress,
  side,
  disconnected = false,
}: {
  judging: boolean;
  progress: Progress;
  side: "you" | "opp";
  disconnected?: boolean;
}) {
  const determinate = judging && !!progress && progress.total > 0;
  const pct = determinate
    ? Math.round((progress!.passed / progress!.total) * 100)
    : 0;
  const label = determinate
    ? `${progress!.passed}/${progress!.total} tests`
    : judging
      ? "judging…"
      : disconnected
        ? "disconnected"
        : "idle";
  return (
    <div className={styles.pip}>
      <div className={styles.pipTrack}>
        {judging && !determinate && <div className={styles.pipSweep} />}
        {determinate && (
          <div
            className={`${styles.pipBar} ${styles[side]}`}
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
      <span
        className={`${styles.pipLabel} ${disconnected && !judging ? styles.pipOff : ""}`}
      >
        {label}
      </span>
    </div>
  );
}

export function DuelHud({
  you,
  opp,
  elapsed,
  onForfeit,
  youJudging,
  youProgress,
  oppJudging,
  oppProgress,
  oppConnected,
}: {
  you: Player;
  opp: Player;
  elapsed: number;
  onForfeit: () => void;
  youJudging: boolean;
  youProgress: Progress;
  oppJudging: boolean;
  oppProgress: Progress;
  oppConnected: boolean;
}) {
  const t = Math.min(elapsed, DUEL_CAP_SECONDS);
  return (
    <div className={styles.hud}>
      <div className={styles.left}>
        <Logo sm />
        <span className={styles.live}>
          <span className={styles.liveDot} />
          LIVE DUEL
        </span>
      </div>

      <div className={styles.score}>
        <div className={styles.player}>
          <PlayerChip name={you.username} rating={you.rating} side="you" />
          <ProgressPip judging={youJudging} progress={youProgress} side="you" />
        </div>
        <span className={styles.vs}>VS</span>
        <div className={styles.player}>
          <PlayerChip name={opp.username} rating={opp.rating} side="opp" />
          <ProgressPip
            judging={oppJudging}
            progress={oppProgress}
            side="opp"
            disconnected={!oppConnected}
          />
        </div>
      </div>

      <div className={styles.right}>
        <div className={styles.timer}>
          {clock2(t)}
          <span className={styles.cap}> / 30:00</span>
        </div>
        <button className={styles.forfeit} onClick={onForfeit}>
          Forfeit
        </button>
      </div>
    </div>
  );
}
