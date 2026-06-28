import { Avatar } from "./Avatar";
import { initial } from "../lib/format";
import styles from "./PlayerChip.module.css";

export function PlayerChip({
  name,
  rating,
  side,
}: {
  name: string;
  rating: number;
  side: "you" | "opp";
}) {
  const right = side === "you";
  const meta = (
    <div className={`${styles.meta} ${right ? styles.metaRight : ""}`}>
      <div className={styles.name}>{name}</div>
      <div className={`${styles.rating} ${styles[side]}`}>{rating}</div>
    </div>
  );
  const avatar = <Avatar side={side} size="hud" letter={initial(name)} />;
  return (
    <div className={styles.player}>
      {right ? (
        <>
          {meta}
          {avatar}
        </>
      ) : (
        <>
          {avatar}
          {meta}
        </>
      )}
    </div>
  );
}
