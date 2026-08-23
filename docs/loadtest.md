# Load test results

Method: `tests/loadtest.py` (asyncio + aiohttp) against the dev compose stack on
a 16-core Fedora workstation, rootless podman, 1 app process, 2 judge workers.
Numbers are client-observed; verdict latency includes a 250 ms polling quantum,
so server-side latency is lower. Sweeps assert verdict correctness, not only
latency. Run phases with:

```
python3 tests/loadtest.py http | auth | judge [rates] | tle [rates] | ws [n_duels]
```

## Results

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
- `/healthz` during the storm: p50 1.1 ms, p95 1.5 ms

### Judging pipeline (2 workers, mixed 60% WA / 40% AC, trivial problems)

| offered rate | p50 | p95 | backlog growth |
|---|---|---|---|
| 2/s | 0.26 s | 0.27 s | none |
| 8/s | 0.26 s | 0.27 s | none |
| 10/s | 0.26 s | 0.27 s | none |
| 12/s | 0.26 s | 0.52 s | none |
| 14/s | 2.3 s | 4.1 s | growing |
| 20/s | 8.7 s | 16.5 s | growing fast |

Knee ≈ 12–13 sub/s sustained with 2 workers (≈ 6.5/s per worker; S ≈ 155 ms per
easy submission incl. compile + bwrap startup). Verdicts stayed 100% correct at
every rate including past saturation; overload degrades latency only, since
idempotency is enforced by DB constraints.

### Worst case (50% TLE / 30% WA / 20% AC — every TLE burns the full 2 s CPU limit)

| offered rate | p50 | p95 | backlog growth |
|---|---|---|---|
| 1.0/s | 2.29 s | 2.8 s | none |
| 1.5/s | 2.29 s | 4.3 s | none |
| 2.0/s | 4.8 s | 7.9 s | growing |

At 1.5 sub/s with this adversarial mix, verdict p95 is 4.3 s (≤ 5 s) on 2 of the
planned 3 judge slots.

### WebSockets + matchmaking (through the real queue)

| | 460 sockets (230 duels) | 1,000 sockets (500 duels) |
|---|---|---|
| queue join (all players) | 0.8 s | 1.7 s |
| matchmaker paired everyone in | 2.7 s | 7.8 s |
| all sockets opened in | 0.3 s | 0.8 s |
| ws connect incl. event replay p95 | 259 ms | 661 ms |
| event broadcast p95 | 14.7 ms | 13.3 ms |
| app memory | — | 83 MB |

Broadcast latency is flat in socket count; 1,000 sockets cost 83 MB on one
process.

## Capacity

- **Reads**: ~700–5,700 req/s on one process. 1M reads/day = 11.6 req/s average,
  under 2% utilization.
- **Submissions**: 2 workers sustain ~1M easy-mix judgments/day (12/s) or
  ~130k/day under the 50%-TLE mix (1.5/s). Capacity = `workers /
  mean-judge-seconds`, linear in worker count; the prod plan (3 dedicated cores)
  covers the target load.
- **Concurrent duelists**: 1,000 sockets ≈ 83 MB on one process, broadcast p95
  13 ms. Scaling past one process needs cross-process event fan-out (the duel
  registry is in-memory per process) — an MVP limitation.
- **Bottleneck**: one uvicorn process saturates one core (~108% CPU during
  churn); Postgres (54 MB) and Redis (6 MB) are idle at these scales.
