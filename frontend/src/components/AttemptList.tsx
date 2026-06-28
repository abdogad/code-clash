import type { Attempt } from "../state/types";
import { VerdictPill } from "./VerdictPill";
import { clock } from "../lib/format";
import { attemptNote } from "../lib/verdict";
import styles from "./AttemptList.module.css";

export function AttemptList({
  attempts,
  emptyText,
  compact = false,
}: {
  attempts: Attempt[];
  emptyText: string;
  compact?: boolean;
}) {
  if (attempts.length === 0) {
    return <div className={styles.empty}>{emptyText}</div>;
  }
  return (
    <>
      {attempts.map((a) => (
        <div
          key={a.id}
          className={`${styles.attempt} ${compact ? styles.compact : ""}`}
        >
          <VerdictPill verdict={a.verdict} />
          <span className={styles.note}>{attemptNote(a)}</span>
          <span className={styles.time}>{clock(a.at)}</span>
        </div>
      ))}
    </>
  );
}
