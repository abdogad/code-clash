import { useMemo } from "react";
import CodeMirror, { EditorView } from "@uiw/react-codemirror";
import { oneDark } from "@codemirror/theme-one-dark";
import { python } from "@codemirror/lang-python";
import { cpp } from "@codemirror/lang-cpp";
import type { Progress } from "../../state/duelMachine";
import type { Attempt, Lang } from "../../state/types";
import { AttemptList } from "../../components/AttemptList";
import { JudgingStrip } from "./JudgingStrip";
import styles from "./Editor.module.css";

/* One-Dark gives the syntax palette the spec calls for (keyword #c678dd,
   func #61afef, plain #abb2bf). We override only the *chrome* to the handoff:
   --bg-hud surface, #3a4453 line numbers, blue caret, no active-line tint. */
const chrome = EditorView.theme(
  {
    "&": { backgroundColor: "var(--bg-hud)", color: "#abb2bf" },
    ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.7" },
    ".cm-content": { caretColor: "var(--you)", fontSize: "13px" },
    ".cm-gutters": {
      backgroundColor: "var(--bg-hud)",
      color: "#3a4453",
      border: "none",
    },
    ".cm-cursor, .cm-dropCursor": {
      borderLeftColor: "var(--you)",
      borderLeftWidth: "2px",
    },
    "&.cm-focused": { outline: "none" },
    ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "transparent" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": {
      backgroundColor: "rgba(90, 162, 255, 0.15)",
    },
  },
  { dark: true },
);

export interface SubmitNote {
  text: string;
  warn: boolean;
}

export function Editor({
  lang,
  onLang,
  code,
  onCode,
  submitting,
  progress,
  note,
  onSubmit,
  myAttempts,
}: {
  lang: Lang;
  onLang: (lang: Lang) => void;
  code: string;
  onCode: (code: string) => void;
  submitting: boolean;
  progress: Progress;
  note: SubmitNote;
  onSubmit: () => void;
  myAttempts: Attempt[];
}) {
  const extensions = useMemo(
    () => [lang === "cpp" ? cpp() : python(), chrome],
    [lang],
  );

  return (
    <div className={styles.editor}>
      <div className={styles.bar}>
        <div className={styles.langToggle}>
          <button
            className={lang === "python" ? styles.active : ""}
            onClick={() => onLang("python")}
            disabled={submitting}
          >
            python
          </button>
          <button
            className={lang === "cpp" ? styles.active : ""}
            onClick={() => onLang("cpp")}
            disabled={submitting}
          >
            cpp
          </button>
        </div>
        <span className={styles.file}>
          {lang === "cpp" ? "solution.cpp" : "solution.py"}
        </span>
      </div>

      <CodeMirror
        value={code}
        height="300px"
        theme={oneDark}
        extensions={extensions}
        onChange={onCode}
        basicSetup={{
          foldGutter: false,
          highlightActiveLine: false,
          highlightActiveLineGutter: false,
        }}
      />

      {submitting && <JudgingStrip progress={progress} />}

      <div className={styles.actions}>
        <button
          className={styles.submit}
          onClick={onSubmit}
          disabled={submitting}
        >
          {submitting ? "Judging…" : "Submit solution"}
        </button>
        <span className={`${styles.note} ${note.warn ? styles.noteWarn : ""}`}>
          {note.text}
        </span>
      </div>

      <div className={styles.attempts}>
        <div className={styles.attemptsLabel}>YOUR ATTEMPTS</div>
        <AttemptList
          attempts={myAttempts}
          emptyText="No attempts yet — make the first one count."
        />
      </div>
    </div>
  );
}
