/* Code-splitting fetches each screen's JS chunk on demand. That fetch can fail
   two ways: a flaky network, or a redeploy that replaced the hashed chunk files
   while this tab still holds the old index.html (its chunk URLs now 404).
   Without a boundary, a failed lazy import unmounts the whole app → blank page.
   We catch it and reload once to pull the fresh build; a timestamp guard stops
   a reload loop if the reload doesn't fix it. */
import { Component, type ReactNode } from "react";
import styles from "../App.module.css";

const RELOAD_KEY = "chunk-reload-at";

function isChunkLoadError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /Loading chunk|dynamically imported module|module script failed|ChunkLoadError/i.test(
    msg,
  );
}

export class ChunkErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    if (!isChunkLoadError(error)) return; // real bugs surface in the fallback
    const last = Number(sessionStorage.getItem(RELOAD_KEY) ?? 0);
    if (Date.now() - last > 10_000) {
      sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
      window.location.reload();
    }
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className={styles.wrap}>
        <div className={styles.card}>
          <div className={styles.kicker}>◆ Couldn’t finish loading</div>
          <p className={styles.lead}>
            The app failed to load part of itself — usually a dropped connection
            or a fresh deploy.
          </p>
          <button className={styles.primary} onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
