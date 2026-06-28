r"""LaTeX / Markdown -> HTML for imported problem statements, via pandoc.

pandoc renders the statement to self-contained HTML once, at import time — no
custom parser, and nothing for the frontend to re-parse:
  - figures, tables and cross-refs ("See Figure 1") become real HTML;
  - math is left as raw TeX inside `<span class="math inline">` /
    `<span class="math display">` (pandoc's `--katex` mode) for the frontend's
    KaTeX pass — the class says exactly what's math, so there's no `$`-vs-currency
    guessing;
  - referenced images are inlined as base64 `data:` URIs (see `_inline_images`)
    so a statement carries everything it needs and the DB stays the only store.
LaTeX section levels are shifted down one so `\section` lands at `<h2>`, leaving
`<h1>` for the title the caller prepends.

pandoc must be on PATH (or set $PANDOC). It is needed only at import time, never
at runtime. Install: `dnf install pandoc` / `apt install pandoc` / `brew install
pandoc`.
"""
import base64
import mimetypes
import os
import re
import shutil
import subprocess
from pathlib import Path

# Shims for the kattis/problemtools `problem.cls` macros pandoc can't see (it
# doesn't load the document class). Without these an unknown `\command{arg}`
# leaks its arguments into the prose. `\providecommand` so a package that defines
# its own version wins. Extend this if a package uses another custom command —
# the one maintenance point left. (LaTeX input only.)
_PREAMBLE = (
    r"\providecommand{\problemname}[1]{}"
    "\n"
    r"\providecommand{\illustration}[3]{\begin{figure}"
    r"\includegraphics[width=#1\textwidth]{#2}\caption{#3}\end{figure}}"
    "\n"
)


class PandocMissing(RuntimeError):
    """pandoc is required to convert statements but wasn't found."""


def _pandoc() -> str:
    exe = os.environ.get("PANDOC") or shutil.which("pandoc")
    if not exe:
        raise PandocMissing(
            "pandoc not found — it's required to convert problem statements "
            "(only at import time, never at runtime). Install it:\n"
            "  Fedora:  sudo dnf install pandoc\n"
            "  Debian:  sudo apt install pandoc\n"
            "  macOS:   brew install pandoc\n"
            "or point $PANDOC at the binary."
        )
    return exe


_HTML_IMG = re.compile(r"(<img\b[^>]*?\bsrc=)([\"'])(?P<path>.*?)\2", re.I)
# Web-renderable raster/vector formats we can inline into an <img>. (PDF figures,
# which LaTeX also allows, can't be shown in an <img>, so we don't inline them.)
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


def _resolve_image(base_dir: Path, path: str) -> Path | None:
    """Find the file a `<img src>` points at. Handles pandoc keeping a subdir
    prefix and — crucially — extension-less `\\includegraphics{fig}` (very common
    in LaTeX), where the source is really `fig.png` / `fig.jpg` / …"""
    for cand in (base_dir / path, base_dir / Path(path).name):
        if cand.exists():
            return cand
        if not cand.suffix:                      # `\includegraphics{fig}` -> fig.png
            for ext in _IMG_EXTS:
                if cand.with_suffix(ext).exists():
                    return cand.with_suffix(ext)
    return None


def _inline_images(html: str, base_dir: Path) -> str:
    """Rewrite local `<img src=...>` paths to self-contained base64 `data:` URIs.
    External (`http://`, `data:`) sources are left alone; a path that doesn't
    resolve under `base_dir` is left untouched (a visibly missing image beats a
    silent corruption)."""
    def repl(m: re.Match) -> str:
        path = m.group("path")
        if "://" in path or path.startswith("data:"):
            return m.group(0)
        f = _resolve_image(base_dir, path)
        if f is None:
            return m.group(0)
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        uri = f"data:{mime};base64," + base64.b64encode(f.read_bytes()).decode()
        return f"{m.group(1)}{m.group(2)}{uri}{m.group(2)}"

    return _HTML_IMG.sub(repl, html)


def to_html(text: str, *, from_fmt: str = "latex", base_dir: Path | str | None = None) -> str:
    """Convert a `latex` or `markdown` statement to HTML. If `base_dir` is given,
    images referenced relative to it are inlined as base64 `data:` URIs."""
    args = [_pandoc(), "-f", from_fmt, "-t", "html", "--katex"]
    if from_fmt == "latex":                      # \section -> <h2>, leaving <h1> for the title
        args.append("--shift-heading-level-by=1")
        text = _PREAMBLE + text
    proc = subprocess.run(args, input=text, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed:\n{proc.stderr.strip()}")
    html = proc.stdout
    if base_dir is not None:
        html = _inline_images(html, Path(base_dir))
    return html.strip() + "\n"
