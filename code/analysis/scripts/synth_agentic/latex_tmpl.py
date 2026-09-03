"""LaTeX template + small text helpers (fresh, self-contained).

The preamble is code-owned and matches the shared `references.bib` (biblatex +
biber, authoryear). `wrap_section_doc` builds a standalone document around a
section body -- used both as the compile harness and as the final deliverable.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# text escaping / slugs
# --------------------------------------------------------------------------- #
_ESC_RE = re.compile(r"([&%$#_{}~^\\])")
_ESC_MAP = {"~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "\\": r"\textbackslash{}"}


def esc(s) -> str:
    """Escape LaTeX specials in plain text (titles, headers)."""
    return _ESC_RE.sub(lambda m: _ESC_MAP.get(m.group(1), "\\" + m.group(1)), str(s))


def slug(theme: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(theme).casefold()).strip("-")
    return s[:48] or "theme"


_SUBSEC_RE = re.compile(r"\\subsection\*?\{([^}]*)\}")


def subsection_titles(body: str) -> list:
    """Titles of the \\subsection{...} blocks in a macro/section body, in order."""
    return [t.strip() for t in _SUBSEC_RE.findall(body or "") if t.strip()]


# --------------------------------------------------------------------------- #
# citation-key handling
# --------------------------------------------------------------------------- #
CITE_RE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")
_CITE_FULL = re.compile(r"(\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*)\{([^}]*)\}")

CITE_RULES = (
    "Cite with biblatex commands only: \\parencite{Key2020} for a parenthetical "
    "citation, \\textcite{Key2020} when the authors are the grammatical subject; "
    "combine multiple keys in ONE command, e.g. \\parencite{KeyA2019,KeyB2020}. "
    "Use ONLY the allowed citation anchors, copied verbatim (case and any trailing "
    "suffix letter matter). Never write a bare (Author, 2020) citation and never "
    "invent a key."
)


def cited_keys(text: str, allowed: set):
    """Return (valid, invalid) distinct cite keys found in `text`."""
    seen, valid, bad = set(), [], []
    for grp in CITE_RE.findall(text):
        for raw in grp.split(","):
            k = raw.strip()
            if not k or k in seen:
                continue
            seen.add(k)
            (valid if k in allowed else bad).append(k)
    return valid, bad


def sanitize_cites(text: str, allowed: set):
    """Drop any cite key not in `allowed`; empty a citation that loses all keys.

    A generator-side cleanup (NOT a content review): guarantees every key that
    survives resolves against the theme's anchors so compilation stays clean.
    """
    removed = []

    def repl(m):
        head, keys = m.group(1), m.group(2)
        kept, seen = [], set()
        for raw in keys.split(","):
            k = raw.strip()
            if not k:
                continue
            if k in allowed:
                if k not in seen:
                    seen.add(k)
                    kept.append(k)
            else:
                removed.append(k)
        return f"{head}{{{','.join(kept)}}}" if kept else ""

    return _CITE_FULL.sub(repl, text), removed


# --------------------------------------------------------------------------- #
# document wrappers
# --------------------------------------------------------------------------- #
def _bibname(bib_path: str) -> str:
    """Bare filename of the bibliography (e.g. 'references.bib').

    biber cannot reliably open an absolute Windows path given to
    \\addbibresource, so we reference the bib by BARE FILENAME and let biber's
    kpsewhich find it via BIBINPUTS (set by texcompile) or a co-located copy.
    """
    return str(bib_path).replace("\\", "/").split("/")[-1]


def preamble(bib_path: str) -> str:
    return (
        "\\documentclass[11pt]{article}\n"
        "\\usepackage[utf8]{inputenc}\n"
        "\\usepackage[T1]{fontenc}\n"
        "\\usepackage{lmodern}\n"
        "\\usepackage[margin=1in]{geometry}\n"
        "\\usepackage{csquotes}\n"
        "\\usepackage[style=authoryear,backend=biber,sorting=nyt,maxbibnames=99,"
        "giveninits=true,uniquename=init]{biblatex}\n"
        "\\usepackage[hidelinks]{hyperref}\n"
        f"\\addbibresource{{{_bibname(bib_path)}}}\n"
    )


def wrap_section_doc(title: str, body: str, bib_path: str) -> str:
    """Standalone document around a section body (its own \\section*{title})."""
    return (f"{preamble(bib_path)}\\begin{{document}}\n"
            f"\\section*{{{esc(title)}}}\n\n{body.strip()}\n\n"
            "\\printbibliography\n\\end{document}\n")


# --------------------------------------------------------------------------- #
# body cleanup (strip stray preamble / fences / chatter the generator may add)
# --------------------------------------------------------------------------- #
_FENCE = re.compile(r"^\s*```")
_STRIP_LINE = re.compile(
    r"^\s*\\(documentclass|usepackage|begin\{document\}|end\{document\}|"
    r"addbibresource|printbibliography|maketitle|title|author|date)\b")


def clean_body(text: str) -> str:
    """Keep only the inner body: drop code fences and any stray preamble lines."""
    out = [ln for ln in text.splitlines()
           if not _FENCE.match(ln) and not _STRIP_LINE.match(ln)]
    return "\n".join(out).strip()
