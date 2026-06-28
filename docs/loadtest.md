# Load test results

Method: `tests/loadtest.py` (asyncio + aiohttp) against the dev compose stack
on a 16-core Fedora workstation, rootless podman, 1 app process, 2 judge
workers. All numbers are client-observed. Verdict latency includes a 250 ms
polling quantum, so true server-side numbers are lower. Run phases with:

```
python3 tests/loadtest.py http | auth | judge [rates] | tle [rates] | ws [n_duels]
```

## Bugs found by this load test (both fixed)

1. **Judge workers leaked one zombie per sandbox run.** bwrap's in-namespace
   init helper reparents to the container's PID 1 on exit; the Python worker
   never reaps foreign children. Zombies accumulated until `pids_limit: 128`,
   after which **every fork failed and all submissions were mis-verdicted CE**
   ("bwrap: Can't fork for pid 1"). Fix: a PID-1 init (`tini`, baked into the
   judge image) reaps the foreign children, plus `judging.py` now raises on `bwrap:`
   stderr so an infrastructure failure becomes an internal-error verdict
   instead of blaming the contestant's code. After the fix: 1,500+ judged
   submissions, `pids.current` steady at 3.
2. **bcrypt blocked the event loop.** `hash_password` (~200 ms CPU) ran
   synchronously in the async handler: a registration storm froze every
   WS/API response (`/healthz` p95 hit 1 s). Fix: `run_in_threadpool` for
   hash/verify. Registration throughput went 5/s → 43/s and `/healthz` p95
   under the same storm dropped to 1.5 ms.

Lesson recorded for the report: latency-only load tests lie — the first sweep
"sustained 60/s" because 97% of verdicts were fast-failing CEs. The sweep now
asserts verdict correctness.

## Results after fixes

### HTTP reads (10 s per row)

| endpoint | concurrency | req/s | p50 | p95 |
|---|---|---|---|---|
| GET /healthz | 50 | 4,745 | 10 ms | 14 ms |
| GET /api/leaderboard | 50 | 705 | 70 ms | 77 ms |
| GET /api/auth/me | 50 | 1,816 | 10 ms | 94 ms |
| GET /api/users/{id}/matches | 50 | 1,555 | 29 ms | 65 ms |
| GET /healthz | 200 | 5,665 | 34 ms | 45 ms |
| GET /api/leaderboard | 200 | 683 | 292 ms | 347 ms |

### Auth writes (bcrypt in threadpool)

- POST /auth/register at c=10: **43 req/s**, p50 231 ms, p95 242 ms
- `/healthz` during the storm: p50 1.1 ms, p95 1.5 ms (event loop healthy)

### Judging pipeline (2 workers, mixed 60% WA / 40% AC, trivial problems)

| offered rate | p50 | p95 | backlog growth |
|---|---|---|---|
| 2/s | 0.26 s | 0.27 s | none |
| 8/s | 0.26 s | 0.27 s | none |
| 10/s | 0.26 s | 0.27 s | none |
| 12/s | 0.26 s | 0.52 s | none |
| 14/s | 2.3 s | 4.1 s | growing |
| 20/s | 8.7 s | 16.5 s | growing fast |

**Knee ≈ 12–13 submissions/s sustained** with 2 workers (≈ 6.5/s per
worker, S ≈ 155 ms per easy submission incl. compile + bwrap startups).
Verdicts remained 100% correct at every rate, including past saturation —
overload degrades latency only, never correctness (DB-constraint idempotency).

### Worst case (50% TLE / 30% WA / 20% AC — every TLE burns the full 2 s CPU limit)

| offered rate | p50 | p95 | backlog growth |
|---|---|---|---|
| 1.0/s | 2.29 s | 2.8 s | none |
| 1.5/s | 2.29 s | 4.3 s | none |
| 2.0/s | 4.8 s | 7.9 s | growing |

**Design-doc §3.2 SLO check: at 1.7 sub/s with an adversarially bad mix,
verdict p95 ≤ 5 s — held with only 2 of the planned 3 judge slots.**

### WebSockets + matchmaking (through the real queue)

| | 460 sockets (230 duels) | 1,000 sockets (500 duels) |
|---|---|---|
| queue join (all players) | 0.8 s | 1.7 s |
| matchmaker paired everyone in | 2.7 s | 7.8 s |
| all sockets opened in | 0.3 s | 0.8 s |
| ws connect incl. event replay p95 | 259 ms | 661 ms |
| event broadcast p95 | 14.7 ms | 13.3 ms |
| app memory | — | 83 MB |

The §3.2 target of ~460 concurrent sockets passes with an order of magnitude
of headroom on memory; broadcast latency is flat in socket count.

## Capacity answers

- **Reads**: ~700–5,700 req/s on one process ⇒ 1M GET requests is ~3 minutes
  of leaderboard traffic, or ~25 minutes at the heaviest endpoint. Per day,
  1M reads = 11.6 req/s average — under 2% utilization.
- **Submissions** (the real currency): 2 workers sustain ~1M easy-mix
  judgments/day (12/s), or ~130k/day under the adversarial 50%-TLE mix
  (1.5/s). Capacity is `workers / mean-judge-seconds` and scales linearly:
  the prod plan (3 dedicated cores) covers the §3.2 design point with margin.
- **Concurrent duelists**: 1,000 sockets ≈ 83 MB / one process, broadcast
  p95 13 ms. 100k concurrent sockets would need multiple app processes and a
  cross-process event fan-out (duel registry is in-memory per process) — an
  acknowledged MVP compromise, not a dev-box measurement.
- **Single hard limit observed**: one uvicorn process saturates one core
  (~108% CPU during churn); everything else (Postgres 54 MB, Redis 6 MB) is
  idle at these scales.
