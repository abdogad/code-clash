"""Unit tests for the program-checker binary cache (judge/judging.py).

Pure and fast: the real compile (_compile_checker, which needs bwrap+g++) is
stubbed, so these only exercise the cache's hashing / reuse / invalidation. Run:

    python3 tests/test_checker_cache.py
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "judge"))

import judging  # noqa: E402


def _stub_compiler(counter: dict):
    async def fake_compile(box: Path, source: str) -> None:
        counter["n"] += 1
        (box / "checker").write_bytes(b"stand-in-binary")  # what os.replace publishes
    return fake_compile


def test_compiles_once_per_distinct_source():
    judging.CHECKER_CACHE = Path(tempfile.mkdtemp())
    judging.TESTLIB_H = Path(tempfile.mktemp()); judging.TESTLIB_H.write_text("tl")
    calls = {"n": 0}
    judging._compile_checker = _stub_compiler(calls)

    async def go():
        a1 = await judging._ensure_checker_binary("checker-A")
        a2 = await judging._ensure_checker_binary("checker-A")  # cache HIT
        b1 = await judging._ensure_checker_binary("checker-B")  # different -> miss
        return a1, a2, b1

    a1, a2, b1 = asyncio.run(go())
    assert a1 == a2 and a1.exists()        # same source -> same cached binary
    assert b1 != a1                        # different source -> different slot
    assert calls["n"] == 2                 # A built once (reused), B built once


def test_testlib_change_invalidates_cache():
    judging.CHECKER_CACHE = Path(tempfile.mkdtemp())
    judging.TESTLIB_H = Path(tempfile.mktemp()); judging.TESTLIB_H.write_text("v1")
    calls = {"n": 0}
    judging._compile_checker = _stub_compiler(calls)

    async def go():
        await judging._ensure_checker_binary("checker-A")
        await judging._ensure_checker_binary("checker-A")      # hit
        judging.TESTLIB_H.write_text("v2 — testlib bumped")    # invalidates the key
        await judging._ensure_checker_binary("checker-A")      # miss -> rebuild

    asyncio.run(go())
    assert calls["n"] == 2


def _fresh_cache():
    judging.CHECKER_CACHE = Path(tempfile.mkdtemp())
    judging.TESTLIB_H = Path(tempfile.mktemp()); judging.TESTLIB_H.write_text("tl")
    judging._last_prune = 0.0
    judging._PRUNE_INTERVAL_S = 0     # let every call sweep, for deterministic tests


def test_no_count_cap_keeps_whole_active_set():
    _fresh_cache()
    judging.CHECKER_CACHE_TTL_S = 10_000
    judging._compile_checker = _stub_compiler({"n": 0})

    async def go():
        for i in range(70):           # >> the old cap of 64
            await judging._ensure_checker_binary(f"checker-{i}")

    asyncio.run(go())
    slots = [d for d in judging.CHECKER_CACHE.iterdir()
             if d.is_dir() and len(d.name) == 64]
    assert len(slots) == 70, len(slots)   # all kept — actively-used set is uncapped


def test_evicts_only_entries_unused_past_ttl():
    _fresh_cache()
    judging.CHECKER_CACHE_TTL_S = 1_000
    judging._compile_checker = _stub_compiler({"n": 0})

    async def go():
        old = await judging._ensure_checker_binary("checker-OLD")
        stale = time.time() - 5_000                     # well past the TTL, untouched
        os.utime(old.parent, (stale, stale))
        fresh = await judging._ensure_checker_binary("checker-NEW")  # triggers sweep
        return old, fresh

    old, fresh = asyncio.run(go())
    assert fresh.exists()           # recently used -> kept
    assert not old.exists()         # unused past TTL -> reclaimed


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name} ok")
    print("\nCHECKER CACHE TESTS PASSED")


if __name__ == "__main__":
    main()
