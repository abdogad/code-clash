import type { Verdict } from "../state/types";
import styles from "./VerdictPill.module.css";

export function VerdictPill({ verdict }: { verdict: Verdict }) {
  return <span className={`${styles.pill} ${styles[verdict]}`}>{verdict}</span>;
}
