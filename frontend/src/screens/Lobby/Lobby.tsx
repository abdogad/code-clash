import { useEffect, useState } from "react";
import { useApp, useDispatch } from "../../state/DuelContext";
import { Logo } from "../../components/Logo";
import { Wordmark } from "../../components/Wordmark";
import { Avatar } from "../../components/Avatar";
import {
  api,
  auth,
  type LeaderboardRow,
  type MatchHistoryRow,
} from "../../hooks/api";
import { initial, tierFor, timeAgo } from "../../lib/format";
import styles from "./Lobby.module.css";

const MEDAL: Record<number, string> = {
  1: "var(--gold)",
  2: "#c7ccd6",
  3: "#c98a3c",
};

export function Lobby() {
  const { me } = useApp();
  const dispatch = useDispatch();
  const [board, setBoard] = useState<LeaderboardRow[]>([]);
  const [history, setHistory] = useState<MatchHistoryRow[]>([]);

  useEffect(() => {
    if (!me) return;
    let live = true;
    const load = async () => {
      if (document.hidden) return; // don't poll a backgrounded tab
      const [lb, hist] = await Promise.all([
        api<LeaderboardRow[]>("/leaderboard?limit=100"),
        api<MatchHistoryRow[]>(`/users/${me.id}/matches`),
      ]);
      if (!live) return;
      if (lb.ok && lb.data) setBoard(lb.data);
      if (hist.ok && hist.data) setHistory(hist.data);
    };
    load();
    // Leaderboard/history barely move, so poll gently and only while the tab is
    // visible; refetch on refocus to cover the paused window.
    const id = setInterval(load, 15000);
    const onVisible = () => {
      if (!document.hidden) load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      live = false;
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [me]);

  if (!me) return null;

  const myRank = board.find((r) => r.username === me.username)?.rank ?? "—";
  // Highlight "you" in place if you're in the top; otherwise pin a separate
  // "you" row below.
  const top = board.slice(0, 10);
  const meInTop = top.some((r) => r.username === me.username);

  const finished = history.filter((m) => m.state === "FINISHED");
  let wins = 0;
  let losses = 0;
  for (const m of finished) {
    if (m.outcome === "DRAW") continue;
    if (m.winner_id === me.id) wins++;
    else losses++;
  }
  const played = wins + losses;
  const winRate = played ? `${Math.round((wins / played) * 100)}%` : "—";

  const logout = () => {
    void api("/auth/logout", {}); // revoke the refresh token + clear cookies server-side
    auth.clear();
    dispatch({ type: "LOGOUT" });
  };
  const findOpponent = async () => {
    // Await the join before showing the queue screen: otherwise its first
    // /queue/status can race ahead, read "idle", and bounce back to the lobby.
    const res = await api("/queue/join", {});
    if (!res.ok) return; // 401 -> global handler signs out; other errors: stay put
    dispatch({ type: "SET_SCREEN", screen: "queue" });
  };

  return (
    <div className={styles.shell}>
      <div className={styles.topbar}>
        <div className={styles.brand}>
          <Logo />
          <Wordmark />
        </div>
        <div className={styles.chip}>
          <div className={styles.chipMeta}>
            <div className={styles.chipName}>{me.username}</div>
            <div className={styles.chipRating}>
              {me.rating} · #{myRank}
            </div>
          </div>
          <Avatar side="you" size="chip" letter={initial(me.username)} />
          <button className={styles.logout} onClick={logout} title="log out">
            log out
          </button>
        </div>
      </div>

      <div className={styles.hero}>
        <span className={`${styles.glow} ${styles.glowRed}`} />
        <span className={`${styles.glow} ${styles.glowBlue}`} />
        <div className={styles.heroBody}>
          <span className={styles.ranked}>RANKED · 1V1</span>
          <h1 className={styles.heroTitle}>Step into the arena.</h1>
          <p className={styles.heroLead}>
            Get matched with a coder near your rating on a problem neither of you
            has seen. The first accepted solution takes the points.
          </p>
          <div className={styles.heroActions}>
            <button className={styles.primary} onClick={findOpponent}>
              <span className={styles.key}>▸</span> Find opponent
            </button>
          </div>
        </div>

        <div className={styles.standing}>
          <div className={styles.standingLabel}>YOUR STANDING</div>
          <div className={styles.standingId}>
            <Avatar side="you" size="standing" letter={initial(me.username)} />
            <div>
              <div className={styles.standingName}>{me.username}</div>
              <div className={styles.standingTier}>
                Rank #{myRank} · {tierFor(me.rating)}
              </div>
            </div>
          </div>
          <div className={styles.standingRating}>
            <b>{me.rating}</b>
            <span>rating</span>
          </div>
          <div className={styles.standingStats}>
            <div className={styles.stat}>
              <div className={styles.statNum}>
                {wins}–{losses}
              </div>
              <div className={styles.statLabel}>RECORD</div>
            </div>
            <div className={styles.stat}>
              <div className={`${styles.statNum} ${styles.statGood}`}>
                {winRate}
              </div>
              <div className={styles.statLabel}>WIN RATE</div>
            </div>
          </div>
        </div>
      </div>

      <div className={styles.grid}>
        <div className={styles.panel}>
          <div className={styles.panelHead}>
            <h2 className={styles.panelTitle}>Leaderboard</h2>
            <span className={styles.panelNote}>live · zset</span>
          </div>
          <div className={`${styles.lbRow} ${styles.lbHead}`}>
            <div>#</div>
            <div>PLAYER</div>
            <div>RATING</div>
          </div>
          {top.map((r) => {
            const isMe = r.username === me.username;
            return (
              <div
                key={r.username}
                className={`${styles.lbRow} ${styles.lbItem} ${isMe ? styles.lbYou : ""}`}
              >
                <div
                  className={styles.lbRank}
                  style={{ color: MEDAL[r.rank] ?? (isMe ? undefined : "var(--muted-2)") }}
                >
                  {r.rank}
                </div>
                <div className={styles.lbPlayer}>
                  <Avatar
                    side={isMe ? "you" : "neutral"}
                    size="row"
                    letter={initial(r.username)}
                  />
                  <span className={styles.lbName}>
                    {r.username}
                    {isMe && <span className={styles.tagYou}> you</span>}
                  </span>
                </div>
                <div className={styles.lbRating}>{r.rating}</div>
              </div>
            );
          })}
          {!meInTop && (
            <>
              <div className={styles.lbDivider} />
              <div className={`${styles.lbRow} ${styles.lbYou}`}>
                <div className={styles.lbRank}>{myRank}</div>
                <div className={styles.lbPlayer}>
                  <Avatar side="you" size="row" letter={initial(me.username)} />
                  <span className={styles.lbName}>
                    {me.username} <span className={styles.tagYou}>you</span>
                  </span>
                </div>
                <div className={styles.lbRating}>{me.rating}</div>
              </div>
            </>
          )}
        </div>

        <div className={styles.panel}>
          <div className={styles.panelHead}>
            <h2 className={styles.panelTitle}>Recent duels</h2>
          </div>
          {history.length === 0 ? (
            <div className={styles.emptyLine}>
              No duels yet — your history shows up here.
            </div>
          ) : (
            history.slice(0, 5).map((m) => <RecentRow key={m.id} m={m} meId={me.id} />)
          )}
        </div>
      </div>
    </div>
  );
}

function RecentRow({ m, meId }: { m: MatchHistoryRow; meId: number }) {
  const o =
    m.state !== "FINISHED"
      ? { code: "·", badge: styles.badgeD }
      : m.outcome === "DRAW"
        ? { code: "D", badge: styles.badgeD }
        : m.winner_id === meId
          ? { code: "W", badge: styles.badgeW }
          : { code: "L", badge: styles.badgeL };

  const d = m.rating_delta;
  const deltaClass =
    d == null || d === 0
      ? styles.deltaFlat
      : d > 0
        ? styles.deltaUp
        : styles.deltaDown;
  const delta = d == null ? "" : d > 0 ? `+${d}` : `${d}`;

  return (
    <div className={styles.recentItem}>
      <div className={`${styles.recentBadge} ${o.badge}`}>{o.code}</div>
      <div className={styles.recentMain}>
        <div className={styles.recentOpp}>vs {m.opponent_name || "unknown"}</div>
        <div className={styles.recentAgo}>
          {timeAgo(m.finished_at)} · {m.title}
        </div>
      </div>
      <div className={`${styles.recentDelta} ${deltaClass}`}>{delta}</div>
    </div>
  );
}
