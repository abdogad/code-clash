import type { Progress } from "../../state/duelMachine";
import styles from "./JudgingStrip.module.css";

/* Real per-test progress: the judge streams a tick after each passing test, so
   once the test count is known the bar fills test-by-test. Until the first
   tick (count unknown) it falls back to an honest indeterminate sweep. */
export function JudgingStrip({ progress }: { progress?: Progress }) {
  const determinate = !!progress && progress.total > 0;
  const pct = determinate
    ? Math.round((progress!.passed / progress!.total) * 100)
    : 0;
  return (
    <div className={styles.judging}>
      <div className={styles.row}>
        <span className={styles.text}>Judging…</span>
        {determinate && (
          <span className={styles.count}>
            {progress!.passed} / {progress!.total} tests
          </span>
        )}
      </div>
      <div className={styles.track}>
        {determinate ? (
          <div className={styles.bar} style={{ width: `${pct}%` }} />
        ) : (
          <div className={styles.fill} />
        )}
      </div>
    </div>
  );
}
