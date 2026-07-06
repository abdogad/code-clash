"""Tiered, early-exit judging schedule.

Tier 1 (samples + killer tests) runs first and stops at the first failure — most
wrong attempts die here cheaply. Only tier-1 survivors run the full tier-2 suite,
still with early exit and a cumulative cap per submission.

Verdicts are decided on *measured* CPU time and peak RSS (per-run cgroup, see
sandbox.py), not wall clock, so machine load can't fail a correct solution. A
per-language CPU/memory multiplier keeps one problem limit fair across C++ and
(slower) Python.
"""

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from checkers import compare, parse_checker
from sandbox import MAX_OUTPUT_BYTES, PYTHON, RunResult, run_sandboxed

log = logging.getLogger("judge.judging")

GXX = "/usr/bin/g++"

TESTCASES_DIR = Path(os.environ.get("TESTCASES_DIR", "/testcases"))
COMPILE_WALL_MS = 15_000          # g++ -O2 can be slow; generous
COMPILE_CPU_S = 12
COMPILE_MEM_KB = 512 * 1024       # g++ -O2 is memory-hungry
MAX_VERDICT_DETAIL = 200

# The virtual clock: retired instructions one millisecond of a problem's time
# limit buys. 2e6/ms = the "2 GHz CPU retiring one instruction per cycle"
# convention (sio2jail). A policy knob — calibrate it (and per-problem limits)
# against reference solutions on the judging hardware; err generous, since a
# loose budget only admits slightly slower solutions while a tight one rejects
# correct ones. Used only when tallyrun reports measurement:"full"; a degraded
# host (no PMU) judges on measured CPU ms instead.
INSN_PER_MS = int(os.environ.get("INSN_PER_MS", 2_000_000))

# Output-checker (jury code) limits — separate from and more generous than the
# contestant's run budget; checker CPU is never charged against the contestant.
CHECKER_CPU_S = 10
CHECKER_MEM_KB = 256 * 1024
CHECKER_WALL_MS = 10_000
# Vendored testlib.h, staged into the checker box at compile time (the sandbox
# binds only /usr + the box, so the header must live alongside the source).
TESTLIB_H = Path(__file__).resolve().parent / "vendor" / "testlib.h"
# Compiled program-checker binaries are cached here, keyed by a hash of the
# source + testlib.h, so a checker is built once and reused across submissions
# instead of recompiling testlib.h (~seconds) every time for an identical result.
CHECKER_CACHE = Path(os.environ.get("CHECKER_CACHE_DIR") or tempfile.gettempdir()) \
    / "cc-checker-cache"
# Eviction by age, not count: every use re-stamps a checker, so the active working
# set stays cached regardless of problem count, and only entries unused past the
# TTL are removed (orphans from retired problems or a testlib bump that re-keys
# everything). The sweep is throttled so the per-submission cost stays O(1).
CHECKER_CACHE_TTL_S = int(os.environ.get("CHECKER_CACHE_TTL_DAYS") or 30) * 86_400
_PRUNE_INTERVAL_S = 3_600
_last_prune = 0.0

# Per-language config. time_mult/mem_mult scale a problem's single limit to be fair
# across languages (Python is ~10-50x slower than C++). Raising a multiplier helps
# slow languages but lets a TLE burn that much more CPU, trading against worst-case
# judge throughput (see docs/loadtest.md).
LANGUAGES = {
    "python": {
        "source": "solution.py",
        # syntax error -> CE (not 20x RE); pyc cache goes to the tmpfs
        "compile": [
            PYTHON, "-c",
            "import py_compile; py_compile.compile('solution.py',"
            " cfile='/tmp/solution.pyc', doraise=True)",
        ],
        "compile_writable": False,
        "run": [PYTHON, "solution.py"],
        "time_mult": 3.0,
        "mem_mult": 2.0,
    },
    "cpp": {
        "source": "solution.cpp",
        # compile into the (writable) box so the run steps see the binary;
        # dynamic libstdc++/libc come from the ro-bound /usr.
        "compile": [
            GXX, "-O2", "-std=gnu++17", "-o", "/box/solution", "/box/solution.cpp",
        ],
        "compile_writable": True,
        "run": ["/box/solution"],
        "time_mult": 1.0,
        "mem_mult": 1.0,
    },
}


def _read_capped(path: Path, cap: int = 64 * 1024) -> bytes:
    with open(path, "rb") as f:
        return f.read(cap)


async def _compile_checker(box: Path, source: str) -> None:
    """Compile a testlib.h program checker into <box>/checker (once per
    submission). Raises on failure — a broken jury checker is our bug, not the
    contestant's, so it must surface as an internal error, never a verdict."""
    box.joinpath("checker.cpp").write_text(source)
    box.joinpath("testlib.h").write_text(TESTLIB_H.read_text())
    out, err = box / "chk_compile_out", box / "chk_compile_err"
    res = await run_sandboxed(
        str(box),
        [GXX, "-O2", "-std=gnu++17", "-I/box", "-o", "/box/checker", "/box/checker.cpp"],
        stdin_path="/dev/null", stdout_path=out, stderr_path=err,
        cpu_seconds=COMPILE_CPU_S, memory_kb=COMPILE_MEM_KB, wall_ms=COMPILE_WALL_MS,
        writable=True,
    )
    if res.exit_code != 0:
        raise RuntimeError(
            "checker compile failed: " + _read_capped(err).decode(errors="replace")
        )


async def _ensure_checker_binary(source: str) -> Path:
    """Return a compiled checker binary, building only on a cache miss.

    Keyed by source + the testlib.h it links against, so re-running the same checker
    is free and bumping testlib invalidates stale binaries. The build box lives on
    the cache filesystem so publishing is an atomic rename; concurrent builds of the
    same key overwrite an identical result, which is harmless."""
    key = hashlib.sha256(source.encode() + TESTLIB_H.read_bytes()).hexdigest()
    slot = CHECKER_CACHE / key
    binary = slot / "checker"
    if not binary.exists():
        CHECKER_CACHE.mkdir(parents=True, exist_ok=True)
        box = Path(tempfile.mkdtemp(prefix="build-", dir=CHECKER_CACHE))
        try:
            await _compile_checker(box, source)
            slot.mkdir(parents=True, exist_ok=True)
            os.replace(box / "checker", binary)  # atomic publish within the cache fs
        finally:
            shutil.rmtree(box, ignore_errors=True)
    _mark_used_and_prune(slot)
    return binary


def _slot_mtime(d: Path) -> float:
    try:
        return d.stat().st_mtime
    except OSError:
        return 0.0


def _mark_used_and_prune(slot: Path) -> None:
    """Stamp this slot as just-used (so anything active stays cached), then — at most
    once per _PRUNE_INTERVAL_S — sweep entries unused past the TTL. Touching first
    means an in-use entry is never the one removed."""
    try:
        os.utime(slot, None)
    except OSError:
        pass
    global _last_prune
    now = time.time()
    if now - _last_prune < _PRUNE_INTERVAL_S:
        return
    _last_prune = now
    cutoff = now - CHECKER_CACHE_TTL_S
    try:
        slots = [d for d in CHECKER_CACHE.iterdir()
                 if d.is_dir() and len(d.name) == 64]  # sha256 slots, not build-*
    except OSError:
        return
    for d in slots:
        if _slot_mtime(d) < cutoff:  # unused past the TTL -> orphan, reclaim it
            shutil.rmtree(d, ignore_errors=True)


async def _run_program_checker(
    box: Path, in_path: Path, out_path: Path, ans_path: Path
) -> tuple[bool, str]:
    """Run the compiled checker on one test via the testlib CLI contract:
    `checker <input> <participant_output> <jury_answer>`, exit 0 = AC, 1/2 = WA.
    Returns (ok, detail). Raises on _fail (exit 3) / crash — the checker itself
    broke, which is an internal error rather than a verdict against the player."""
    box.joinpath("input.txt").write_bytes(in_path.read_bytes())
    box.joinpath("output.txt").write_bytes(out_path.read_bytes())  # contestant stdout
    box.joinpath("answer.txt").write_bytes(ans_path.read_bytes())
    cout, cerr = box / "chk_out", box / "chk_err"
    res = await run_sandboxed(
        str(box),
        ["/box/checker", "/box/input.txt", "/box/output.txt", "/box/answer.txt"],
        stdin_path="/dev/null", stdout_path=cout, stderr_path=cerr,
        cpu_seconds=CHECKER_CPU_S, memory_kb=CHECKER_MEM_KB, wall_ms=CHECKER_WALL_MS,
    )
    # tallyrun raises on a sandbox-start failure, so any result here is the
    # checker's own exit. testlib prints the verdict text to stderr; fall to stdout.
    stderr = _read_capped(cerr).decode(errors="replace")
    msg = stderr.strip() or _read_capped(cout).decode(errors="replace").strip()
    if res.exit_code == 0:                 # _ok
        return True, ""
    if res.exit_code in (1, 2):            # _wa / _pe (no PE verdict yet → WA)
        return False, msg
    # exit 3 (_fail) or a crash (exit_code None on a signal) — the checker broke.
    raise RuntimeError(
        f"checker failed (exit {res.exit_code}, signal {res.signal}): {msg}"
    )


async def judge_submission(fields: dict, on_progress=None) -> dict:
    """Judge one submission message from the stream; returns the verdict
    message for the verdicts stream.

    `on_progress(passed, total)` — if given, an async callback invoked once
    the test count is known and after every passing test, so the duel can
    drive a real per-test progress bar. Purely cosmetic; verdicts are the
    authoritative result."""
    slug = fields["problem_slug"]
    time_limit_ms = int(fields["time_limit_ms"])
    memory_limit_kb = int(fields["memory_limit_kb"])
    language = fields.get("language", "python")

    problem_dir = TESTCASES_DIR / slug
    manifest = json.loads((problem_dir / "manifest.json").read_text())
    tests: list[str] = list(manifest["tier1"]) + list(manifest["tier2"])
    checker = parse_checker(manifest)  # how AC/WA is decided (default: diff)

    workdir = tempfile.mkdtemp(prefix="sub-")
    checker_dir: str | None = None  # allocated only for a program checker
    try:
        def verdict(v: str, passed: int, detail: str = "",
                    res: RunResult | None = None, test: str = "") -> dict:
            # One diagnostic line per submission, logged where the verdict is
            # decided so the decisive run's raw tallyrun JSON is on record: the
            # ground truth behind the call (retired instructions vs cpu_ms, peak
            # RSS, kill reason). WARNING when the host fell off the instruction
            # counter — that silently makes verdicts load-dependent (sandbox.py).
            degraded = res is not None and (
                res.measurement != "full" or res.accounting != "cgroup")
            log.log(
                logging.WARNING if degraded else logging.INFO,
                "judged %s %s %s %d/%d -> %s%s%s",
                fields["submission_id"], slug, language, passed, len(tests), v,
                f" test={test}" if test else "",
                f" sandbox={res.raw}" if res is not None else "",
            )
            return {
                "submission_id": fields["submission_id"],
                "verdict": v,
                "tests_passed": str(passed),
                "tests_total": str(len(tests)),
                "detail": detail[:MAX_VERDICT_DETAIL],
            }

        lang = LANGUAGES.get(language)
        if lang is None:
            return verdict("CE", 0, f"unsupported language: {language}")

        # Effective per-test limits after the language fairness multiplier.
        eff_time_ms = int(time_limit_ms * lang["time_mult"])
        eff_mem_kb = int(memory_limit_kb * lang["mem_mult"])
        # The instruction budget is the TLE authority; RLIMIT_CPU is only a loose
        # backstop for syscall-heavy code the counter under-measures (3x headroom).
        insn_limit = eff_time_ms * INSN_PER_MS
        run_cpu_s = max(1, (3 * eff_time_ms) // 1000) + 2
        per_test_wall_ms = 2 * eff_time_ms + 5000  # hang safety net; never the verdict

        Path(workdir, lang["source"]).write_text(fields["code"])
        out_path = Path(workdir, "stdout")
        err_path = Path(workdir, "stderr")

        # Compile step: Python syntax-check or C++ build. Generous, separate
        # limits — compilation time/memory is not the contestant's run budget.
        res = await run_sandboxed(
            workdir,
            lang["compile"],
            stdin_path="/dev/null",
            stdout_path=out_path,
            stderr_path=err_path,
            cpu_seconds=COMPILE_CPU_S,
            memory_kb=COMPILE_MEM_KB,
            wall_ms=COMPILE_WALL_MS,
            writable=lang["compile_writable"],
        )
        if res.exit_code != 0:
            return verdict("CE", 0, _read_capped(err_path).decode(errors="replace"),
                           res=res)

        # Custom checker: fetch its compiled binary (cached across submissions by
        # source hash — a contestant CE above never reaches here, so a broken
        # solution never even triggers a cache miss) and stage it into the run box.
        if checker.kind == "program":
            checker_dir = tempfile.mkdtemp(prefix="chk-")
            binary = await _ensure_checker_binary(
                (problem_dir / checker.source).read_text()
            )
            shutil.copy2(binary, Path(checker_dir) / "checker")

        # A correct solution passing every test under its limit must never hit
        # this cumulative cap, so base it on the (virtual) time it may consume.
        budget_ms = eff_time_ms * len(tests) + 2000
        passed = 0
        if on_progress is not None:
            await on_progress(0, len(tests))  # bar starts at 0/total
        for name in tests:
            if budget_ms <= 0:
                return verdict("TLE", passed, "cumulative time cap")
            res = await run_sandboxed(
                workdir,
                lang["run"],
                stdin_path=str(problem_dir / f"{name}.in"),
                stdout_path=str(out_path),
                stderr_path=str(err_path),
                cpu_seconds=run_cpu_s,
                memory_kb=eff_mem_kb,
                wall_ms=per_test_wall_ms,
                insn_limit=insn_limit,
            )

            # Virtual time = retired instructions / INSN_PER_MS when the counter
            # is live (load-invariant); measured CPU ms on a degraded host.
            if res.measurement == "full" and res.instructions is not None:
                used_ms = res.instructions // INSN_PER_MS
            else:
                used_ms = res.cpu_ms
            budget_ms -= used_ms

            # TLE: tallyrun hit the instruction budget, a genuine wall-clock hang,
            # virtual time over the limit, or a CPU-time backstop on syscall-heavy
            # code the instruction counter under-measures. That backstop is either
            # the subtree cgroup cpu.stat kill (killed:"cpu", SIGKILL) or the
            # per-process RLIMIT_CPU SIGXCPU (24) — and bwrap (tallyrun's child)
            # reaps the signalled solution and exits 128+signal, so a SIGXCPU
            # reaches us as exit_code 152, not signal 24.
            if (res.killed in ("instructions", "cpu") or res.timed_out
                    or used_ms > eff_time_ms
                    or res.signal == 24 or res.exit_code == 128 + 24):
                return verdict("TLE", passed, f"test {name}: {used_ms} ms",
                               res=res, test=name)

            # MLE: measured peak over the limit. tallyrun caps real RSS at 1.25x,
            # so a run between 1.0x and 1.25x is measured over-limit, not OOM'd
            # early; at >=1.25x the kernel OOM-kills it (a signal, caught below).
            if res.peak_kb > eff_mem_kb:
                return verdict("MLE", passed, f"test {name}: {res.peak_kb} kB",
                               res=res, test=name)

            # RE: nonzero exit, or a terminating signal tallyrun didn't attribute
            # to a limit. The stderr string match reclassifies an OOM crash as MLE
            # on a degraded host, where per-process peak_kb under-reports (Python
            # MemoryError, C++/glibc out-of-memory aborts).
            if (res.exit_code is not None and res.exit_code != 0) or res.signal is not None:
                stderr = _read_capped(err_path).decode(errors="replace")
                mem_signal = any(s in stderr for s in
                                 ("MemoryError", "bad_alloc", "out of memory"))
                last = stderr.strip().splitlines()[-1] if stderr.strip() else ""
                return verdict("MLE" if mem_signal else "RE", passed,
                               f"test {name}: {last}", res=res, test=name)
            ans_path = problem_dir / f"{name}.out"
            if checker.kind == "program":
                ok, why = await _run_program_checker(
                    Path(checker_dir), problem_dir / f"{name}.in", out_path, ans_path
                )
            else:
                ok, why = compare(
                    checker, _read_capped(out_path, MAX_OUTPUT_BYTES),
                    ans_path.read_bytes(),
                )
            if not ok:
                return verdict("WA", passed,
                               f"test {name}: {why}" if why else f"test {name}",
                               res=res, test=name)
            passed += 1
            if on_progress is not None:
                await on_progress(passed, len(tests))
        return verdict("AC", passed, res=res)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if checker_dir is not None:
            shutil.rmtree(checker_dir, ignore_errors=True)
