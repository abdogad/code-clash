import { useEffect, useRef, useState } from "react";
import { useApp, useDispatch } from "../../state/DuelContext";
import { Avatar } from "../../components/Avatar";
import { api, type MatchResponse, type QueueStatus } from "../../hooks/api";
import { enterMatch } from "../../hooks/enterMatch";
import { clock, initial } from "../../lib/format";
import styles from "./Matchmaking.module.css";

const REVEAL_MS = 1300; // VS reveal before dropping into the duel
// The real matchmaker widens its rating window without an upper cap; the UI just
// saturates the fill bar at this display ceiling.
const MAX_WINDOW_DISPLAY = 250;

export function Matchmaking() {
  const { me } = useApp();
  const dispatch = useDispatch();
  const startedAt = useRef(Date.now());
  const [waited, setWaited] = useState(0);
  const [found, setFound] = useState<MatchResponse | null>(null);

  // local 1s ticker so the wait/window read smoothly between status polls
  useEffect(() => {
    const id = setInterval(
      () => setWaited(Math.max(0, Math.floor((Date.now() - startedAt.current) / 1000))),
      1000,
    );
    return () => clearInterval(id);
  }, []);

  // poll the queue until matched (the status endpoint is the source of truth)
  useEffect(() => {
    if (!me || found) return;
    let live = true;
    const poll = async () => {
      const res = await api<QueueStatus>("/queue/status");
      if (!live || !res.ok || !res.data) return; // a failed poll != left the queue
      const st = res.data;
      if (st.status === "queued") {
        startedAt.current = Date.now() - st.waited_s * 1000; // reconcile to server
      } else if (st.status === "matched") {
        const m = await api<MatchResponse>(`/matches/${st.match_id}`);
        if (live && m.ok && m.data) setFound(m.data);
      } else {
        dispatch({ type: "SET_SCREEN", screen: "lobby" }); // idle -> left/expired
      }
    };
    poll();
    const id = setInterval(poll, 1500);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [me, found, dispatch]);

  // after the reveal, enter the duel (reusing the already-fetched match)
  useEffect(() => {
    if (!found || !me) return;
    const t = setTimeout(() => {
      void enterMatch(dispatch, me.id, found.id, found);
    }, REVEAL_MS);
    return () => clearTimeout(t);
  }, [found, me, dispatch]);

  if (!me) return null;

  const cancel = async () => {
    await api("/queue/leave", {});
    dispatch({ type: "SET_SCREEN", screen: "lobby" });
  };

  if (found) {
    const oppId = Object.keys(found.players).find((id) => Number(id) !== me.id)!;
    const opp = found.players[oppId];
    return (
      <div className={styles.screen}>
        <div className={styles.found}>
          <div className={styles.foundLabel}>◆ MATCH FOUND</div>
          <div className={styles.pair}>
            <div className={styles.side}>
              <Avatar side="you" size="versus" letter={initial(me.username)} />
              <div className={styles.name}>{me.username}</div>
              <div className={`${styles.rating} ${styles.ratingYou}`}>
                {me.rating}
              </div>
            </div>
            <div className={styles.vs}>VS</div>
            <div className={styles.side}>
              <Avatar side="opp" size="versus" letter={initial(opp.username)} />
              <div className={styles.name}>{opp.username}</div>
              <div className={`${styles.rating} ${styles.ratingOpp}`}>
                {opp.rating}
              </div>
            </div>
          </div>
          <p className={styles.enter}>entering duel…</p>
        </div>
      </div>
    );
  }

  // window mirrors the real matchmaker: ±(BASE 50 + 25 per 10s waited)
  const half = 50 + 25 * Math.floor(waited / 10);
  // the band fills the track as the window widens; full at the display ceiling
  const bandPct = Math.min(100, (half / MAX_WINDOW_DISPLAY) * 100);
  return (
    <div className={styles.screen}>
      <div className={styles.queue}>
        <div className={styles.eyebrow}>
          <span className={styles.scanDot} />
          SCANNING THE LADDER
        </div>

        <div className={styles.radar}>
          <span className={styles.ring} />
          <span className={`${styles.ring} ${styles.ring2}`} />
          <span className={`${styles.ring} ${styles.ring3}`} />
          <Avatar side="you" size="queue" letter={initial(me.username)} />
        </div>

        <h2 className={styles.title}>Finding your opponent</h2>
        <p className={styles.sub}>
          Matching near <b>{me.rating}</b> · the window widens every pass
        </p>

        <div className={styles.meter}>
          <div className={styles.meterRow}>
            <div className={styles.meterTrack}>
              <div className={styles.meterBand} style={{ width: `${bandPct}%` }} />
            </div>
            <div className={styles.meterMark} />
          </div>
          <div className={styles.meterEnds}>
            <span className={styles.meterEdge}>{me.rating - half}</span>
            <span className={styles.meterLabel}>rating window · ±{half}</span>
            <span className={styles.meterEdge}>{me.rating + half}</span>
          </div>
        </div>

        <div className={styles.elapsed}>
          <span className={styles.elapsedNum}>{clock(waited)}</span>
          <span className={styles.elapsedLabel}>elapsed</span>
        </div>

        <button className={styles.cancel} onClick={cancel}>
          Cancel search
        </button>
      </div>
    </div>
  );
}
