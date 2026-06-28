"""Duel state machine, durable event log, and WebSocket fan-out.

The log (duel_events) is the truth; in-memory Duel objects are a cache rebuilt
from Postgres on startup (load_active), so a mid-duel app restart survives. Each
state change appends an event with a per-duel monotonic seq, assigned under an
asyncio lock (one process owns the duel, so that's a total order). Clients
reconnect with their last seen seq and get the gap replayed.

Lifecycle:
- disconnect → grace timer → forfeit (S=0)
- duel cap reached, nobody accepted → draw (S=0.5)
- fair win: earliest *submitted* AC wins; held while the opponent has an
  earlier-timestamped submission still pending.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from ..rating.leaderboard import apply_results
from ..rating.problems import update_problem_rating
from ..rating.service import finalize_match

log = logging.getLogger(__name__)

# Env-overridable so the grace/cap paths can be exercised quickly.
GRACE_SECONDS = int(os.environ.get("DUEL_GRACE_SECONDS", "60"))
CAP_SECONDS = int(os.environ.get("DUEL_CAP_SECONDS", "1800"))


@dataclass
class Duel:
    id: UUID
    player_a: int
    player_b: int
    problem_id: int
    seq: int = 0
    finished: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sockets: dict = field(default_factory=dict)  # user_id -> WebSocket
    grace: dict = field(default_factory=dict)  # user_id -> Task
    cap_task: asyncio.Task | None = None

    @property
    def players(self) -> tuple[int, int]:
        return (self.player_a, self.player_b)

    def opponent(self, user_id: int) -> int:
        return self.player_b if user_id == self.player_a else self.player_a


class DuelManager:
    def __init__(self, pool: asyncpg.Pool, redis):
        self.pool = pool
        self.redis = redis
        self.duels: dict[UUID, Duel] = {}

    # -- lifecycle ---------------------------------------------------------

    async def load_active(self) -> None:
        """Crash recovery: rebuild ACTIVE duels from Postgres. Both players
        are treated as disconnected until their sockets return."""
        rows = await self.pool.fetch(
            """SELECT id, player_a, player_b, problem_id, started_at
               FROM matches WHERE state = 'ACTIVE'"""
        )
        for row in rows:
            duel = await self._register(row)
            for uid in duel.players:
                self._start_grace(duel, uid)
        if rows:
            log.info("recovered %d active duel(s) from event log", len(rows))

    async def create_duel(self, row) -> Duel:
        return await self._register(row)

    async def _register(self, row) -> Duel:
        duel = Duel(
            id=row["id"],
            player_a=row["player_a"],
            player_b=row["player_b"],
            problem_id=row["problem_id"],
        )
        duel.seq = await self.pool.fetchval(
            "SELECT COALESCE(MAX(seq), 0) FROM duel_events WHERE match_id = $1",
            duel.id,
        )
        elapsed = (
            datetime.now(timezone.utc) - row["started_at"]
        ).total_seconds()
        duel.cap_task = asyncio.create_task(
            self._cap(duel, max(0.0, CAP_SECONDS - elapsed))
        )
        self.duels[duel.id] = duel
        return duel

    # -- event log ---------------------------------------------------------

    async def append_event(self, duel: Duel, type_: str, payload: dict) -> None:
        # seq, the durable write, and the broadcast all happen under the lock.
        # Outside it, two concurrent appends could be *sent* out of seq order and
        # the client — which drops anything <= its last seen seq — would silently
        # discard the lower-seq event until a reconnect. Cheap: a duel has 2 sockets.
        async with duel.lock:
            duel.seq += 1
            seq = duel.seq
            await self.pool.execute(
                """INSERT INTO duel_events (match_id, seq, type, payload)
                   VALUES ($1, $2, $3, $4)""",
                duel.id,
                seq,
                type_,
                json.dumps(payload),
            )
            await self._broadcast(duel, {"seq": seq, "type": type_, "payload": payload})

    # A dead-but-unclosed socket must not stall the duel: bound each send and drop
    # the socket on timeout/error (the client reconnects and replays).
    SEND_TIMEOUT_S = 5

    async def _broadcast(self, duel: Duel, message: dict) -> None:
        for uid, ws in list(duel.sockets.items()):
            try:
                await asyncio.wait_for(ws.send_json(message), self.SEND_TIMEOUT_S)
            except Exception:
                duel.sockets.pop(uid, None)

    async def _replay_since(self, ws, match_id: UUID, last_seq: int) -> None:
        rows = await self.pool.fetch(
            """SELECT seq, type, payload FROM duel_events
               WHERE match_id = $1 AND seq > $2 ORDER BY seq""",
            match_id,
            last_seq,
        )
        for r in rows:
            await ws.send_json(
                {"seq": r["seq"], "type": r["type"], "payload": json.loads(r["payload"])}
            )

    # -- websocket plane ----------------------------------------------------

    def get(self, match_id: UUID) -> "Duel | None":
        """The live (in-memory) duel for a match, or None if not active."""
        return self.duels.get(match_id)

    async def connect(self, match_id: UUID, user_id: int, ws, last_seq: int) -> bool:
        duel = self.duels.get(match_id)
        if duel is None:
            # Finished (or unknown) match: replay the log and close, so a
            # client reloading after the duel ended still sees the outcome.
            return await self._replay_finished(match_id, user_id, ws, last_seq)
        if user_id not in duel.players:
            await ws.close(code=4403)
            return False
        await ws.accept()
        timer = duel.grace.pop(user_id, None)
        if timer:
            timer.cancel()
        duel.sockets[user_id] = ws
        # Replay the gap. A live event may slip in during the fetch and
        # arrive twice; clients must dedupe by seq.
        await self._replay_since(ws, match_id, last_seq)
        await self.append_event(duel, "player_connected", {"user_id": user_id})
        return True

    async def _replay_finished(self, match_id, user_id, ws, last_seq) -> bool:
        exists = await self.pool.fetchval(
            "SELECT 1 FROM matches WHERE id = $1 AND (player_a = $2 OR player_b = $2)",
            match_id,
            user_id,
        )
        if not exists:
            await ws.close(code=4404)
            return False
        await ws.accept()
        await self._replay_since(ws, match_id, last_seq)
        await ws.close(code=1000)
        return False

    async def disconnect(self, match_id: UUID, user_id: int) -> None:
        duel = self.duels.get(match_id)
        if duel is None or duel.finished:
            return
        if duel.sockets.pop(user_id, None) is not None:
            await self.append_event(
                duel, "player_disconnected", {"user_id": user_id}
            )
        self._start_grace(duel, user_id)

    def _start_grace(self, duel: Duel, user_id: int) -> None:
        old = duel.grace.pop(user_id, None)
        if old:
            old.cancel()
        duel.grace[user_id] = asyncio.create_task(self._grace(duel, user_id))

    async def _grace(self, duel: Duel, user_id: int) -> None:
        await asyncio.sleep(GRACE_SECONDS)
        if duel.finished or user_id in duel.sockets:
            return
        await self.finish(
            duel, "FORFEIT", duel.opponent(user_id),
            reason=f"player {user_id} disconnected beyond grace period",
        )

    async def _cap(self, duel: Duel, delay: float) -> None:
        await asyncio.sleep(delay)
        if duel.finished:
            return
        if not await self.try_finalize(duel):
            await self.finish(duel, "DRAW", None, reason="duel time cap reached")

    # -- verdicts and the fair-win rule -------------------------------------

    async def on_verdict(self, sub: dict) -> None:
        duel = self.duels.get(sub["match_id"])
        if duel is None or duel.finished:
            return
        await self.append_event(
            duel,
            "verdict",  # opponent sees progress, never the code
            {
                "user_id": sub["user_id"],
                "verdict": sub["verdict"],
                "tests_passed": sub["tests_passed"],
                "tests_total": sub["tests_total"],
            },
        )
        await self.try_finalize(duel)

    async def on_progress(self, f: dict) -> None:
        """Cosmetic per-test progress: broadcast-only, never written to the event
        log (seq=0, no Postgres row), so the replay/seq contract is untouched. The
        judge only knows submission_id, so resolve the duel + player here."""
        try:
            sub_id = UUID(f["submission_id"])
        except (KeyError, ValueError):
            return
        row = await self.pool.fetchrow(
            "SELECT match_id, user_id FROM submissions WHERE id = $1", sub_id
        )
        if row is None or row["match_id"] is None:
            return  # unknown, or a practice submission (no duel)
        duel = self.duels.get(row["match_id"])
        if duel is None or duel.finished:
            return
        # Under the lock so this frame can't interleave with a real event's on
        # the same socket (seq=0 keeps it out of the client's dedupe).
        async with duel.lock:
            await self._broadcast(
                duel,
                {
                    "seq": 0,
                    "type": "judging_progress",
                    "payload": {
                        "user_id": row["user_id"],
                        "tests_passed": int(f["tests_passed"]),
                        "tests_total": int(f["tests_total"]),
                    },
                },
            )

    async def try_finalize(self, duel: Duel) -> bool:
        """Fair-win rule: winner = earliest submitted AC, held while the opponent
        has an earlier-timestamped submission still pending. Pure DB queries →
        idempotent and order-independent, so it's safe to re-run after every
        verdict and across restarts."""
        first_ac = await self.pool.fetchrow(
            """SELECT user_id, submitted_at FROM submissions
               WHERE match_id = $1 AND verdict = 'AC'
               ORDER BY submitted_at LIMIT 1""",
            duel.id,
        )
        if first_ac is None:
            return False
        held = await self.pool.fetchval(
            """SELECT 1 FROM submissions
               WHERE match_id = $1 AND user_id = $2
                 AND verdict = 'PENDING' AND submitted_at < $3""",
            duel.id,
            duel.opponent(first_ac["user_id"]),
            first_ac["submitted_at"],
        )
        if held:
            return False
        await self.finish(duel, "WIN", first_ac["user_id"], reason="accepted")
        return True

    async def finish(
        self, duel: Duel, outcome: str, winner_id: int | None, reason: str = ""
    ) -> None:
        # Match state + Elo and the terminal "finished" event commit in ONE
        # transaction (under the lock, so seq stays consistent with append_event).
        # Two separate commits risked a crash in between: match FINISHED in
        # Postgres but no terminal event, so a reconnecting client replayed
        # everything except its outcome and never saw the result screen.
        payload = {
            "outcome": outcome,
            "winner_id": winner_id,
            "reason": reason,
        }
        async with duel.lock:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    results = await finalize_match(conn, duel.id, outcome, winner_id)
                    if results is None:
                        return  # someone else finalized first
                    # Drift the problem's rating from who solved it, in the same
                    # txn so it commits with the outcome. Only the problem moves;
                    # player Elo is already set by finalize_match.
                    await update_problem_rating(conn, duel.id, outcome)
                    seq = duel.seq + 1
                    payload["ratings"] = results
                    await conn.execute(
                        """INSERT INTO duel_events (match_id, seq, type, payload)
                           VALUES ($1, $2, 'finished', $3)""",
                        duel.id,
                        seq,
                        json.dumps(payload),
                    )
            # Committed: the outcome and its terminal event are durable together.
            duel.seq = seq
            duel.finished = True
            await self._broadcast(
                duel, {"seq": seq, "type": "finished", "payload": payload}
            )
        # Post-commit side effects, all rebuilt from Postgres on restart, so a
        # crash here is harmless. Clearing the stale "you are matched" pointers
        # keeps players from being routed back into a finished duel.
        await self.redis.delete(*(f"mm:matched:{uid}" for uid in duel.players))
        await apply_results(self.redis, results)  # leaderboard zset
        current = asyncio.current_task()
        for t in list(duel.grace.values()) + [duel.cap_task]:
            if t is not None and t is not current:
                t.cancel()
        self.duels.pop(duel.id, None)
