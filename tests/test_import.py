"""Tests for tools/import_problem.py — the ICPC/Kattis package importer.

Hermetic: each import targets a temp testcases root via --into/--seed, so the
repo's real testcases/ is never touched. No services needed. Run directly:

    python3 tests/test_import.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMPORTER = REPO / "tools" / "import_problem.py"
FIXTURES = REPO / "tests" / "fixtures" / "ppf"
TESTLIB = REPO / "judge" / "vendor" / "testlib.h"

sys.path.insert(0, str(REPO / "tools"))
from texhtml import PandocMissing, _pandoc          # noqa: E402


def has_pandoc() -> bool:
    """The importer renders statements with pandoc; skip where it isn't present
    (import-time-only dep — set $PANDOC or install it to run these)."""
    try:
        _pandoc()
        return True
    except PandocMissing:
        return False


def run_import(name, into, **flags):
    cmd = [sys.executable, str(IMPORTER), str(FIXTURES / name),
           "--into", str(into), "--seed", str(into / "seed.sql")]
    for k, v in flags.items():
        cmd += [f"--{k}"] if v is True else [f"--{k}", str(v)]
    return subprocess.run(cmd, capture_output=True, text=True)


def manifest(into, slug):
    return json.loads((into / slug / "manifest.json").read_text())


def test_float_import():
    with tempfile.TemporaryDirectory() as d:
        into = Path(d)
        r = run_import("average", into)
        assert r.returncode == 0, r.stderr
        m = manifest(into, "average")
        assert m["checker"] == {"type": "float", "tol": 1e-6}, m["checker"]
        assert m["tier1"] == ["01"] and m["tier2"] == ["02", "03"]  # sample/secret
        assert m["title"] == "Average of N" and m["license"] == "cc0"
        assert m["time_limit_ms"] == 1000 and m["memory_limit_kb"] == 256 * 1024
        # .ans was converted to .out
        assert (into / "average" / "01.out").read_text().strip() == "2.0"
        assert (into / "average" / "statement.html").exists()


def test_program_import_builds():
    with tempfile.TemporaryDirectory() as d:
        into = Path(d)
        r = run_import("pair", into)
        assert r.returncode == 0, r.stderr
        m = manifest(into, "pair")
        assert m["checker"]["type"] == "program"
        assert m["title"] == "Pair Sum"            # 2023 name: {en: ...} form
        chk = into / "pair" / "checker.cpp"
        assert chk.exists() and 'testlib.h' in chk.read_text()
        # the imported checker must actually build against our vendored testlib
        (into / "pair" / "testlib.h").write_text(TESTLIB.read_text())
        b = subprocess.run(
            ["g++", "-O2", "-std=gnu++17", f"-I{into/'pair'}",
             "-o", str(into / "pair" / "checker"), str(chk)],
            capture_output=True, text=True)
        assert b.returncode == 0, b.stderr


def test_tokens_default_and_sharealike_warns():
    with tempfile.TemporaryDirectory() as d:
        into = Path(d)
        r = run_import("greet", into)
        assert r.returncode == 0, r.stderr
        assert manifest(into, "greet")["checker"] == "tokens"
        assert "share-alike" in r.stdout            # cc by-sa warned but imported


def test_noncommercial_license_rejected():
    with tempfile.TemporaryDirectory() as d:
        into = Path(d)
        r = run_import("nc-prob", into)
        assert r.returncode != 0 and "REJECTED" in (r.stdout + r.stderr)
        assert not (into / "nc-prob").exists()      # nothing written
        # ...but --force lets the owner override
        r2 = run_import("nc-prob", into, force=True)
        assert r2.returncode == 0, r2.stderr
        assert (into / "nc-prob" / "manifest.json").exists()


def test_icpc_interface_validator_skipped():
    with tempfile.TemporaryDirectory() as d:
        into = Path(d)
        r = run_import("icpc-special", into)
        assert r.returncode != 0
        assert "exit-42/43" in (r.stdout + r.stderr)
        assert not (into / "icpc-special").exists()


def test_seed_idempotent_and_aggregated():
    with tempfile.TemporaryDirectory() as d:
        into = Path(d)
        run_import("average", into)
        run_import("greet", into)
        run_import("average", into)                 # re-import: must stay idempotent
        seed = (into / "seed.sql").read_text()
        assert seed.count("'average'") == 1 and "'greet'" in seed
        assert "ON CONFLICT (slug) DO NOTHING" in seed


try:
    import pytest
    # Imports render statements with pandoc; skip (don't fail) without it.
    pytestmark = pytest.mark.skipif(
        not has_pandoc(), reason="pandoc not installed (import-time-only dep)")
except ImportError:
    pass


def main():
    if not has_pandoc():
        print("SKIPPED: pandoc not installed (set $PANDOC or install it).")
        return
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name} ok")
    print("\nIMPORTER TESTS PASSED")


if __name__ == "__main__":
    main()
