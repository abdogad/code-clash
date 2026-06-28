"""Per-test isolated execution via bubblewrap, with deterministic timing and
memory accounting via cgroup v2.

Isolation: bwrap --unshare-all gives the run its own network (no interfaces at
all — the worker's Redis is unreachable from inside), PID, user, IPC and mount
namespaces. Only /usr is bind-mounted read-only; /testcases and /srv are never
mapped, so expected outputs and worker code are invisible. Fresh /proc and /dev
mounts are blocked under rootless podman (masked paths), hence the read-only /proc
bind and individual device-node binds.

Timing & memory: each run gets its own cgroup v2 node. TLE is decided on *measured
CPU time* (cpu.stat:usage_usec) and MLE on *measured peak RSS* (memory.peak), not
wall clock — so a verdict doesn't depend on machine load or interpreter startup.
Wall clock survives only as a safety timeout that kills genuine hangs via
cgroup.kill (atomic subtree SIGKILL, fork-bomb proof).

cgroups are mandatory here: bwrap runs the solution in a new PID namespace, so
os.wait4 rusage doesn't capture its CPU/RSS (a 1.8s burn reports 0.005s to the
parent). Without cgroup delegation (e.g. a dev box without --systemd=always) we
fall back to wall-clock + RLIMIT_CPU — still runs, just less precise.
"""

import asyncio
import logging
import os
import resource
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("judge.sandbox")

BWRAP = "/usr/bin/bwrap"
PYTHON = "/usr/local/bin/python3"
MAX_OUTPUT_BYTES = 8 * 1024 * 1024

# cgroup.kill is asynchronous: it SIGKILLs the subtree, but the tasks linger as
# "dying" (still attached to the cgroup) until the kernel reaps them, and rmdir
# fails with EBUSY until the cgroup is empty. So we poll rmdir for up to
# _REMOVE_RETRIES * _REMOVE_RETRY_SLEEP_S before giving up.
_REMOVE_RETRIES = 100
_REMOVE_RETRY_SLEEP_S = 0.005
# RLIMIT_NPROC counts per host uid, which under rootless podman includes
# every process of the desktop user — a tight value makes clone() fail
# before bwrap can even start. Real fork-bomb containment is the container
# pids cgroup (pids_limit in docker-compose.yml); this is only a backstop.
MAX_PROCS = 4096
MAX_OPEN_FILES = 64
# Host-safety memory cap headroom: the cgroup is killed if RSS exceeds the
# limit by this factor, but the MLE *verdict* is still decided on the limit
# itself — so a solution between 1.0x and this factor is measured, not OOM'd
# early, and correctly flagged MLE.
MEM_CAP_FACTOR = 1.25
# How often we poll the cgroup's measured CPU to enforce the time limit, and
# how far past the limit we let a run go before killing it. The grace bounds
# the CPU a busy-loop TLE wastes; the verdict is still decided on the limit.
CPU_POLL_S = 0.05
CPU_KILL_GRACE_MS = 250

CGROUP_ROOT = Path("/sys/fs/cgroup")
# Set by setup_cgroups(); None means cgroup accounting is unavailable and we
# fall back to wall-clock timing.
_cgroup_base: Path | None = None


@dataclass
class RunResult:
    timed_out: bool       # wall-clock safety timeout fired (a genuine hang)
    exit_code: int        # negative = killed by that signal
    wall_ms: int
    cpu_ms: int | None    # measured CPU (user+sys); None if no cgroup
    peak_kb: int | None   # measured peak RSS; None if no cgroup memory


def _self_cgroup_dir() -> Path:
    """The cgroup this process currently lives in, as an absolute fs path.
    /proc/self/cgroup is a single v2 line: '0::<path>'."""
    rel = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    return CGROUP_ROOT / rel.lstrip("/")


def setup_cgroups() -> bool:
    """Prepare per-run cgroup accounting; call once at worker startup.

    cgroup v2 forbids a cgroup from having both member processes and
    controllers enabled for its children, so we vacate our own cgroup by
    moving every process into a 'judge-init' leaf, then enable cpu+memory in
    subtree_control. Per-run cgroups are then created as siblings and inherit
    accurate accounting. Returns True iff this succeeded.
    """
    global _cgroup_base
    try:
        base = _self_cgroup_dir()
        init_leaf = base / "judge-init"
        init_leaf.mkdir(exist_ok=True)
        leaf_procs = init_leaf / "cgroup.procs"
        # Move every process (the worker, podman-init, ...) out of base.
        for pid in (base / "cgroup.procs").read_text().split():
            try:
                leaf_procs.write_text(pid)
            except OSError:
                pass  # kernel threads / already-exited pids
        leaf_procs.write_text(str(os.getpid()))
        # Enable child accounting; fall back to cpu-only if memory can't be
        # delegated (still fixes the TLE determinism, just no measured MLE).
        try:
            (base / "cgroup.subtree_control").write_text("+cpu +memory")
        except OSError:
            (base / "cgroup.subtree_control").write_text("+cpu")
            log.warning("cgroup memory controller unavailable; MLE stays heuristic")
        _cgroup_base = base
        log.info("per-run cgroup accounting enabled at %s", base)
        return True
    except OSError as e:
        _cgroup_base = None
        log.warning("cgroup accounting unavailable (%s); falling back to "
                    "wall-clock timing — verdicts will be less deterministic", e)
        return False


class _RunCgroup:
    """A throwaway cgroup for a single sandboxed execution."""

    def __init__(self, base: Path):
        self.path = base / f"run-{uuid.uuid4().hex}"
        self.path.mkdir()
        self.procs_path = str(self.path / "cgroup.procs")

    def set_memory_max(self, kb: int) -> None:
        try:
            # swap.max=0 first: without it the kernel pushes over-limit pages to
            # swap and throttles instead of OOM-killing, so an MLE just runs
            # slowly (and trips the time limit) rather than being caught.
            (self.path / "memory.swap.max").write_text("0")
            (self.path / "memory.max").write_text(str(kb * 1024))
        except OSError:
            pass  # memory controller not delegated

    def cpu_ms(self) -> int | None:
        try:
            for line in (self.path / "cpu.stat").read_text().splitlines():
                if line.startswith("usage_usec"):
                    return int(line.split()[1]) // 1000
        except OSError:
            pass
        return None

    def peak_kb(self) -> int | None:
        try:
            return int((self.path / "memory.peak").read_text()) // 1024
        except OSError:
            return None  # memory controller not delegated

    def kill_all(self) -> None:
        try:
            (self.path / "cgroup.kill").write_text("1")
        except OSError:
            pass

    def remove(self) -> None:
        self.kill_all()
        for _ in range(_REMOVE_RETRIES):  # wait for the kernel to drain dying tasks
            try:
                self.path.rmdir()
                return
            except OSError:
                time.sleep(_REMOVE_RETRY_SLEEP_S)


def _make_preexec(cpu_seconds: int, memory_kb: int, procs_path: str | None,
                  set_as_limit: bool):
    def preexec() -> None:
        # Join the run cgroup before exec so every descendant (across the new
        # PID namespace bwrap creates) is accounted. os.open/os.write only —
        # async-signal-safe, unlike buffered file objects, in this fork child.
        if procs_path is not None:
            fd = os.open(procs_path, os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
        os.setsid()  # own process group: the killpg fallback when no cgroup
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        # RLIMIT_AS (virtual address space) wildly over-counts Python's RSS and
        # causes false MLE; when the cgroup caps real RSS we drop it entirely.
        if set_as_limit:
            mem = memory_kb * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCS, MAX_PROCS))
        resource.setrlimit(
            resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES)
        )
        resource.setrlimit(
            resource.RLIMIT_NOFILE, (MAX_OPEN_FILES, MAX_OPEN_FILES)
        )

    return preexec


def _bwrap_cmd(workdir: str, *argv: str, writable: bool = False) -> list[str]:
    # The box is read-only for *runs* (a solution must not write files); the
    # compile step passes writable=True so the compiler can emit its binary.
    box_bind = "--bind" if writable else "--ro-bind"
    return [
        BWRAP,
        "--unshare-all",
        "--die-with-parent",
        "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin",
        "--ro-bind", "/proc", "/proc",
        "--dev-bind", "/dev/null", "/dev/null",
        "--dev-bind", "/dev/zero", "/dev/zero",
        "--dev-bind", "/dev/urandom", "/dev/urandom",
        "--dev-bind", "/dev/random", "/dev/random",
        box_bind, workdir, "/box",
        "--tmpfs", "/tmp",
        "--chdir", "/box",
        "--clearenv",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/tmp",
        # /box is read-only; bytecode cache must go to the tmpfs
        "--setenv", "PYTHONPYCACHEPREFIX", "/tmp/pycache",
        # g++ temp files (cc1plus output, etc.) also go to the tmpfs
        "--setenv", "TMPDIR", "/tmp",
        "--",
        *argv,
    ]


async def run_sandboxed(
    workdir: str,
    argv: list[str],
    *,
    stdin_path: str | Path,
    stdout_path: str | Path,
    stderr_path: str | Path,
    cpu_seconds: int,
    memory_kb: int,
    wall_ms: int,
    cpu_limit_ms: int | None = None,
    writable: bool = False,
) -> RunResult:
    """Run one command in the sandbox. I/O goes through inherited file
    descriptors, so the sandbox never needs to see the testcase paths.

    With a cgroup and cpu_limit_ms set, we poll measured CPU and kill promptly
    once it exceeds the limit (+grace); wall_ms is then only the safety net for
    a process that hangs without burning CPU (e.g. sleeping on a read)."""
    cmd = _bwrap_cmd(workdir, *argv, writable=writable)

    cg = None
    if _cgroup_base is not None:
        try:
            cg = _RunCgroup(_cgroup_base)
            cg.set_memory_max(int(memory_kb * MEM_CAP_FACTOR))
        except OSError:
            cg = None
    procs_path = cg.procs_path if cg is not None else None
    kill_cpu_ms = (
        cpu_limit_ms + CPU_KILL_GRACE_MS
        if cpu_limit_ms is not None and cg is not None
        else None
    )

    def kill() -> None:
        if cg is not None:
            cg.kill_all()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    started = time.monotonic()
    deadline = started + wall_ms / 1000
    with open(stdin_path, "rb") as fin, open(stdout_path, "wb") as fout, open(
        stderr_path, "wb"
    ) as ferr:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=fin,
            stdout=fout,
            stderr=ferr,
            preexec_fn=_make_preexec(
                cpu_seconds, memory_kb, procs_path, set_as_limit=cg is None
            ),
        )
        timed_out = False
        wait_task = asyncio.ensure_future(proc.wait())
        while True:
            done, _ = await asyncio.wait({wait_task}, timeout=CPU_POLL_S)
            if wait_task in done:
                exit_code = wait_task.result()
                break
            if kill_cpu_ms is not None:
                c = cg.cpu_ms()
                if c is not None and c > kill_cpu_ms:  # over CPU limit -> TLE
                    kill()
                    exit_code = await wait_task
                    break
            if time.monotonic() >= deadline:  # wall safety net -> genuine hang
                timed_out = True
                kill()
                exit_code = await wait_task
                break
    wall = int((time.monotonic() - started) * 1000)

    cpu_ms = peak_kb = None
    if cg is not None:
        cpu_ms = cg.cpu_ms()
        peak_kb = cg.peak_kb()
        cg.remove()
    return RunResult(
        timed_out=timed_out, exit_code=exit_code, wall_ms=wall,
        cpu_ms=cpu_ms, peak_kb=peak_kb,
    )
