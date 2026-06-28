import styles from "./Logo.module.css";

export function Logo({ sm = false }: { sm?: boolean }) {
  return (
    <span className={`${styles.logo} ${sm ? styles.sm : ""}`}>
      <i />
      <i />
    </span>
  );
}
