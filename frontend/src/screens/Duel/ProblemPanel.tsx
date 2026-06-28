import { useEffect, useMemo, useRef } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";
import type { Problem } from "../../state/types";
import styles from "./ProblemPanel.module.css";

// band 1/2/3 seeds the problem's starting rating (1000/1500/2000); show it as a
// difficulty users can read rather than a bare number.
const BAND_LABEL: Record<number, string> = { 1: "EASY", 2: "MEDIUM", 3: "HARD" };

/* Statements are pre-rendered, self-contained HTML (pandoc at import time): figures
   and tables are already HTML, images inlined, samples appended as `.samples`. After
   mount we run KaTeX over the `<span class="math">` spans and add a copy button per
   sample `<pre>`. The card header shows the title, so strip the leading <h1>. */
export function ProblemPanel({ problem }: { problem: Problem }) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const html = useMemo(
    () => problem.statement_md.replace(/^\s*<h1\b[^>]*>[\s\S]*?<\/h1>\s*/i, ""),
    [problem.statement_md],
  );

  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;

    root.querySelectorAll<HTMLElement>("span.math").forEach((span) => {
      katex.render(span.textContent ?? "", span, {
        displayMode: span.classList.contains("display"),
        throwOnError: false,
      });
    });

    root.querySelectorAll<HTMLPreElement>("pre.sample-io").forEach((pre) => {
      const pane = pre.parentElement;
      if (!pane || pane.querySelector(`.${styles.copy}`)) return; // idempotent
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = styles.copy;
      btn.textContent = "Copy";
      btn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(pre.textContent ?? "");
          btn.textContent = "Copied";
          setTimeout(() => (btn.textContent = "Copy"), 1200);
        } catch {
          /* clipboard blocked (non-secure context) — leave the label as is */
        }
      });
      pane.appendChild(btn);
    });
  }, [html]);

  return (
    <div className={styles.statement}>
      <div className={styles.tags}>
        <span className={styles.band}>{BAND_LABEL[problem.band] ?? `BAND ${problem.band}`}</span>
        <span className={styles.meta}>{problem.time_limit_ms} ms</span>
        <span className={styles.meta}>stdin → stdout</span>
      </div>
      <h2 className={styles.title}>{problem.title}</h2>
      <div
        ref={bodyRef}
        className={styles.body}
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}
