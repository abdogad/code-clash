# CodeClash — architecture

Three views of the system, from broad to narrow: the containers and how they
talk, what happens to a single submission, and the duel state machine.

## 1. System overview — who talks to whom

The whole trick of the architecture is in the two compose networks: the app
talks to Postgres and Redis, the judge workers talk **only to Redis** on an
internal network with no internet. Contestant code runs one layer deeper, in a
bubblewrap sandbox that can't even see Redis. The testcases volume is mounted
only into the workers, never into the sandbox.

```mermaid
flowchart LR
  subgraph client["Browser"]
    SPA["React + TS SPA<br/>(CodeMirror 6 editor)"]
  end

  subgraph appnet["compose network: default"]
    APP["<b>FastAPI app</b><br/>REST + WebSockets<br/>DuelManager (event log, timers)<br/>matchmaker loop<br/>verdict consumer"]
    PG[("PostgreSQL<br/>users · problems · matches<br/>submissions · duel_events<br/>rating_history · seen_problems")]
  end

  subgraph judgenet["compose network: judge_net (internal — no internet)"]
    REDIS[("Redis<br/>streams: submissions, verdicts<br/>zsets: mm:queue, leaderboard")]
    W1["judge worker ×2<br/>(pids_limit 128)"]
    SB["bubblewrap sandbox<br/>--unshare-all · no net · ro fs<br/>rlimits: cpu, mem, fsize, nproc"]
  end

  TC[/"testcases volume (ro)<br/>mounted in workers only —<br/>never inside the sandbox"/]

  SPA -- "REST /api (cookie auth)" --> APP
  SPA <-. "WS /ws/duel/{id}?last_seq (cookie auth)" .-> APP
  APP -- "SQL (source of truth)" --> PG
  APP <--> REDIS
  REDIS <--> W1
  W1 -- "spawns per test" --> SB
  TC --> W1
```

Key point: Redis sits on **both** networks and is the only bridge between the
app and the workers. A worker never touches Postgres; the app never touches a
sandbox. If a worker is compromised by contestant code (it shouldn't be — the
sandbox is the real wall), the blast radius is the Redis queue, not the
database.

## 2. Life of a submission — from click to Elo

Idempotency is carried by two database constraints, not by careful code:
a partial unique index allows only one PENDING submission per player per match
(the 409), and the verdict write is `UPDATE … WHERE verdict='PENDING'`, so a
redelivered or duplicated verdict is a no-op. Streams + consumer groups +
XAUTOCLAIM give at-least-once delivery; the idempotent write upgrades that to
effectively-once.

```mermaid
sequenceDiagram
  autonumber
  actor P as Player (SPA)
  participant A as FastAPI app
  participant DB as PostgreSQL
  participant R as Redis Streams
  participant W as Judge worker
  participant S as bwrap sandbox
  actor O as Opponent (SPA)

  P->>A: POST /api/submissions {match_id, code}
  A->>DB: INSERT submission (verdict=PENDING)
  Note over A,DB: partial unique index ⇒ second in-flight<br/>submission fails → 409 to the player
  A->>R: XADD submissions
  A->>DB: INSERT duel_event "submission" (seq=n)
  A-->>P: event via WS (disable submit button)
  A-->>O: event via WS ("opponent judging…")

  W->>R: XREADGROUP (or XAUTOCLAIM after 60s<br/>if another worker died mid-job)
  W->>W: py_compile → CE? stop early
  loop tier 1 (samples+killers), then tier 2 — stop at first fail, 10s budget
    W->>S: run code vs testcase (rlimits, no net, ro fs)
    S-->>W: stdout / exit code / signal
  end
  W->>R: XADD verdicts, then XACK

  A->>R: XREADGROUP verdicts
  A->>DB: UPDATE submission SET verdict WHERE verdict='PENDING'
  Note over A,DB: no row updated ⇒ duplicate delivery, drop it
  A->>DB: INSERT duel_event "verdict" (seq=n+1)
  A-->>P: verdict via WS
  A-->>O: verdict via WS (live opponent panel)

  opt verdict = AC
    A->>DB: try_finalize: earliest submitted AC wins,<br/>held while opponent has an earlier PENDING
    A->>DB: Elo for both players in one transaction
    A->>R: update leaderboard zset (post-commit)
    A-->>P: "finished" event (Victory/Defeat + ratings)
    A-->>O: "finished" event
  end
```

## 3. Duel lifecycle — states, timers, recovery

Every state-changing fact about a duel is appended to `duel_events` with a
per-duel sequence number **before** it is broadcast. That single decision buys
three features at once: clients reconnect with `last_seq` and replay what they
missed (page reload), the app rebuilds every active duel from the log on
restart (crash recovery), and clients dedupe by seq so redelivery is harmless.

```mermaid
stateDiagram-v2
  direction LR
  [*] --> Queued: POST /queue/join<br/>(Redis zset by rating)
  Queued --> Active: matchmaker pairs adjacent players<br/>window = 50 + 25·(waited/10s)<br/>picks a problem neither has seen

  state Active {
    direction LR
    [*] --> BothConnected
    BothConnected --> GraceRunning: a player disconnects
    GraceRunning --> BothConnected: reconnects in time<br/>(WS replay from last_seq)
    GraceRunning --> [*]: grace expires (60s)
  }

  Active --> Finished_WIN: first submitted AC<br/>(held while opponent has an<br/>earlier PENDING submission)
  Active --> Finished_FORFEIT: disconnect grace expired
  Active --> Finished_DRAW: duel cap hit (30 min)<br/>and no one is winning
  Active --> Finished_VOID: admin/void path —<br/>ratings never touched

  Finished_WIN --> [*]: Elo ±K in one transaction<br/>leaderboard zset updated
  Finished_FORFEIT --> [*]: Elo applied
  Finished_DRAW --> [*]: Elo applied (draw score)
  Finished_VOID --> [*]

  note right of Active
    Every transition is an event in duel_events
    (per-duel seq). App restart ⇒ load_active()
    replays the log and re-arms timers.
    Client reload ⇒ WS reconnect with last_seq.
  end note
```

## How the pieces map to code

| Piece | Where |
|---|---|
| REST + WS routes | `app/codeclash/{auth,match,matchmaking,rating,problems}/router.py` |
| Duel state machine, event log, timers | `app/codeclash/match/duel.py` (`DuelManager`) |
| Verdict consumer (stream → DB → duel) | `app/codeclash/match/verdicts.py` |
| Matchmaker loop | `app/codeclash/matchmaking/service.py` |
| Elo + leaderboard | `app/codeclash/rating/{elo,service,leaderboard}.py` |
| Worker loop (claim → judge → ack) | `judge/worker.py` |
| Tiered judging, verdict mapping | `judge/judging.py` |
| Sandbox construction | `judge/sandbox.py` |
| Schema + constraints | `db/migrations/*.sql` (applied by `app/codeclash/migrate.py`) |
| SPA | `frontend/src/` (React + TS + Vite, built into `app/static`) |
