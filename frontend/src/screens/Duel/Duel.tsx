import { useEffect, useState } from "react";
import { useApp, useDispatch } from "../../state/DuelContext";
import { useDuelSocket } from "../../hooks/useDuelSocket";
import { api, type SubmissionResponse } from "../../hooks/api";
import type { Lang, Player } from "../../state/types";
import { DuelHud } from "./DuelHud";
import { ProblemPanel } from "./ProblemPanel";
import { Editor, type SubmitNote } from "./Editor";
import { OpponentPanel } from "./OpponentPanel";
import { ConfirmModal } from "../../components/ConfirmModal";
import styles from "./Duel.module.css";

const STARTERS: Record<Lang, string> = {
  python: "# read from stdin with input(), print() the answer\n",
  cpp:
    "#include <bits/stdc++.h>\nusing namespace std;\n\n" +
    "int main() {\n    // read from stdin, print the answer\n    return 0;\n}\n",
};

const DEFAULT_NOTE: SubmitNote = {
  text: "one submission in flight · 409 if you double-fire",
  warn: false,
};

const isStarter = (code: string) =>
  code.trim() === "" ||
  Object.values(STARTERS).some((s) => s.trim() === code.trim());

export function Duel() {
  const state = useApp();
  const dispatch = useDispatch();
  const duel = state.duel;

  const [code, setCode] = useState(() =>
    duel ? STARTERS[duel.lang] : STARTERS.python,
  );
  const [note, setNote] = useState<SubmitNote>(DEFAULT_NOTE);
  const [elapsed, setElapsed] = useState(0);
  const [confirmForfeit, setConfirmForfeit] = useState(false);
  const [forfeiting, setForfeiting] = useState(false);

  // The websocket is the source of truth; this just keeps the displayed time
  // smooth between events. Start + cap come from the server.
  const startedAt = duel?.startedAt;
  useEffect(() => {
    if (!startedAt) return;
    const tick = () =>
      setElapsed(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);

  // Live duel connection (parses events, dispatches into the reducer).
  useDuelSocket(duel?.matchId ?? null, dispatch);

  if (!duel) return null;

  const me = state.me;
  const you: Player =
    (me && duel.players[String(me.id)]) ?? {
      username: me?.username ?? "you",
      rating: state.youRating,
    };
  const opp: Player = duel.players[String(duel.oppId)] ?? {
    username: "opponent",
    rating: 0,
  };

  function changeLang(next: Lang) {
    if (duel!.submitting) return;
    dispatch({ type: "SET_LANG", lang: next });
    setCode((cur) => (isStarter(cur) ? STARTERS[next] : cur));
  }

  async function submit() {
    if (duel!.submitting) return;
    dispatch({ type: "SET_SUBMITTING", submitting: true }); // optimistic
    setNote(DEFAULT_NOTE);
    const res = await api<SubmissionResponse>("/submissions", {
      match_id: duel!.matchId,
      code,
      language: duel!.lang,
    });
    if (res.status === 409) {
      // single-in-flight rule: keep the button "Judging…"; the in-flight
      // submission's verdict event will clear it.
      setNote({ text: "previous submission is still being judged", warn: true });
    } else if (!res.ok) {
      dispatch({ type: "SET_SUBMITTING", submitting: false });
      const detail = (res.data as { detail?: string } | null)?.detail;
      setNote({ text: detail ?? "submit failed", warn: true });
    }
  }

  async function forfeit() {
    // Concede now: the server finishes the match immediately (opponent wins)
    // and the `finished` event over the socket drives the result screen — no
    // waiting on the disconnect grace timer.
    setForfeiting(true);
    let ok = false;
    try {
      ok = (await api(`/matches/${duel!.matchId}/forfeit`, {})).ok;
    } catch {
      ok = false; // network error (e.g. no-backend demo)
    }
    if (!ok) {
      // No live duel to forfeit (demo/no backend, or already finished): just
      // bail to the lobby so the player isn't stuck behind the modal.
      setForfeiting(false);
      setConfirmForfeit(false);
      dispatch({ type: "LEAVE_DUEL" });
      dispatch({ type: "SET_SCREEN", screen: "lobby" });
    }
  }

  return (
    <div>
      <DuelHud
        you={you}
        opp={opp}
        elapsed={elapsed}
        onForfeit={() => setConfirmForfeit(true)}
        youJudging={duel.submitting}
        youProgress={duel.myProgress}
        oppJudging={duel.oppJudging}
        oppProgress={duel.oppProgress}
        oppConnected={duel.oppConnected}
      />
      <div className={styles.grid}>
        {duel.problem && <ProblemPanel problem={duel.problem} />}
        <div className={styles.matchCol}>
          <Editor
            lang={duel.lang}
            onLang={changeLang}
            code={code}
            onCode={setCode}
            submitting={duel.submitting}
            progress={duel.myProgress}
            note={note}
            onSubmit={submit}
            myAttempts={duel.myAttempts}
          />
          <OpponentPanel
            opp={opp}
            connected={duel.oppConnected}
            status={duel.oppStatus}
            judging={duel.oppJudging}
            progress={duel.oppProgress}
            attempts={duel.oppAttempts}
          />
        </div>
      </div>

      {confirmForfeit && (
        <ConfirmModal
          title="Forfeit this duel?"
          message="The match ends immediately and counts as a loss — your opponent takes the points. This can't be undone."
          confirmLabel="Forfeit"
          cancelLabel="Keep playing"
          danger
          busy={forfeiting}
          onConfirm={forfeit}
          onCancel={() => setConfirmForfeit(false)}
        />
      )}
    </div>
  );
}
