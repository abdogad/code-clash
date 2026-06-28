import { Avatar } from "../../components/Avatar";
import { AttemptList } from "../../components/AttemptList";
import type { Progress } from "../../state/duelMachine";
import type { Attempt, Player } from "../../state/types";
import { initial } from "../../lib/format";
import styles from "./OpponentPanel.module.css";

/* Everything here is driven by the live opponent-state websocket. We never see
   their code — only connect/submission/verdict, from which the status text is
   synthesized in the reducer. */
export function OpponentPanel({
  opp,
  connected,
  status,
  judging,
  progress,
  attempts,
}: {
  opp: Player;
  connected: boolean;
  status: string;
  judging: boolean;
  progress: Progress;
  attempts: Attempt[];
}) {
  const determinate = !!progress && progress.total > 0;
  const pct = determinate
    ? Math.round((progress!.passed / progress!.total) * 100)
    : 0;
  return (
    <div className={styles.telemetry}>
      <div className={styles.head}>
        <span className={styles.label}>OPPONENT</span>
        <span
          className={`${styles.conn} ${connected ? styles.online : styles.offline}`}
        >
          <span className={styles.dot} />
          {connected ? "connected" : "disconnected"}
        </span>
      </div>

      <div className={styles.id}>
        <Avatar side="opp" size="panel" letter={initial(opp.username)} />
        <div>
          <div className={styles.name}>{opp.username}</div>
          <div className={styles.rating}>{opp.rating}</div>
        </div>
      </div>

      <div className={styles.status}>
        <div className={styles.statusLabel}>STATUS</div>
        <div className={styles.statusRow}>
          <span className={styles.statusDot} />
          <span className={styles.statusText}>{status}</span>
        </div>
        {judging && (
          <>
            <div className={styles.sweep}>
              {determinate ? (
                <div className={styles.sweepBar} style={{ width: `${pct}%` }} />
              ) : (
                <div className={styles.sweepFill} />
              )}
            </div>
            {determinate && (
              <div className={styles.sweepCount}>
                {progress!.passed} / {progress!.total} tests
              </div>
            )}
          </>
        )}
      </div>

      <div className={styles.attemptsLabel}>THEIR ATTEMPTS</div>
      <AttemptList attempts={attempts} emptyText="No submissions yet." compact />

      <p className={styles.foot}>
        You see their verdicts and timing — never their code.
      </p>
    </div>
  );
}
