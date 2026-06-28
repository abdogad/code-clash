#!/usr/bin/env python3
"""Day-13 load test against a running stack (http://localhost:8000).

Usage: python3 loadtest.py <phase> [args]
  http             read-endpoint throughput sweep
  auth             registration/login throughput (bcrypt path) + event-loop-blocking probe
  judge [rates]    judging pipeline: service time + submission-rate sweep (default 1,2,4,6,8 /s)
  tle [rates]      worst-case sweep: 50% TLE submissions (each burns the full CPU limit)
  ws [n_duels]     concurrent duels over real matchmaking + WS broadcast latency (default 230)

Each phase prints a self-contained report. Results are client-observed.
"""
import asyncio
import json
import os
import secrets
import statistics
import sys
import time

import aiohttp

# Override for a remote/containerised run, e.g. LT_BASE=http://app:8000
BASE = os.environ.get("LT_BASE", "http://localhost:8000")
WA_CODE = "print('zzz')"
SOLUTIONS = {
    "sum-a-b": "a,b=map(int,input().split());print(a+b)",
    "count-vowels": "print(sum(c in 'aeiouAEIOU' for c in input()))",
    "reverse-string": "print(input()[::-1])",
}
# Pull in AC solutions for the full generated catalog if present, so the AC
# path has a correct solution for every problem /api/problems returns.
_sol_file = os.path.join(os.path.dirname(__file__), "solutions.json")
if os.path.exists(_sol_file):
    with open(_sol_file) as _f:
        SOLUTIONS.update(json.load(_f))


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100))]


def report_latencies(name, lats, errors=0, dur=None):
    n = len(lats)
    rps = f"  {n / dur:7.1f} req/s" if dur else ""
    print(
        f"  {name:<34}{rps}  n={n:<6} p50={pct(lats, 50) * 1000:7.1f}ms "
        f"p95={pct(lats, 95) * 1000:8.1f}ms p99={pct(lats, 99) * 1000:8.1f}ms "
        f"max={max(lats) * 1000 if lats else 0:8.1f}ms"
        + (f"  errors={errors}" if errors else "")
    )


# Auth moved to httpOnly cookies. One ClientSession here impersonates many users,
# so we run it with a DummyCookieJar (no auto-store/resend) and thread each user's
# access-token value explicitly as a Cookie header — `_auth(tok)` builds it.
def _auth(token: str) -> dict:
    return {"Cookie": f"access_token={token}"}


async def register(session, prefix="lt"):
    name = f"{prefix}-{secrets.token_hex(4)}"
    async with session.post(
        f"{BASE}/api/auth/register",
        json={"username": name, "email": f"{name}@lt.dev", "password": "secret123"},
    ) as r:
        assert r.status == 201, (r.status, await r.text())
        d = await r.json()
        return r.cookies["access_token"].value, d["user"]


# ---------------------------------------------------------------- phase: http


async def bench_endpoint(session, name, path, headers, concurrency, duration):
    lats, errors = [], 0
    deadline = time.monotonic() + duration

    async def worker():
        nonlocal errors
        while time.monotonic() < deadline:
            t0 = time.monotonic()
            try:
                async with session.get(f"{BASE}{path}", headers=headers) as r:
                    await r.read()
                    if r.status != 200:
                        errors += 1
                        continue
            except aiohttp.ClientError:
                errors += 1
                continue
            lats.append(time.monotonic() - t0)

    await asyncio.gather(*[worker() for _ in range(concurrency)])
    report_latencies(f"{name} (c={concurrency})", lats, errors, duration)


async def phase_http():
    conn = aiohttp.TCPConnector(limit=300)
    async with aiohttp.ClientSession(connector=conn, cookie_jar=aiohttp.DummyCookieJar()) as s:
        token, user = await register(s)
        auth = _auth(token)
        print("== HTTP read throughput (10s per row) ==")
        for c in (50, 200):
            await bench_endpoint(s, "GET /healthz", "/healthz", {}, c, 10)
            await bench_endpoint(s, "GET /api/leaderboard", "/api/leaderboard", {}, c, 10)
            await bench_endpoint(s, "GET /api/auth/me", "/api/auth/me", auth, c, 10)
            await bench_endpoint(
                s, "GET /api/users/{id}/matches", f"/api/users/{user['id']}/matches",
                auth, c, 10,
            )


# ---------------------------------------------------------------- phase: auth


async def phase_auth():
    conn = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(connector=conn, cookie_jar=aiohttp.DummyCookieJar()) as s:
        print("== auth write path (bcrypt) ==")
        duration, conc = 15, 10
        lats, probe_lats = [], []
        deadline = time.monotonic() + duration

        async def reg_worker():
            while time.monotonic() < deadline:
                t0 = time.monotonic()
                try:
                    await register(s)
                except Exception:
                    continue
                lats.append(time.monotonic() - t0)

        async def healthz_probe():
            # Measures event-loop responsiveness of the *server* while
            # bcrypt runs: /healthz does no work, so its latency under a
            # registration storm ~= how long the loop is blocked.
            while time.monotonic() < deadline:
                t0 = time.monotonic()
                async with s.get(f"{BASE}/healthz") as r:
                    await r.read()
                probe_lats.append(time.monotonic() - t0)
                await asyncio.sleep(0.05)

        await asyncio.gather(*[reg_worker() for _ in range(conc)], healthz_probe())
        report_latencies(f"POST /api/auth/register (c={conc})", lats, 0, duration)
        report_latencies("GET /healthz during reg storm", probe_lats)


# --------------------------------------------------------------- phase: judge


async def submit_practice(session, token, problem, code):
    async with session.post(
        f"{BASE}/api/submissions",
        json={"problem_id": problem["id"], "code": code},
        headers=_auth(token),
    ) as r:
        body = await r.json()
        return r.status, body


async def wait_verdict(session, token, sub_id, timeout=90.0, poll=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # get_submission is owner-only now, so poll as the submitter.
        async with session.get(
            f"{BASE}/api/submissions/{sub_id}", headers=_auth(token)
        ) as r:
            d = await r.json()
            if d["verdict"] != "PENDING":
                return d["verdict"]
        await asyncio.sleep(poll)
    return None


TLE_CODE = "while True: pass"


async def phase_judge(rates, tle_mix=False):
    conn = aiohttp.TCPConnector(limit=300)
    async with aiohttp.ClientSession(connector=conn, cookie_jar=aiohttp.DummyCookieJar()) as s:
        async with s.get(f"{BASE}/api/problems") as r:
            problems = await r.json()
        # The AC scenarios submit SOLUTIONS[slug] for whatever the catalog holds,
        # so every seeded problem needs a reference solution. Fail early and
        # actionably here instead of a cryptic KeyError deep in the sweep.
        missing = sorted(p["slug"] for p in problems if p["slug"] not in SOLUTIONS)
        if missing:
            raise SystemExit(
                f"no reference solution for {len(missing)} seeded problem(s): "
                f"{', '.join(missing)}.\nThe AC scenarios need an AC solution per "
                "seeded problem; add them to tests/solutions.json (slug -> AC "
                "source) for the current catalog before running the load test."
            )
        users = await asyncio.gather(*[register(s, "judge") for _ in range(10)])
        tokens = [t for t, _ in users]

        print("== judging service time (idle queue, sequential) ==")
        for label, code_of in (
            ("AC (full suite)", lambda p: SOLUTIONS[p["slug"]]),
            ("WA (tier-1 early exit)", lambda _: WA_CODE),
        ):
            lats = []
            for i in range(6):
                p = problems[i % len(problems)]
                t0 = time.monotonic()
                st, body = await submit_practice(s, tokens[0], p, code_of(p))
                assert st == 202, (st, body)
                v = await wait_verdict(s, tokens[0], body["id"], poll=0.05)
                lats.append(time.monotonic() - t0)
                assert v is not None
            report_latencies(label, lats)

        mix = "50% TLE / 30% WA / 20% AC" if tle_mix else "60% WA / 40% AC"
        print(f"\n== submission-rate sweep (25s per rate, {mix}) ==")
        print("   rate = offered submissions/s; latency = POST -> verdict visible via API")
        for rate in rates:
            duration = 25
            lats, rejected, timeouts = [], 0, 0
            wrong_verdicts = {}
            waiters = []
            t_start = time.monotonic()
            n_sent = 0

            async def track(token, sub_id, t0, expect):
                nonlocal timeouts
                v = await wait_verdict(s, token, sub_id)
                if v is None:
                    timeouts += 1
                else:
                    lats.append(time.monotonic() - t0)
                    if v != expect:
                        # a load-dependent misjudgment is a correctness bug,
                        # not a performance number — count it loudly
                        key = f"{expect}->{v}"
                        wrong_verdicts[key] = wrong_verdicts.get(key, 0) + 1

            while time.monotonic() - t_start < duration:
                p = problems[n_sent % len(problems)]
                if tle_mix:
                    k = n_sent % 10
                    code, expect = (
                        (TLE_CODE, "TLE") if k < 5
                        else (WA_CODE, "WA") if k < 8
                        else (SOLUTIONS[p["slug"]], "AC")
                    )
                else:
                    code, expect = (
                        (WA_CODE, "WA") if n_sent % 5 < 3
                        else (SOLUTIONS[p["slug"]], "AC")
                    )
                tok = tokens[n_sent % len(tokens)]
                t0 = time.monotonic()
                st, body = await submit_practice(s, tok, p, code)
                if st == 202:
                    waiters.append(
                        asyncio.create_task(track(tok, body["id"], t0, expect))
                    )
                else:
                    rejected += 1
                n_sent += 1
                # pace to the offered rate
                next_at = t_start + n_sent / rate
                await asyncio.sleep(max(0.0, next_at - time.monotonic()))

            await asyncio.gather(*waiters)
            half = len(lats) // 2
            drift = (
                f"  drift: first-half p50={pct(lats[:half], 50):.2f}s "
                f"second-half p50={pct(lats[half:], 50):.2f}s"
                if half >= 5 else ""
            )
            report_latencies(f"{rate}/s offered", lats)
            if rejected or timeouts:
                print(f"      rejected={rejected} timeouts(>90s)={timeouts}")
            if wrong_verdicts:
                print(f"      WRONG VERDICTS: {wrong_verdicts}")
            if drift:
                print(f"   {drift}")
            saturated = pct(lats, 50) > 15
            if saturated:
                print("      saturated (p50 > 15s) — stopping sweep, draining…")
                break
            await asyncio.sleep(3)  # let queue drain between rates


# ------------------------------------------------------------------ phase: ws


async def phase_ws(n_duels):
    n_users = n_duels * 2
    # limit=0: each held-open websocket occupies a connector slot forever,
    # so any finite limit deadlocks the connect phase once exceeded.
    conn = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=conn, cookie_jar=aiohttp.DummyCookieJar()) as s:
        print(f"== websocket phase: {n_duels} duels / {n_users} sockets ==")

        t0 = time.monotonic()
        sem = asyncio.Semaphore(8)

        async def reg_one(i):
            async with sem:
                return await register(s, f"ws{i}")

        users = await asyncio.gather(*[reg_one(i) for i in range(n_users)])
        print(f"  registered {n_users} users in {time.monotonic() - t0:.1f}s "
              f"(bcrypt-bound)")

        t0 = time.monotonic()

        async def join(tok):
            async with s.post(
                f"{BASE}/api/queue/join", json={},
                headers=_auth(tok),
            ) as r:
                assert r.status in (200, 201), await r.text()

        await asyncio.gather(*[join(t) for t, _ in users])
        joined_at = time.monotonic()
        print(f"  all joined queue in {joined_at - t0:.1f}s")

        async def wait_matched(tok):
            while True:
                async with s.get(
                    f"{BASE}/api/queue/status",
                    headers=_auth(tok),
                ) as r:
                    d = await r.json()
                    if d.get("status") == "matched":
                        return d["match_id"]
                await asyncio.sleep(2.0)

        match_ids = await asyncio.gather(*[wait_matched(t) for t, _ in users])
        print(f"  all {n_users} players matched into {len(set(match_ids))} duels "
              f"in {time.monotonic() - joined_at:.1f}s after join")

        # open every socket and hold it
        sockets, events = [], {}
        connect_lats = []

        async def open_ws(tok, mid):
            t0 = time.monotonic()
            ws = await s.ws_connect(
                f"{BASE}/ws/duel/{mid}?last_seq=0", headers=_auth(tok)
            )
            connect_lats.append(time.monotonic() - t0)
            sockets.append(ws)
            q = events.setdefault(mid, asyncio.Queue())

            async def reader():
                try:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await q.put(json.loads(msg.data))
                except (aiohttp.ClientError, asyncio.CancelledError):
                    pass  # socket torn down at phase end

            return asyncio.create_task(reader())

        t0 = time.monotonic()
        readers = await asyncio.gather(
            *[open_ws(tok, mid) for (tok, _), mid in zip(users, match_ids)]
        )
        print(f"  opened {len(sockets)} websockets in {time.monotonic() - t0:.1f}s")
        report_latencies("ws connect (incl. event replay)", connect_lats)

        await asyncio.sleep(2)
        for q in events.values():  # drop replay/connect noise
            while not q.empty():
                q.get_nowait()

        # broadcast probe: player A submits, time until *some* holder of this
        # duel's queue sees the submission event (both sockets feed one queue).
        probe_lats = []
        sample = list(dict.fromkeys(match_ids))[:25]
        by_match = {}
        for (tok, _u), mid in zip(users, match_ids):
            by_match.setdefault(mid, tok)
        for mid in sample:
            tok = by_match[mid]
            q = events[mid]
            t0 = time.monotonic()
            async with s.post(
                f"{BASE}/api/submissions",
                json={"match_id": mid, "code": WA_CODE},
                headers=_auth(tok),
            ) as r:
                if r.status != 202:
                    continue
            while True:
                e = await asyncio.wait_for(q.get(), timeout=30)
                if e.get("type") == "submission":
                    probe_lats.append(time.monotonic() - t0)
                    break
        report_latencies(
            f"event broadcast at {len(sockets)} open sockets", probe_lats
        )

        print("  holding sockets 10s…")
        await asyncio.sleep(10)
        for ws in sockets:
            await ws.close()
        for rt in readers:
            rt.cancel()
        print(f"  closed all sockets. NOTE: {n_duels} duels now run their "
              f"grace timers and forfeit — test data only.")


# -------------------------------------------------------------------- main


async def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "http"
    if phase == "http":
        await phase_http()
    elif phase == "auth":
        await phase_auth()
    elif phase == "judge":
        rates = [float(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 \
            else [1, 2, 4, 6, 8]
        await phase_judge(rates)
    elif phase == "tle":
        rates = [float(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 \
            else [0.5, 1, 2, 3]
        await phase_judge(rates, tle_mix=True)
    elif phase == "ws":
        await phase_ws(int(sys.argv[2]) if len(sys.argv) > 2 else 230)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
