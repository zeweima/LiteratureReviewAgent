"""Central configuration for the agentic LaTeX-synthesis engine.

Fully self-contained: shares no code with the old `synth_latex_common.py` /
`14*.py`. Paths resolve relative to THIS file so the package works identically
whether run from the worktree copy or the real checkout (once copied to
`code/analysis/scripts/synth_agentic/`).
"""
from __future__ import annotations

from pathlib import Path

# ---- layout -------------------------------------------------------------- #
# .../code/analysis/scripts/synth_agentic/config.py -> parents[2] == code/analysis
BASE = Path(__file__).resolve().parents[2]
TABLES = BASE / "tables"
LATEX = BASE / "latex"
AGENTIC = LATEX / "agentic"                 # all inputs + run outputs live here
BIB = LATEX / "references.bib"              # shared bibliography

# ---- track -> (cluster-name, [long-fields]) ------------------------------ #
TRACKS = {
    "frontier":  ("future_directions", ["future_directions"]),
    "knowledge": ("summary_bullets-highlights", ["summary_bullets", "highlights"]),
}

# ---- track focus (the lens each review COMPONENT is written under) -------- #
# Threaded via --track into every step (fine, macro, overall) so the concentration
# stays consistent as macro condenses fine and the overall architect structures the
# paper. Told to the generator/orchestrator/architect; the reviewer gets it only as
# a one-line orientation (its faithfulness + citation checks are unchanged).
TRACK_FOCUS = {
    "frontier": (
        "This is the RESEARCH FRONTIER component of the review: future directions, "
        "open questions, and research gaps in agricultural subsurface (tile) drainage. "
        "Frame the synthesis around what remains uncertain or unstudied and where the "
        "field should go next -- priorities, hypotheses to test, methods and data still "
        "needed -- rather than around settled results."
    ),
    "knowledge": (
        "This is the STATE-OF-KNOWLEDGE component of the review: what the evidence has "
        "established about agricultural subsurface (tile) drainage. Frame the synthesis "
        "around consolidated findings, mechanisms, and quantified outcomes that are well "
        "supported by the sources -- what is known and how strongly -- rather than around "
        "open questions."
    ),
}

# ---- steps (tiers) ------------------------------------------------------- #
# mode drives which generator/reviewer prompt is used: "fine" = maximal coverage,
# no cap; "synth" = integrate + condense the section(s) below under a soft cap.
# flat = independent units run in parallel (fine, macro); overall is staged.
STEPS = {
    "fine":    {"mode": "fine",  "source": None,    "flat": True,  "cap_hi": None},
    "macro":   {"mode": "synth", "source": "fine",  "flat": True,  "cap_hi": 8000},
    "overall": {"mode": "synth", "source": "macro", "flat": False, "cap_hi": None},
}
MACRO_SOFT_CAP = 8000      # words (soft, prompt-only); macro chapter target ceiling
OVERALL_BUDGET = 20000     # words total for the assembled paper; the architect splits
                           # this into a per-section (word_lo, word_hi) range.
HARD_OVERFLOW = 1.1        # HARD word cap = soft cap x this. The generator is forced
                           # (deterministic condense loop) to stay under it -- e.g. a
                           # soft cap of 5000 becomes a hard ceiling of 5500.

# long-field CSV + column for each field key
FIELDS = {
    "summary_bullets":   ("long_summary_bullets.csv", "summary_bullets"),
    "highlights":        ("long_highlights.csv", "highlights"),
    "future_directions": ("long_future_directions.csv", "future_directions"),
}

# ---- toolchain ----------------------------------------------------------- #
# used only if `latexmk` is not on PATH
MIKTEX_BIN = Path.home() / "AppData" / "Local" / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64"

# ---- models (aliases accepted by the claude CLI) ------------------------- #
ORCH_MODEL = "sonnet"     # cheap judgment loop
GEN_MODEL = "opus"        # strongest writer
REVIEW_MODEL = "opus"     # faithfulness / reference check
ARCHITECT_MODEL = "opus"    # overall (c) outline planner -- structural design needs the strong model

# ---- concurrency + loop bounds ------------------------------------------- #
CONCURRENCY = 3           # orchestrators (units) in flight
SUBAGENT_CONCURRENCY = 6  # generator/reviewer sub-agents in flight (shared)
MAX_ATTEMPTS = 4          # orchestrator generate/review rounds
GEN_MAX_FIX = 10          # generator internal compile-fix loop
GEN_MAX_CONDENSE = 3      # generator internal condense passes to meet the hard word cap

# ---- timeouts (seconds) -------------------------------------------------- #
GEN_TIMEOUT = 1800
REVIEW_TIMEOUT = 3600     # opus reviewer on large macro/overall sources is slow
ORCH_TIMEOUT = 14400      # headroom for multiple slow generate->opus-review rounds
COMPILE_TIMEOUT = 600
ARCHITECT_TIMEOUT = 900   # overall (c) outline planner reads all macro chapters

# ---- sub-agent turn budgets ---------------------------------------------- #
GEN_MAX_TURNS = 30
REVIEW_MAX_TURNS = 30     # opus reviewer paginates large macro/overall sources
ORCH_MAX_TURNS = 40

# ---- transient-API-error resilience -------------------------------------- #
ORCH_RETRIES = 3            # restart the orchestrator on a transient API error
ORCH_RETRY_BACKOFF = 5      # seconds * attempt number
