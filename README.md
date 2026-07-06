# CodeClash

**CodeClash** is a 1v1 competitive-programming arena: you're matched against an
opponent near your rating on a problem neither of you has seen, and the first
correct submission takes the points. It combines a real-time duel experience —
live opponent status, instant verdicts over WebSockets, Elo ratings — with a
hardened judge that runs untrusted code in [tallyrun](https://github.com/abdogad/tallyrun),
a rootless bubblewrap sandbox with load-invariant, instruction-counted timing.

- 🌐 **Live demo:** <https://167-233-112-171.sslip.io>
- 🎬 **Video walkthrough:** <https://www.youtube.com/watch?v=ul7qFvcvog4>

## Architecture (MVP)

Diagrams (system map, submission lifecycle, duel state machine) are in
[`docs/architecture.md`](docs/architecture.md) — GitHub renders the inline
Mermaid.

A modular monolith (FastAPI) plus separate judge workers:

```
app/codeclash/
  auth/         registration/login (JWT)
  problems/     statements, metadata
  matchmaking/  queue, Elo-window pairing, unseen-problem pick
  match/        duel state machine, duel_events log, WebSockets, submissions
  rating/       Elo transaction, leaderboard
judge/          sandboxed judging workers (Redis-only by design)
db/migrations/  forward-only schema migrations (run by the app on startup)
db/seed/        problem catalog + dev users (emptiness-guarded first-boot seed)
testcases/      <slug>/NN.in, NN.out + manifest.json (tier1/tier2 split)
```

Pipeline: `POST /api/submissions` → Postgres row + `XADD stream:submissions` →
judge worker (`XREADGROUP`, `XAUTOCLAIM` for crash recovery) → `XADD
stream:verdicts` → app consumer applies the verdict idempotently (keyed by
submission id, only PENDING rows update).

## Run locally

Prerequisites: Docker and Docker Compose.

```bash
docker compose up -d --build      # postgres, redis, app, judge workers
curl localhost:8000/healthz       # -> {"status":"ok"} once startup finishes
```

Then open <http://localhost:8000>. Dev logins are seeded: `alice` / `bob`,
password `secret123`. Interactive API docs (Swagger): <http://localhost:8000/docs>.

Run the tests (hermetic — no live stack needed):

```bash
cd tests
python3 test_import.py              # ICPC/Kattis package importer
python3 test_checkers.py           # checker engine (diff/float/testlib)
python3 test_checker_cache.py      # compiled-checker cache keying
python3 test_problem_rating.py     # per-problem Elo rating drift
```

### Load-invariant judging (instruction counting via tallyrun)

Every submission runs through [**tallyrun**](https://github.com/abdogad/tallyrun),
a rootless bubblewrap + cgroup-v2 sandbox that measures work in **retired CPU
instructions** (a `perf_event_open` hardware counter across the process tree)
rather than wall or CPU seconds. TLE is decided on that load-invariant "virtual
time" (`instructions / INSN_PER_MS`), so the same solution gets the same verdict
on an idle box and a busy one; MLE is decided on measured peak RSS from a per-run
cgroup. Instruction counting needs a PMU and unprivileged `perf` access
(`kernel.perf_event_paranoid ≤ 2`); the production overlay
(`docker-compose.prod.yml`) enables it with `cgroup: host` + `/sys/fs/cgroup:rw`
and `seccomp=unconfined` (which also unblocks `perf_event_open`), and
`deploy/bootstrap-server.sh` sets the host sysctl — no `--privileged`, no caps.

The plain `docker compose up` dev stack has no PMU / cgroup delegation, so
tallyrun reports `measurement:"degraded"` and the judge falls back to measured
CPU time, like a classic judge (`judge/judging.py`) — still runs, just
load-dependent.

**Languages:** `python` and `cpp` (g++ `-O2 -std=gnu++17`). Each problem has one
time/memory limit; per-language multipliers in `judge/judging.py` (`LANGUAGES`)
keep it fair across the slower interpreter (Python ×3 time, ×2 memory).

## Duel protocol

Auth: `POST /api/auth/register {username, email, password}` or
`POST /api/auth/login {username, password}` → sets httpOnly `access`/`refresh`
cookies and returns `{user}`. REST and the duel WebSocket both authenticate from
the access cookie (same-origin, so the browser sends it automatically);
`POST /api/auth/refresh` rotates it, `POST /api/auth/logout` revokes it.

1. `POST /api/queue/join` then poll `GET /api/queue/status`
   until `{status: "matched", match_id}` (also how a client rediscovers an
   active duel after a reload or an app restart).
2. `GET /api/matches/{id}` — problem statement, players, state.
3. `WS /ws/duel/{id}?last_seq=N` — authenticated by the access cookie (closes
   with code 4401 if missing/invalid). Server replays events with
   `seq > N`, then streams live. Clients must dedupe by `seq`. Event types:
   `started`, `player_connected`, `player_disconnected`, `submission`,
   `verdict` (opponent progress — never code), `finished` (outcome,
   winner, Elo changes). Connecting to a finished duel replays the log and
   closes.
4. `POST /api/submissions {match_id, code, language}` — `language` is
   `python` (default) or `cpp`; 409 while the previous submission is pending
   (one in-flight per player). Practice mode: pass `problem_id` instead of
   `match_id`.

Lifecycle: disconnect → 60 s grace (`DUEL_GRACE_SECONDS`) → forfeit; 30 min
cap (`DUEL_CAP_SECONDS`) with no AC → draw; winner = earliest *submitted*
accepted solution (fair-win rule).

## Status

Built and deployed:

- **Judging** — untrusted code runs in the [tallyrun](https://github.com/abdogad/tallyrun)
  sandbox (bubblewrap net/pid/user namespaces + seccomp + per-run cgroup),
  tiered early-exit schedule, real verdicts (AC/WA/CE/TLE/MLE/RE, verified
  adversarially). Load-invariant TLE from retired-instruction counting, peak-RSS
  MLE; Python and C++ submissions.
- **Matchmaking & duels** — Elo-window pairing with unseen-problem pick, duel
  state machine + durable `duel_events` log, WebSockets with `last_seq` replay,
  fair-win rule, admission control (409 on double submit), Elo applied on finish.
- **Auth** — JWT (bcrypt; REST bearer + WS cookie), Redis leaderboard zset
  (rebuilt on startup, updated post-commit), rating-history endpoint.
- **Frontend** — React + TypeScript + Vite SPA (CodeMirror 6 editor, KaTeX
  statements, live opponent panel, result overlay), built into `app/static`
  and served by FastAPI; WS auto-reconnect with `last_seq` replay.
- **Deploy** — Hetzner + nginx + TLS (see `deploy/`).
- **Load test** — `tests/loadtest.py`, results in `docs/loadtest.md`:
  worst-case mix 1.5 sub/s at p95 4.3 s with 2 workers; 12 sub/s easy mix;
  1,000 sockets at 83 MB. Found and fixed two
  load bugs: bwrap zombie leak → pids exhaustion → false CEs (`tini` as PID 1 +
  sandbox-failure detection), and bcrypt blocking the event loop (threadpool).
