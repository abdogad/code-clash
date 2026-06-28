import { useEffect } from "react";
import styles from "./ConfirmModal.module.css";

/* A small focus-trap-free confirm dialog. Escape cancels, the backdrop click
   cancels, and the confirm button can be styled as a danger action. */
export function ConfirmModal({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  return (
    <div className={styles.overlay} onMouseDown={busy ? undefined : onCancel}>
      <div
        className={styles.dialog}
        role="alertdialog"
        aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 className={styles.title}>{title}</h2>
        <p className={styles.message}>{message}</p>
        <div className={styles.actions}>
          <button className={styles.cancel} onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            className={`${styles.confirm} ${danger ? styles.danger : ""}`}
            onClick={onConfirm}
            disabled={busy}
            autoFocus
          >
            {busy ? "…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
