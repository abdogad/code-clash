"""Unit tests for the built-in output checkers (judge/checkers.py).

Pure and fast: no Redis, no sandbox, no API — they only exercise the in-process
diff/tokens/float comparison and the manifest spec parser. Run directly:

    python3 tests/test_checkers.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "judge"))

from checkers import CheckerSpec, compare, parse_checker  # noqa: E402


def b(s: str) -> bytes:
    return s.encode()


def test_parse_defaults_to_diff():
    assert parse_checker({}).kind == "diff"
    assert parse_checker({"checker": "diff"}).kind == "diff"
    assert parse_checker({"checker": "tokens"}).kind == "tokens"
    f = parse_checker({"checker": {"type": "float", "tol": 1e-3}})
    assert f.kind == "float" and f.tol == 1e-3
    assert parse_checker({"checker": {"type": "float"}}).tol == 1e-6  # default
    p = parse_checker({"checker": {"type": "program", "source": "chk.cpp"}})
    assert p.kind == "program" and p.source == "chk.cpp"
    assert parse_checker({"checker": {"type": "program"}}).source == "checker.cpp"


def test_parse_rejects_garbage():
    for bad in ("nope", {"type": "nope"}, 5):
        try:
            parse_checker({"checker": bad})
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_diff_matches_legacy_normalization():
    spec = CheckerSpec("diff")
    # trailing whitespace / trailing newlines ignored; interior layout matters
    assert compare(spec, b("5\n"), b("5"))[0]
    assert compare(spec, b("5  \n\n"), b("5"))[0]
    assert not compare(spec, b("1 2"), b("1  2"))[0]   # interior space differs
    assert not compare(spec, b("6"), b("5"))[0]


def test_tokens_is_whitespace_insensitive():
    spec = CheckerSpec("tokens")
    assert compare(spec, b("1  2\t3\n"), b("1 2 3"))[0]
    assert compare(spec, b("1\n2\n3\n"), b("1 2 3"))[0]   # line breaks collapse
    assert not compare(spec, b("1 2 3 4"), b("1 2 3"))[0]  # extra token
    assert not compare(spec, b("1 2 9"), b("1 2 3"))[0]


def test_float_tolerance():
    spec = CheckerSpec("float", tol=1e-6)
    assert compare(spec, b("3.1415926"), b("3.1415927"))[0]   # within tol
    assert compare(spec, b("2.0 4.0"), b("2 4"))[0]            # int vs float token
    assert not compare(spec, b("3.14"), b("3.15"))[0]         # outside tol
    assert not compare(spec, b("1.0 2.0"), b("1.0"))[0]       # token count differs
    # non-numeric tokens compare exactly even under the float checker
    assert compare(spec, b("YES 1.0000001"), b("YES 1.0"))[0]
    assert not compare(spec, b("NO 1.0"), b("YES 1.0"))[0]


def test_float_relative_tolerance_for_large_values():
    spec = CheckerSpec("float", tol=1e-6)
    # absolute diff is 1.0 (huge) but relative diff is ~1e-9 → accepted
    assert compare(spec, b("1000000000.000001"), b("1000000000.0"))[0]


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name} ok")
    print("\nCHECKER UNIT TESTS PASSED")


if __name__ == "__main__":
    main()
