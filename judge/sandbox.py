"""Per-test isolated execution, delegated to the `tallyrun` sandbox binary.

tallyrun (https://github.com/abdogad/tallyrun) is the bwrap + cgroup-v2 sandbox
this module used to implement in-process, now packaged as a static binary with a
one-JSON-line contract. It owns isolation (`bwrap --unshare-all`: fresh net/PID/
user/IPC/mount namespaces — the worker's Redis is unreachable from inside),
per-run cgroup accounting (measured CPU + peak RSS across the PID namespace,
fork-bomb-proof teardown), the memory cap, and the rlimit backstops.

What it adds over the old in-process sandbox is the point of the switch: retired
user-space **instructions** (`perf_event_open` across the process tree) as a
load-invariant "virtual time". TLE is decided on that, so the same solution gets
the same verdict on an idle box and a busy one (see judging.py). On a host with
no PMU/perf access tallyrun reports `measurement:"degraded"` and we fall back to
measured CPU time, like a classic judge.

tallyrun manages its own per-run cgroups, so there is no worker-startup setup to
do here — one `tallyrun run` in, one JSON line out.
"""

import asyncio
import json
import os
from dataclasses import dataclass

TALLYRUN = os.environ.get("TALLYRUN", "/usr/local/bin/tallyrun")
PYTHON = "/usr/local/bin/python3"
MAX_OUTPUT_BYTES = 8 * 1024 * 1024

# Hard-fail (exit 3) instead of silently degrading to CPU-time verdicts when the
# instruction counter is unavailable. Off by default so dev boxes / no-PMU CI
# still run (falling back to cpu_ms); the prod overlay sets it to "1" so a judge
# host that loses its PMU surfaces as an internal error rather than quietly
# handing out load-dependent verdicts.
REQUIRE_INSN = os.environ.get("JUDGE_REQUIRE_INSN") == "1"


@dataclass
class RunResult:
    """One tallyrun JSON result. Fields mirror docs/CONTRACT.md; parsed
    leniently (unknown keys ignored) per tallyrun's stability promise."""
    exit_code: int | None   # the command's exit code; None if killed by a signal
    signal: int | None      # terminating signal (9 kill, 24 SIGXCPU backstop, …)
    timed_out: bool         # wall-clock safety timeout fired -> a genuine hang
    killed: str | None      # why tallyrun killed it: "instructions"|"cpu"|"wall"|None
    instructions: int | None  # retired user-space instructions; None if degraded
    measurement: str        # "full" | "degraded" (no PMU -> instructions is None)
    accounting: str         # "cgroup" | "cpu-only" | "rusage"
    cpu_ms: int             # measured CPU time (user+sys)
    wall_ms: int
    peak_kb: int            # measured peak RSS

    @classmethod
    def from_json(cls, line: str) -> "RunResult":
        d = json.loads(line)
        return cls(
            exit_code=d["exit_code"], signal=d["signal"],
            timed_out=d["timed_out"], killed=d["killed"],
            instructions=d.get("instructions"), measurement=d["measurement"],
            accounting=d["accounting"], cpu_ms=d["cpu_ms"],
            wall_ms=d["wall_ms"], peak_kb=d["peak_kb"],
        )


async def run_sandboxed(
    workdir: str,
    argv: list[str],
    *,
    stdin_path: str,
    stdout_path: str,
    stderr_path: str,
    cpu_seconds: int,
    memory_kb: int,
    wall_ms: int,
    insn_limit: int | None = None,
    writable: bool = False,
    binds: tuple[str, ...] = (),
) -> RunResult:
    """Run one command in the tallyrun sandbox and return the parsed result.

    tallyrun opens stdin/stdout/stderr from the given paths itself, so the
    sandboxed program never sees the host testcase paths (I/O rides on inherited
    fds). `insn_limit` is the retired-instruction budget: tallyrun kills the run
    with `killed:"instructions"` once it is exceeded — a load-invariant TLE.
    `cpu_seconds` (RLIMIT_CPU) and `wall_ms` are backstops for the cases the
    instruction counter misses (syscall-heavy code) or a genuine hang.

    Raises RuntimeError if tallyrun itself fails to start the sandbox (exit 3,
    no JSON) — infrastructure, routed to the worker's internal-error path, never
    a verdict against the contestant.
    """
    cmd = [
        TALLYRUN, "run",
        "--box", str(workdir),
        "--stdin", str(stdin_path),
        "--stdout", str(stdout_path),
        "--stderr", str(stderr_path),
        "--wall-ms", str(wall_ms),
        "--cpu-s", str(cpu_seconds),
        "--mem-kb", str(memory_kb),
    ]
    if insn_limit is not None:
        cmd += ["--insn-limit", str(insn_limit)]
        if REQUIRE_INSN:
            cmd += ["--require-insn"]
    if writable:
        cmd += ["--writable"]
    for b in binds:
        cmd += ["--bind", b]
    cmd += ["--", *argv]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,  # tallyrun's one JSON line
        stderr=asyncio.subprocess.PIPE,  # tallyrun's own warnings/errors
    )
    out, err = await proc.communicate()
    lines = out.decode(errors="replace").strip().splitlines()
    # No JSON line means the sandbox itself failed to start, or a --require
    # guarantee could not be met (exit 3): our infra, not the contestant's code.
    # tallyrun mirrors the child's exit code, so proc.returncode is NOT a failure
    # signal — a program exiting 3 still yields a valid result to parse for RE.
    if not lines:
        raise RuntimeError(
            f"tallyrun failed to run sandbox (exit {proc.returncode}): "
            f"{err.decode(errors='replace').strip()}"
        )
    return RunResult.from_json(lines[-1])
