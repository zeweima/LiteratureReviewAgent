"""Deterministic LaTeX compilation (pure Python, no agent, no pipe stall).

Used by the generator tool (as its ground-truth compile) and by `report` for the
final PDF. Quiet `latexmk -silent`; output captured to a string and written to a
log file by the caller.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import config


def remove_stray_nul(directory) -> None:
    """Delete a stray Windows reserved-name `nul` file left by latexmk/CLI.

    A file literally named `nul` can only be removed via the `\\?\` extended-length
    path prefix, which bypasses reserved-device-name parsing. No-op elsewhere.
    """
    # abspath the DIRECTORY only (safe); appending "nul" first would make abspath
    # collapse it to the device path \\.\nul and drop the directory.
    dir_bs = os.path.abspath(str(directory)).replace("/", "\\")
    tgt = "\\\\?\\" + dir_bs + "\\nul"
    try:
        os.remove(tgt)
    except OSError:
        pass


def _extended(fn: str) -> str:
    """`\\?\` extended-length path for a possibly reserved-name file."""
    d, base = os.path.dirname(fn), os.path.basename(fn)
    return "\\\\?\\" + os.path.abspath(d).replace("/", "\\") + "\\" + base


def force_rmtree(path) -> None:
    """rmtree that survives a Windows reserved-name `nul` file inside `path`."""
    if not os.path.exists(str(path)):
        return
    remove_stray_nul(path)

    def _onerr(func, fn, exc):
        try:
            os.remove(_extended(fn))
        except Exception:
            pass

    shutil.rmtree(str(path), onerror=_onerr)

_ERR = re.compile(
    r"^(?:! |l\.\d|Runaway|Emergency|Fatal error|Undefined control sequence|"
    r"LaTeX Error|Missing |Extra |Package \w+ Error|! Undefined)", re.M)


def latexmk_bin() -> str:
    exe = shutil.which("latexmk")
    if exe:
        return exe
    cand = config.MIKTEX_BIN / "latexmk.exe"
    return str(cand) if cand.exists() else "latexmk"


def extract_errors(logtext: str, limit: int = 1500) -> str:
    lines = [ln for ln in logtext.splitlines() if _ERR.match(ln)]
    return "\n".join(lines[:40])[:limit]


def dollar_balance_issues(body: str, max_report: int = 5) -> list[str]:
    """Lines whose count of unescaped `$` is odd (the most common compile halt)."""
    out = []
    for i, ln in enumerate(body.splitlines(), 1):
        if len(re.findall(r"(?<!\\)\$", ln)) % 2:
            out.append(f"line {i}: unbalanced $  ->  {ln.strip()[:100]}")
            if len(out) >= max_report:
                break
    return out


def compile_doc(tex_path: Path, build_dir: Path, timeout: int | None = None):
    """Compile `tex_path` into `build_dir`. Returns (ok, error_excerpt, full_log)."""
    build_dir.mkdir(parents=True, exist_ok=True)
    cmd = [latexmk_bin(), "-pdf", "-silent", "-interaction=nonstopmode",
           "-halt-on-error", f"-outdir={build_dir}", tex_path.name]
    # biber cannot open an absolute \addbibresource path on Windows; point
    # BIBINPUTS at the latex dir so it resolves references.bib by basename.
    env = dict(os.environ)
    env["BIBINPUTS"] = str(config.LATEX) + os.pathsep + env.get("BIBINPUTS", "")
    rc = None
    try:
        r = subprocess.run(cmd, cwd=str(tex_path.parent), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env,
                           timeout=timeout or config.COMPILE_TIMEOUT)
        rc = r.returncode
        stdout = r.stdout or ""
    except FileNotFoundError:
        return None, "latexmk not found (install MiKTeX or add to PATH)", ""
    except subprocess.TimeoutExpired:
        return False, f"compile timed out after {timeout or config.COMPILE_TIMEOUT}s", ""

    log_fp = build_dir / (tex_path.stem + ".log")
    logtext = (log_fp.read_text(encoding="utf-8", errors="replace")
               if log_fp.exists() else stdout)
    pdf = build_dir / (tex_path.stem + ".pdf")
    wrote_pdf = bool(re.search(r"Output written on .*\(\d+ pages?", logtext))
    ok = pdf.exists() and (rc == 0 or wrote_pdf)

    # a PDF that lost its bibliography is NOT acceptable -- fail so the generator
    # fixes it rather than shipping a section with unresolved citations.
    bib_bad = re.findall(r"(Empty bibliography|Citation '[^']+'[^\n]*undefined)", logtext)
    if bib_bad:
        ok = False
        msg = "Bibliography/citation problem (biber did not resolve): " + \
              "; ".join(sorted(set(bib_bad))[:6])
        remove_stray_nul(tex_path.parent)
        return ok, (msg + "\n" + extract_errors(logtext)).strip(), logtext

    remove_stray_nul(tex_path.parent)   # clean the latexmk `nul` artifact
    return ok, extract_errors(logtext), logtext
