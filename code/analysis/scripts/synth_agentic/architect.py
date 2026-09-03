"""Overall (step c) ARCHITECT -- plan the review paper's section outline.

An SDK sub-agent reads the macro themes (a digest file) under the track's focus and
returns a JSON outline: an ordered list of sections, each with a kind, a title, the
macro source_ids it draws on (theme sections only), and a soft word RANGE (word_lo,
word_hi). The ranges are normalized to sum near the total budget. If the agent fails
or returns invalid JSON, `default_outline` gives a deterministic fallback.

The paper's structure is OPEN-ENDED except for a fixed skeleton: it always has an
'abstract', an 'intro', and 'conclusions'. Between intro and conclusions the
architect designs the body freely -- one or more 'theme' sections (which read macro
material) plus any synthesis sections it judges useful (any kind label). There is no
predefined gaps/cross section; a dedicated research-gaps section is intentionally
omitted because the review splits knowledge vs. research-frontier into two separate
component papers, so gaps live in the frontier component.

Each section maps to a dependency STAGE (see `section_stage`) that drives
`run_overall.py`: stage 1 = 'theme' sections read raw macro material; stage 2 =
intro/conclusions/synthesis sections read the written theme bodies; stage 3 =
abstract reads intro + conclusions. Citation and subsection policy per section come
from `section_allow_cite` / `section_use_subsections`.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

import config
from latex_tmpl import slug, subsection_titles
from sdk_agents import call_agent


ARCHITECT_SYSTEM = (
    "You are the ARCHITECT of an academic review paper on agricultural subsurface "
    "(tile) drainage. The macro themes given to you are raw INPUT MATERIAL, not a "
    "template. READ their actual text and DESIGN a coherent paper structure over that "
    "material -- reorganise, merge, or split it as a good review paper would. Do NOT "
    "mirror one macro theme onto one section. Your goal is a COHERENT structure with "
    "LITTLE REPETITION: every idea should have exactly one home. You do NOT write the "
    "prose of the sections; you return an outline as STRICT JSON only.\n"
    "Each macro theme's body is internally organised into '\\subsection{...}' blocks "
    "(their titles are listed for you). Plan at that granularity: a paper section may "
    "draw on specific subsections of a macro theme, and different subsections of the "
    "SAME macro theme may go to DIFFERENT paper sections. Routing subsections is your "
    "main tool for avoiding repetition when a macro theme informs more than one "
    "section.\n"
    "The paper ALWAYS has three fixed sections -- an 'abstract', an 'intro', and a "
    "'conclusions'; include all three (they will be written as flowing prose). Between "
    "the intro and the conclusions, design the body FREELY: one or more 'theme' "
    "sections, plus any additional synthesis sections you judge the paper needs (give "
    "those any 'kind' label and title, e.g. 'cross' for a cross-cutting synthesis). "
    "Only 'theme' sections read the macro material -- list every macro theme id a theme "
    "section draws on in its 'source_ids' (one or several). The abstract, intro, "
    "conclusions, and any synthesis section take NO source_ids; they are written later "
    "from the theme sections. Add a synthesis section ONLY if it owns content no other "
    "section does; if it would repeat the theme or conclusions sections, omit it.\n"
    "Do NOT add a dedicated research-gaps or future-directions section. The review "
    "treats state-of-knowledge and research-frontier as two SEPARATE component papers, "
    "so open questions belong to the frontier component -- a gaps section here would be "
    "redundant (the task message tells you which component you are planning).\n"
    "Order the sections as they should READ (abstract, intro, theme/synthesis sections, "
    "conclusions). Distribute the word budget as a soft RANGE per section (word_lo, "
    "word_hi) whose midpoints sum to roughly the total budget.\n"
    "For EACH section write, grounded in the macro text you read:\n"
    "  - 'summary': a brief (one-line) description of what the section is about;\n"
    "  - 'guidance': section-level direction to its writer -- the emphasis to take and "
    "what to LEAVE to which other section so the paper does not repeat itself;\n"
    "  - 'subsections': for THEME and SYNTHESIS sections, DESIGN the section's internal "
    "breakdown as an ordered list of subsections, each with a 'title' and a one-line "
    "'guidance' naming which macro subsections / material feed it and what it covers. "
    "This is where you route the macro subsections into a concrete structure. The "
    "abstract, intro, and conclusions are flowing prose -- give them an empty "
    "subsections list.\n"
    "Write PLAIN TEXT in every JSON string value: refer to macro subsections by their "
    "wording only, and do NOT put LaTeX commands or backslash characters anywhere in "
    "the JSON (they make it invalid).\n"
    "Return STRICT JSON only:\n"
    '{"sections": [{"kind": "...", "title": "...", "summary": "one-line intent", '
    '"guidance": "section-level direction", "subsections": [{"title": "...", '
    '"guidance": "what it covers / which macro material"}], '
    '"source_ids": [<macro ids, theme sections only>], "word_lo": <int>, "word_hi": <int>}]}'
)

# The paper's fixed skeleton -- always present, written as flowing prose.
SKELETON = ("abstract", "intro", "conclusions")
_SKELETON_DEFAULTS = {
    "abstract": {"title": "Abstract", "word_lo": 200, "word_hi": 300,
                 "summary": "self-contained overview of scope, themes, and takeaways"},
    "intro": {"title": "Introduction", "word_lo": 1000, "word_hi": 1400,
              "summary": "motivation, background, scope, and organisation"},
    "conclusions": {"title": "Conclusions", "word_lo": 500, "word_hi": 700,
                    "summary": "overall takeaways and closing synthesis"},
}


def section_stage(kind: str) -> int:
    """Dependency tier a section runs in (drives run_overall staging):
    1 = reads raw macro material (theme sections);
    2 = reads the written theme bodies (intro, conclusions, any synthesis section);
    3 = reads intro + conclusions (abstract)."""
    k = (kind or "").lower()
    if k == "theme":
        return 1
    if k == "abstract":
        return 3
    return 2


def section_allow_cite(kind: str) -> bool:
    """Abstract and conclusions are citation-free; everything else may cite."""
    return (kind or "").lower() not in ("abstract", "conclusions")


def section_use_subsections(kind: str) -> bool:
    """The skeleton (abstract/intro/conclusions) is flat prose; theme and synthesis
    sections use \\subsection structure."""
    return (kind or "").lower() not in SKELETON


def _clean_subsections(raw, use_sub: bool) -> list:
    """Coerce the architect's per-section subsection plan into [{title, guidance}].
    Empty for skeleton (flat-prose) sections regardless of what the agent returned."""
    if not use_sub or not isinstance(raw, list):
        return []
    out = []
    for it in raw:
        if isinstance(it, dict):
            title = str(it.get("title") or it.get("name") or "").strip()
            g = str(it.get("guidance") or it.get("summary") or it.get("points") or "").strip()
        else:
            title, g = str(it).strip(), ""
        if title:
            out.append({"title": title, "guidance": g})
    return out


def _first_json_object(text: str):
    """The first complete top-level {...} object, tracking strings so LaTeX braces
    inside JSON string values (e.g. \\subsection{X}) and any trailing prose don't
    confuse the boundary."""
    start = text.find("{")
    if start < 0:
        return None
    depth = in_str = esc = 0
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = 0
            elif c == "\\":
                esc = 1
            elif c == '"':
                in_str = 0
        elif c == '"':
            in_str = 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _sanitize_json(s: str) -> str:
    """Repair the two things the architect most often gets wrong in its JSON:
    unescaped backslashes (it writes literal LaTeX like \\subsection{X} in strings,
    an invalid JSON escape) and trailing commas."""
    s = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)   # escape stray backslashes
    s = re.sub(r',(\s*[}\]])', r'\1', s)             # drop trailing commas
    return s


def _extract_json(text: str):
    if not text:
        return None
    candidates = []
    bal = _first_json_object(text)
    if bal:
        candidates.append(bal)
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        candidates.append(m.group(0))
    for raw in candidates:
        for cand in (raw, _sanitize_json(raw)):
            try:
                return json.loads(cand)
            except Exception:
                continue
    return None


def _assign_ids(sections: list) -> list:
    for i, s in enumerate(sections):
        base = slug(s.get("title") or s["kind"]) or s["kind"]
        s["id"] = f"{i:02d}_{base}"
        s.setdefault("deps", _deps_for(s["kind"]))
    return sections


def _deps_for(kind: str) -> list:
    if kind == "theme":
        return []
    if kind == "abstract":
        return ["intro", "conclusions"]
    return ["theme"]   # intro / cross / gaps / conclusions read the theme sections


def _normalize(sections: list, macro_units: dict, max_words: int) -> list:
    """Coerce fields, keep arbitrary (open-ended) kinds, guarantee the skeleton, and
    scale midpoints to ~max_words."""
    clean, seen_skeleton = [], set()
    for s in sections:
        kind = str(s.get("kind", "")).lower().strip()
        if not kind:
            kind = "theme" if s.get("source_ids") else "section"
        # keep the skeleton unique (first occurrence wins)
        if kind in SKELETON:
            if kind in seen_skeleton:
                continue
            seen_skeleton.add(kind)
        lo = s.get("word_lo") or s.get("words")
        hi = s.get("word_hi") or s.get("words")
        try:
            lo = int(lo) if lo else None
            hi = int(hi) if hi else None
        except (TypeError, ValueError):
            lo = hi = None
        if lo and hi and lo > hi:
            lo, hi = hi, lo
        if not hi:
            hi = lo or 1200
        if not lo:
            lo = max(150, int(hi * 0.8))
        src = [int(k) for k in (s.get("source_ids") or []) if str(k) in macro_units]
        clean.append({"kind": kind, "title": str(s.get("title") or kind.title()),
                      "summary": str(s.get("summary", "") or ""),
                      "guidance": str(s.get("guidance", "") or ""),
                      "subsections": _clean_subsections(s.get("subsections"),
                                                        section_use_subsections(kind)),
                      "source_ids": src, "word_lo": lo, "word_hi": hi})

    # guarantee the fixed skeleton (abstract / intro / conclusions) exists
    for k in SKELETON:
        if k not in seen_skeleton:
            d = _SKELETON_DEFAULTS[k]
            clean.append({"kind": k, "title": d["title"], "summary": d["summary"],
                          "guidance": "", "subsections": [], "source_ids": [],
                          "word_lo": d["word_lo"], "word_hi": d["word_hi"]})

    # NOTE: the architect decides body coverage -- macro themes may be merged into one
    # section, split, or dropped by design; we do NOT force-add uncovered macros.

    # scale midpoints down if the plan overshoots the budget (never pad up)
    mids = sum((s["word_lo"] + s["word_hi"]) / 2 for s in clean) or 1
    if mids > max_words * 1.05:
        f = max_words / mids
        for s in clean:
            s["word_lo"] = max(120, int(s["word_lo"] * f))
            s["word_hi"] = max(s["word_lo"] + 100, int(s["word_hi"] * f))

    # reading order: abstract, intro, themes, other synthesis sections, conclusions.
    # stable sort preserves the architect's order WITHIN the theme/synthesis groups.
    def _order_key(s):
        k = s["kind"]
        return {"abstract": 0, "intro": 1, "theme": 2}.get(k, 4 if k == "conclusions" else 3)

    clean.sort(key=_order_key)
    return _assign_ids(clean)


def default_outline(macro_units: dict, max_words: int, title: str) -> dict:
    """Deterministic fallback: fixed skeleton + one theme per macro theme (no gaps /
    cross -- those are the architect's call, absent here)."""
    n = max(1, len(macro_units))
    theme_b = max(800, min(2500, (max_words - 3000) // n))   # ~3000 reserved for skeleton
    a, i, c = _SKELETON_DEFAULTS["abstract"], _SKELETON_DEFAULTS["intro"], _SKELETON_DEFAULTS["conclusions"]
    secs = [{"kind": "abstract", "title": a["title"], "source_ids": [],
             "word_lo": a["word_lo"], "word_hi": a["word_hi"], "summary": a["summary"]},
            {"kind": "intro", "title": i["title"], "source_ids": [],
             "word_lo": i["word_lo"], "word_hi": i["word_hi"], "summary": i["summary"]}]
    for m in sorted(macro_units, key=lambda k: int(k)):
        e = macro_units[m]
        secs.append({"kind": "theme", "title": str(e.get("theme", f"Theme {m}")),
                     "summary": e.get("description", ""), "source_ids": [int(m)],
                     "word_lo": int(theme_b * 0.8), "word_hi": int(theme_b * 1.2)})
    secs.append({"kind": "conclusions", "title": c["title"], "source_ids": [],
                 "word_lo": c["word_lo"], "word_hi": c["word_hi"], "summary": c["summary"]})
    return {"title": title, "budget": max_words,
            "sections": _normalize(secs, macro_units, max_words)}


async def architect_outline(*, track, title, max_words, macro_units, digest_path,
                            focus, model=None, sem=None):
    def _macro_line(m):
        subs = subsection_titles(macro_units[m].get("body") or "")
        head = f"  - macro {m}: {macro_units[m].get('theme','')}"
        if subs:
            head += "\n      subsections: " + "; ".join(subs)
        return head

    macro_list = "\n".join(_macro_line(m)
                           for m in sorted(macro_units, key=lambda k: int(k)))
    component = "research-frontier" if track == "frontier" else "state-of-knowledge"
    instr = (
        (focus.strip() + "\n\n" if focus else "") +
        f"You are planning the {component.upper()} component paper of the review.\n\n"
        f"Design the outline for the review paper titled:\n  {title}\n\n"
        f"Total word budget: ~{max_words} words.\n\n"
        f"The macro themes available to draw on:\n{macro_list}\n\n"
        f"Read the full macro themes here before planning:\n  {digest_path}\n\n"
        "Return the outline as STRICT JSON only (schema in your instructions)."
    )
    sem = sem or asyncio.Semaphore(1)          # created inside the running loop
    digest_dir = os.path.dirname(digest_path)
    out = await call_agent(
        instr, ARCHITECT_SYSTEM, allowed_tools=["Read"], cwd=digest_dir,
        timeout=config.ARCHITECT_TIMEOUT, sem=sem, model=model or config.ARCHITECT_MODEL,
        max_turns=50, label="architect",
        token_log=os.path.join(digest_dir, "tokens.jsonl"))
    parsed = _extract_json(out)
    if not parsed or not isinstance(parsed.get("sections"), list) or not parsed["sections"]:
        raw_fp = os.path.join(digest_dir, "_architect_raw.txt")
        try:
            with open(raw_fp, "w", encoding="utf-8") as f:
                f.write(out or "")
        except Exception:
            pass
        print(f"  [architect] no valid outline returned -> using default_outline "
              f"(raw output saved to {raw_fp})")
        return default_outline(macro_units, max_words, title)
    sections = _normalize(parsed["sections"], macro_units, max_words)
    if not any(s["kind"] == "theme" for s in sections):
        print("  [architect] outline had no theme sections -> using default_outline")
        return default_outline(macro_units, max_words, title)
    return {"title": title, "budget": max_words, "sections": sections}
