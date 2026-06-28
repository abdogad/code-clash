#!/usr/bin/env python3
"""Import an ICPC / Kattis problem package into CodeClash's testcases layout.

Converts a Problem Package Format directory (problem.yaml + data/ + optional
output_validators/) into testcases/<slug>/ that the judge already understands:
NN.in / NN.out pairs, a manifest.json (tier1 = samples, tier2 = secret) carrying
the right `checker` field, a statement.html (rendered from the package's LaTeX or
Markdown by pandoc; see tools/texhtml.py), and provenance (source/license/uuid).
The judge ignores the extra manifest keys; only tier1/tier2/checker matter to it.

License gate: the problem.yaml `license:` field is the source of truth. Only
clean licenses pass (cc0 / public domain / cc by / mit / bsd / apache); cc by-sa
passes with a share-alike warning; anything non-commercial, unknown, or missing
is rejected unless you pass --force (and own the decision).

Validation mapping:
  - default (no output_validators)        -> "tokens"  (Kattis default is
       with float_tolerance flags         -> {"type":"float","tol":...}     token-based)
       with space_change_sensitive        -> "diff"
  - custom validator that includes testlib.h -> {"type":"program",...} (Codeforces/Polygon)
  - custom validator on the ICPC exit-42/43 interface -> SKIPPED with a warning
       (a different contract than our testlib engine speaks; not yet supported).

Re-running is idempotent. db/seed/problems.sql is regenerated from every
imported problem in testcases/ (those whose manifest has a "source"); the app
seeds it into an empty DB at startup (app/codeclash/migrate.py).

Usage:
  python3 tools/import_problem.py <package_dir> [--slug S] [--band N] [--force]
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

import yaml

from texhtml import to_html

REPO = Path(__file__).resolve().parent.parent
TC_ROOT = REPO / "testcases"                         # set from --into in main()
SEED_FILE = REPO / "db" / "seed" / "problems.sql"  # set from --seed in main()

DEFAULT_TIME_MS = 2000
DEFAULT_MEM_KB = 256 * 1024
# Seed the dynamic problem rating from the band; it self-calibrates from solve
# outcomes thereafter (app/codeclash/rating/problems.py). --rating overrides.
BAND_RATING = {1: 1000, 2: 1500, 3: 2000}


# ----- problem.yaml helpers (handle legacy + 2023 key variants) --------------

def load_meta(pkg: Path) -> dict:
    f = pkg / "problem.yaml"
    if not f.exists():
        sys.exit(f"not a problem package: {f} missing")
    return yaml.safe_load(f.read_text()) or {}


def pick_title(meta: dict, fallback: str) -> str:
    name = meta.get("name")
    if isinstance(name, dict):                       # 2023: {en: "..."} per lang
        return name.get("en") or next(iter(name.values()), fallback)
    return name or fallback


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def license_status(meta: dict) -> tuple[str, str]:
    """Return (verdict, normalized) where verdict is ok / warn / reject."""
    lic = str(meta.get("license", "")).strip().lower()
    if not lic or lic in ("unknown", "educational", "permission"):
        return "reject", lic or "(missing)"
    if "nc" in lic or "noncommercial" in lic or "non-commercial" in lic:
        return "reject", lic
    if "sa" in lic:                                  # share-alike: usable, obligations
        return "warn", lic
    if any(k in lic for k in ("cc0", "public domain", "cc by", "cc-by",
                              "mit", "bsd", "apache")):
        return "ok", lic
    return "reject", lic                             # unrecognized -> be conservative


def pick_limits(meta: dict, pkg: Path) -> tuple[int, int]:
    limits = meta.get("limits") or {}
    secs = None
    tl_file = pkg / ".timelimit"                     # problemtools convention
    if tl_file.exists():
        try:
            secs = float(tl_file.read_text().strip())
        except ValueError:
            pass
    if secs is None and "time_limit" in limits:
        secs = float(limits["time_limit"])
    time_ms = int(secs * 1000) if secs else DEFAULT_TIME_MS
    mem_kb = int(limits["memory"]) * 1024 if "memory" in limits else DEFAULT_MEM_KB
    return time_ms, mem_kb


def collect_statement(pkg: Path, title: str) -> str:
    """Render the package's statement to HTML (pandoc; see tools/texhtml.py).
    Markdown source wins if present (it carries its own title heading), else the
    LaTeX source — which has no rendered title, so we prepend one as an <h1>.
    Images are inlined as base64 `data:` URIs resolved relative to the source
    file; math is left as `<span class="math">` for the frontend's KaTeX pass."""
    for cand in ("problem_statement/problem.en.md", "problem_statement/problem.md",
                 "statement/problem.en.md"):
        p = pkg / cand
        if p.exists():
            return to_html(p.read_text(errors="replace"),
                           from_fmt="markdown", base_dir=p.parent)
    head = f"<h1>{html.escape(title)}</h1>\n"
    tex = list((pkg / "problem_statement").glob("*.tex")) if \
        (pkg / "problem_statement").exists() else []
    if tex:
        return head + to_html(tex[0].read_text(errors="replace"),
                              from_fmt="latex", base_dir=tex[0].parent)
    return head + "<p><em>Imported problem (no statement source found).</em></p>\n"


def samples_html(sample_ins: list[Path]) -> str:
    """Render the sample cases as an HTML section appended to the statement —
    problemtools keeps samples out of the .tex, so the frontend would otherwise
    have nothing to show. Each is an Input/Output pair; the frontend styles the
    `.samples`/`.sample-io` markup and adds copy buttons over the `<pre>`s."""
    pairs = []
    for in_f in sample_ins:
        ans = answer_for(in_f)
        if ans is not None:
            pairs.append((in_f.read_text(errors="replace").rstrip("\n"),
                          ans.read_text(errors="replace").rstrip("\n")))
    if not pairs:
        return ""
    out = ['<section class="samples">', "<h2>Samples</h2>"]
    for i, (inp, outp) in enumerate(pairs, 1):
        label = f"Sample {i}" if len(pairs) > 1 else "Sample"
        out.append(
            f'<div class="sample"><div class="sample-head">{label}</div>'
            '<div class="sample-grid">'
            '<div class="sample-pane"><div class="sample-label">Input</div>'
            f'<pre class="sample-io">{html.escape(inp)}</pre></div>'
            '<div class="sample-pane"><div class="sample-label">Output</div>'
            f'<pre class="sample-io">{html.escape(outp)}</pre></div>'
            "</div></div>"
        )
    out.append("</section>")
    return "\n".join(out) + "\n"


# ----- test data -------------------------------------------------------------

def collect_cases(pkg: Path) -> tuple[list[Path], list[Path]]:
    """Return (sample_in_files, secret_in_files), each sorted by name."""
    def ins(sub: str) -> list[Path]:
        d = pkg / "data" / sub
        return sorted(d.glob("*.in")) if d.exists() else []
    return ins("sample"), ins("secret")


def answer_for(in_file: Path) -> Path | None:
    for ext in (".ans", ".out"):
        cand = in_file.with_suffix(ext)
        if cand.exists():
            return cand
    return None


# ----- validation -> checker -------------------------------------------------

def parse_validator_flags(meta: dict) -> list[str]:
    flags = meta.get("validator_flags", "")
    if isinstance(flags, list):
        return [str(x) for x in flags]
    return str(flags).split()


def find_validator_source(pkg: Path) -> Path | None:
    d = pkg / "output_validators"
    if not d.exists():
        return None
    srcs = [p for p in d.rglob("*") if p.suffix in (".cpp", ".cc", ".cxx", ".c")]
    if not srcs:
        return None
    withmain = [p for p in srcs if "main(" in p.read_text(errors="replace")]
    return (withmain or srcs)[0]


def resolve_checker(meta: dict, pkg: Path):
    """Return (checker_spec, checker_src_text_or_None, skip_reason_or_None)."""
    src = find_validator_source(pkg)
    is_custom = src is not None or str(meta.get("validation", "")).startswith("custom")
    if is_custom:
        if src is None:
            return None, None, "validation: custom but no validator source found"
        text = src.read_text(errors="replace")
        if "testlib.h" in text:
            return {"type": "program", "source": "checker.cpp"}, text, None
        return None, None, (f"custom validator {src.name} uses the ICPC exit-42/43 "
                            "interface (not the testlib contract) — not yet supported")
    # default validation
    flags = parse_validator_flags(meta)
    tol = _float_tol(flags)
    if tol is not None:
        return {"type": "float", "tol": tol}, None, None
    if "space_change_sensitive" in flags:
        return "diff", None, None
    return "tokens", None, None


def _float_tol(flags: list[str]) -> float | None:
    keys = {"float_tolerance", "float_absolute_tolerance", "float_relative_tolerance"}
    best = None
    for i, tok in enumerate(flags):
        if tok in keys and i + 1 < len(flags):
            try:
                best = max(best or 0.0, float(flags[i + 1]))
            except ValueError:
                pass
    return best


# ----- write + seed ----------------------------------------------------------

def regen_seed():
    """Rebuild db/seed/problems.sql from every imported problem (manifest
    has a 'source'). Idempotent: a fresh DB seeds the whole imported set."""
    rows = []
    for mf in sorted(TC_ROOT.glob("*/manifest.json")):
        m = json.loads(mf.read_text())
        if "source" not in m:
            continue                                 # hand-authored, not an imported package
        d = mf.parent
        stmt = (d / "statement.html").read_text() if (d / "statement.html").exists() \
            else f"<h1>{m.get('title', m['slug'])}</h1>\n"
        rows.append(
            f"  ('{m['slug']}', '{_sql(m.get('title', m['slug']))}', "
            f"$imported${stmt}$imported$, {m.get('band', 2)}, "
            f"{m.get('time_limit_ms', DEFAULT_TIME_MS)}, "
            f"{m.get('memory_limit_kb', DEFAULT_MEM_KB)}, "
            f"{m.get('rating', 1500)})"
        )
    out = SEED_FILE
    if not rows:
        out.write_text("-- No imported problems yet.\n")
        return
    body = (
        "-- Generated by tools/import_problem.py — imported problem packages.\n"
        "-- Idempotent: re-running is safe (slug is UNIQUE).\n"
        "INSERT INTO problems (slug, title, statement_md, band, time_limit_ms,"
        " memory_limit_kb, rating) VALUES\n"
        + ",\n".join(rows) + "\nON CONFLICT (slug) DO NOTHING;\n"
    )
    out.write_text(body)


def _sql(s: str) -> str:
    return s.replace("'", "''")


def main():
    ap = argparse.ArgumentParser(description="Import an ICPC/Kattis problem package")
    ap.add_argument("package", type=Path)
    ap.add_argument("--slug", help="override the slug (default: package dir name)")
    ap.add_argument("--band", type=int, default=2, help="difficulty band 1-3")
    ap.add_argument("--rating", type=int,
                    help="initial problem rating (default: seeded from band)")
    ap.add_argument("--force", action="store_true",
                    help="import despite a non-clean license / overwrite a "
                         "hand-authored slug")
    ap.add_argument("--into", type=Path, help="testcases root (default: ./testcases)")
    ap.add_argument("--seed", type=Path,
                    help="seed SQL file (default: db/seed/problems.sql)")
    args = ap.parse_args()

    global TC_ROOT, SEED_FILE
    if args.into:
        TC_ROOT = args.into.resolve()
    if args.seed:
        SEED_FILE = args.seed.resolve()

    pkg = args.package.resolve()
    meta = load_meta(pkg)
    slug = args.slug or slugify(pkg.name)
    title = pick_title(meta, slug)

    verdict, lic = license_status(meta)
    if verdict == "reject" and not args.force:
        sys.exit(f"REJECTED {slug}: license {lic!r} is not clean "
                 f"(use --force only if you own the decision)")
    if verdict == "warn":
        print(f"  ! {slug}: license {lic!r} is share-alike — obligations apply")

    # Don't clobber a hand-authored problem of the same slug.
    existing = TC_ROOT / slug / "manifest.json"
    if existing.exists():
        if "source" not in json.loads(existing.read_text()) and not args.force:
            sys.exit(f"REFUSING: testcases/{slug} exists and is not an imported "
                     f"problem; pass --force to overwrite")

    checker, checker_src, skip = resolve_checker(meta, pkg)
    if skip:
        sys.exit(f"SKIPPED {slug}: {skip}")

    sample, secret = collect_cases(pkg)
    cases = []
    for in_f in sample + secret:
        ans = answer_for(in_f)
        if ans is None:
            print(f"  ! {in_f.name}: no .ans/.out, skipping this case")
            continue
        cases.append((in_f, ans))
    if not cases:
        sys.exit(f"REJECTED {slug}: no usable test cases under data/")
    n_sample = sum(1 for in_f in sample if answer_for(in_f))

    time_ms, mem_kb = pick_limits(meta, pkg)
    statement = collect_statement(pkg, title) + samples_html(sample)

    d = TC_ROOT / slug
    d.mkdir(parents=True, exist_ok=True)
    for old in list(d.glob("*.in")) + list(d.glob("*.out")):
        old.unlink()
    names = []
    for i, (in_f, ans_f) in enumerate(cases, 1):
        n = f"{i:02d}"
        names.append(n)
        (d / f"{n}.in").write_bytes(in_f.read_bytes())
        (d / f"{n}.out").write_bytes(ans_f.read_bytes())
    # Samples are tier1 (cheap early-exit); if none, promote the first 2 secret.
    cut = n_sample if n_sample else min(2, len(names))
    rating = args.rating if args.rating is not None else BAND_RATING.get(args.band, 1500)
    manifest = {
        "slug": slug, "tier1": names[:cut], "tier2": names[cut:],
        "checker": checker,
        "title": title, "band": args.band, "rating": rating,
        "time_limit_ms": time_ms, "memory_limit_kb": mem_kb,
        "source": str(meta.get("source", pkg.name)),
        "license": lic, "uuid": str(meta.get("uuid", "")),
    }
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (d / "statement.html").write_text(statement)
    (d / "statement.md").unlink(missing_ok=True)   # drop the old markdown form
    if checker_src is not None:
        (d / "checker.cpp").write_text(checker_src)

    regen_seed()
    ck = checker if isinstance(checker, str) else checker["type"]
    print(f"imported {slug}: {len(names)} tests ({cut} sample/tier1), checker={ck}, "
          f"rating={rating}, license={lic}, {time_ms}ms/{mem_kb}kb")
    print("  wrote testcases/%s/ and regenerated db/seed/problems.sql" % slug)


if __name__ == "__main__":
    main()
