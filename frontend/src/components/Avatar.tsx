import styles from "./Avatar.module.css";

export type AvatarSide = "you" | "opp" | "neutral";
export type AvatarSize =
  | "row"
  | "chip"
  | "standing"
  | "hud"
  | "panel"
  | "result"
  | "queue"
  | "versus";

export function Avatar({
  side,
  size,
  letter,
}: {
  side: AvatarSide;
  size: AvatarSize;
  letter: string;
}) {
  return (
    <div className={`${styles.avatar} ${styles[side]} ${styles[size]}`}>
      {letter}
    </div>
  );
}
