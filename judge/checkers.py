"""Output checkers (a.k.a. output validators) — the pluggable correctness rule
that decides AC vs WA for one test, mirroring the Codeforces/testlib model.

A problem selects its checker in manifest.json via an optional "checker" field.
Absent ⇒ "diff" (the legacy line-compare), so existing problems are unchanged:

    "checker": "diff"                              # exact line compare (default)
    "checker": "tokens"                            # whitespace-insensitive
    "checker": {"type": "float", "tol": 1e-6}      # numeric tokens within tol
    "checker": {"type": "program", "source": "checker.cpp"}  # custom (testlib)

The first three are *built-in* and decided here, in-process: they only look at
already-captured, size-capped bytes (the contestant's stdout and the jury
answer) and never run contestant code, so they need no sandbox. The "program"
form is handled by judging.py, which compiles the source against testlib.h and
runs it sandboxed; this module only parses and represents its spec.
"""

from dataclasses import dataclass

BUILTIN_KINDS = {"diff", "tokens", "float"}


@dataclass(frozen=True)
class CheckerSpec:
    kind: str             # "diff" | "tokens" | "float" | "program"
    tol: float = 1e-6     # float: absolute-or-relative tolerance
    source: str = ""      # program: source filename inside the problem dir


def parse_checker(manifest: dict) -> CheckerSpec:
    """Resolve manifest["checker"] into a CheckerSpec. Absent ⇒ diff.

    Raises ValueError on a malformed spec — a problem-config bug, which the
    worker surfaces as an internal error rather than miscaching a verdict."""
    spec = manifest.get("checker")
    if spec is None:
        return CheckerSpec("diff")
    if isinstance(spec, str):
        if spec not in BUILTIN_KINDS:
            raise ValueError(
                f"unknown built-in checker {spec!r}; use one of "
                f"{sorted(BUILTIN_KINDS)} or a {{\"type\": \"program\", ...}} object"
            )
        return CheckerSpec(spec)
    if isinstance(spec, dict):
        kind = spec.get("type", "diff")
        if kind == "float":
            return CheckerSpec("float", tol=float(spec.get("tol", 1e-6)))
        if kind == "program":
            return CheckerSpec("program", source=spec.get("source", "checker.cpp"))
        if kind in BUILTIN_KINDS:
            return CheckerSpec(kind)
        raise ValueError(f"unknown checker type {kind!r}")
    raise ValueError(f"invalid checker spec: {spec!r}")


def _lines(text: bytes) -> list[str]:
    """'diff' normalization: drop trailing blank lines, then trailing whitespace
    on every line."""
    body = text.decode("utf-8", errors="replace").rstrip().split("\n")
    return [line.rstrip() for line in body]


def _tokens(text: bytes) -> list[str]:
    return text.decode("utf-8", errors="replace").split()


def _float_ok(a: str, b: str, tol: float) -> bool:
    """True if tokens a (got) and b (expected) match. Numeric tokens compare
    with absolute-or-relative tolerance (testlib rcmp); otherwise exact."""
    try:
        x, y = float(a), float(b)
    except ValueError:
        return a == b
    if x != x or y != y:                 # NaN matches only NaN
        return x != x and y != y
    d = abs(x - y)
    return d <= tol or d <= tol * abs(y)


def compare(spec: CheckerSpec, got: bytes, expected: bytes) -> tuple[bool, str]:
    """Decide AC (True) vs WA (False) for one test with a *built-in* checker.
    Returns (ok, detail); detail is a short reason on mismatch. Not for
    spec.kind == 'program' — judging.py runs that sandboxed."""
    if spec.kind == "diff":
        return (_lines(got) == _lines(expected), "")
    if spec.kind == "tokens":
        g, e = _tokens(got), _tokens(expected)
        if g == e:
            return (True, "")
        return (False, f"expected {len(e)} tokens, got {len(g)}")
    if spec.kind == "float":
        g, e = _tokens(got), _tokens(expected)
        if len(g) != len(e):
            return (False, f"expected {len(e)} tokens, got {len(g)}")
        for i, (a, b) in enumerate(zip(g, e)):
            if not _float_ok(a, b, spec.tol):
                return (False, f"token {i}: expected {b!r}, got {a!r}")
        return (True, "")
    raise ValueError(f"compare() is for built-in checkers, not {spec.kind!r}")
