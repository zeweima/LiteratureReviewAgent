"""
Scalable normalization of free-text extraction fields (physical models, units,
durations, temporal resolution).

Design goals
------------
* **Deterministic & re-runnable** -- same input always gives the same output;
  every raw -> canonical mapping is auditable.
* **Human effort scales with unique strings, not papers.** Unique surface forms
  saturate (Heaps' law) while the paper count keeps growing, so the curated
  registry below stays small even at thousands of papers.

Four-stage pipeline (per field)
-------------------------------
1. ``canonicalize`` -- cheap deterministic cleanup (case, version tokens,
   suffix words, hyphens, parentheticals). No maintenance.
2. **Family registry** -- a small curated table of regex rules mapping a
   canonical string to a model *family*. This is the only hand-maintained asset.
3. **Fuzzy fallback** -- ``difflib`` match against known canonical names for the
   long tail (mirrors ``TileReviewCache/.../normalizers.py``; no extra deps).
4. **Review queue** -- anything still unmatched is returned frequency-sorted so a
   human reviews *new terms only* and appends them to the registry.

This module has no project-specific imports so it can be reused on the full corpus.
"""

from __future__ import annotations

import difflib
import re

import pandas as pd

# --------------------------------------------------------------------------- #
# Stage 1: deterministic canonicalization
# --------------------------------------------------------------------------- #

# Trailing descriptor words to strip (so "WaSim model" == "WaSim").
_SUFFIX_WORDS = r"(?:model|models|simulations?|module|package|approach|method|scheme|code|software|tool)"
# Version tokens: "v4.8", "ver. 4.0", "6.1", "(ver 3.29)", "version 2".
_VERSION = r"\b(?:v(?:er(?:sion)?)?\.?\s*)?\d+(?:\.\d+)*\b"


def canonicalize(text: str | None) -> str:
    """Deterministic string cleanup shared by all entity fields.

    Lowercases, removes parentheticals, version tokens, trailing descriptor
    words, and normalizes hyphens / apostrophes / whitespace.
    """
    if text is None:
        return ""
    s = str(text).lower()
    s = re.sub(r"\([^)]*\)", " ", s)          # drop parentheticals
    s = s.replace("'", "").replace("’", "")  # apostrophes
    s = re.sub(r"[-‐-―_/]", " ", s)  # hyphens/underscores/slashes -> space
    s = re.sub(_VERSION, " ", s)               # version numbers
    s = re.sub(rf"\b{_SUFFIX_WORDS}\b", " ", s)  # descriptor words
    s = re.sub(r"\s+", " ", s).strip()
    return s


# --------------------------------------------------------------------------- #
# Stage 2: model family registry  (the curated, slow-growing asset)
# --------------------------------------------------------------------------- #
# Each rule: (family, regex tested against the *canonicalized* string, kind).
#   kind = "model"    -> named process-based / software simulation models  <-- FOCUS
#   kind = "equation" -> classical analytical solutions / governing equations
# Order matters: first match wins. Patterns are intentionally loose to absorb
# spelling/spacing variants (e.g. "drain mod" after canonicalize == "drainmod").

MODEL = "model"
EQUATION = "equation"

FAMILY_RULES: list[tuple[str, str, str]] = [
    # --- Process-based / software models (the focus of the model landscape) ---
    ("DRAINMOD",  r"drain ?mod",                       MODEL),  # base, -N II, -DSSAT, -FOREST
    ("MODFLOW",   r"modflow|modpath|mt3d",             MODEL),  # MODFLOW + companions
    ("DSSAT",     r"dssat|ceres|cropgro",              MODEL),  # DSSAT and its crop modules
    ("HYDRUS",    r"hydrus",                           MODEL),
    ("SWAT",      r"\bswat\b|swat\d",                  MODEL),  # incl. SWAT2005 / SWAT2009
    ("RZWQM",     r"rzwqm",                            MODEL),
    ("APSIM",     r"apsim",                            MODEL),
    ("SWAP",      r"\bswap\b",                         MODEL),
    ("WaSim",     r"wasim",                            MODEL),
    ("SIDRA",     r"sidra",                            MODEL),
    ("ADAPT",     r"\badapt\b|agricultural drainage and pesticide", MODEL),
    ("CSUID",     r"csuid|colorado state university irrigation",     MODEL),
    ("EPIC",      r"\bepic\b|environmental policy integrated climate", MODEL),
    ("SALTMOD",   r"saltmod",                          MODEL),
    ("CATHY",     r"cathy",                            MODEL),
    ("GFLOW",     r"gflow",                            MODEL),
    ("METRIC",    r"\bmetric\b",                       MODEL),
    ("RETC",      r"\bretc\b",                         MODEL),
    ("Rosetta",   r"rosetta",                          MODEL),
    ("SWB-Sci",   r"swb sci",                          MODEL),
    # --- curated batch (full corpus): additional named process/software models ---
    ("SHAW",           r"\bshaw\b",                     MODEL),  # incl. RZ-SHAW coupling
    ("MIKE SHE",       r"mike ?she",                    MODEL),
    ("HydroGeoSphere", r"hydrogeosphere|\bhgs\b",       MODEL),
    ("SUTRA",          r"\bsutra\b",                    MODEL),
    ("SEEP/W",         r"seep ?w",                      MODEL),
    ("SLOPE/W",        r"slope ?w",                     MODEL),
    ("GLEAMS",         r"gleams",                       MODEL),
    ("WEPP",           r"\bwepp\b|water erosion prediction", MODEL),
    ("FLUSH",          r"\bflush\b",                    MODEL),
    ("COMSOL",         r"comsol",                       MODEL),
    ("Daisy",          r"\bdaisy\b",                    MODEL),
    ("SWMS",           r"swms",                         MODEL),  # SWMS-2D/3D (HYDRUS predecessor)
    ("CENTURY",        r"\bcentury\b",                  MODEL),
    ("RUSLE",          r"\brusle\b|revised universal soil loss", MODEL),
    ("RHESSys",        r"rhessys",                      MODEL),
    ("CLIGEN",         r"cligen",                       MODEL),
    ("WaTEM/SEDEM",    r"watem",                        MODEL),
    ("CHAIN-2D",       r"chain ?2d",                    MODEL),
    ("GMS",            r"groundwater modeling system|\bgms\b", MODEL),
    ("DHSVM",          r"dhsvm",                        MODEL),
    ("PHREEQC",        r"phreeqc",                      MODEL),
    ("SGMP",           r"\bsgmp\b",                     MODEL),
    ("MODHMS",         r"modhms",                       MODEL),
    ("SWIM",           r"swim ?3|\bswim\b",             MODEL),  # SWIM / SWIM3 variant
    ("DNDC",           r"\bdndc\b",                     MODEL),
    ("WaterGAP",       r"watergap|\bwghm\b",            MODEL),
    # --- Classical analytical solutions / governing equations (tracked, not focus) ---
    ("van Genuchten",   r"van genuchten",   EQUATION),
    ("Richards",        r"richards",        EQUATION),
    ("Hooghoudt",       r"hooghoudt",       EQUATION),
    ("Boussinesq",      r"boussinesq",      EQUATION),
    ("Kirkham",         r"kirkham",         EQUATION),
    ("Darcy",           r"darcy",           EQUATION),
    ("Manning",         r"manning",         EQUATION),
    ("Penman-Monteith", r"penman monteith", EQUATION),
    ("Green-Ampt",      r"green ?ampt",     EQUATION),
    ("Convection-Dispersion", r"convection dispersion|advection dispersion", EQUATION),
    ("Saint-Venant",    r"saint venant",    EQUATION),
    ("Dupuit",          r"dupuit",          EQUATION),
    ("Mobile-Immobile", r"mobile immobile", EQUATION),
    ("Glover-Dumm",     r"glover",          EQUATION),  # drain-spacing solution
    ("Van Beers",       r"van beers",       EQUATION),  # drain-spacing solution
    ("Van Schilfgaarde", r"schilfgaarde",   EQUATION),  # drain-spacing solution
]

# Optional explicit canonical names for high-value variants (drill-down label).
# Falls back to Title-cased canonical string when absent.
VARIANT_MAP: dict[str, str] = {
    "drainmod": "DRAINMOD",
    "drainmod n ii": "DRAINMOD-N",
    "drainmod nii": "DRAINMOD-N",
    "drainmod n": "DRAINMOD-N",
    "drainmod dssat": "DRAINMOD-DSSAT",
    "drainmod forest": "DRAINMOD-FOREST",
    "hydrus2d": "HYDRUS-2D",
    "hydrus 2d": "HYDRUS-2D",
    "swim3": "SWIM-3",
    "swim 3": "SWIM-3",
}

_COMPILED = [(fam, re.compile(pat), kind) for fam, pat, kind in FAMILY_RULES]


def _loose(text: str | None) -> str:
    """Like canonicalize but KEEPS parenthetical content (acronyms live there).

    ``canonicalize`` drops "(RZWQM)" / "(SWAT)" etc., which would hide the very
    acronym the family rules match on. This loose form turns brackets/separators
    into spaces without removing the tokens inside them.
    """
    if text is None:
        return ""
    s = str(text).lower()
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[-‐-―_/(),]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def assign_family(raw: str | None) -> dict:
    """Map one raw model string -> {raw, canonical, family, kind, match_method}.

    ``kind`` is ``"model"`` for named process/software models (the focus),
    ``"equation"`` for classical analytical solutions, or ``None`` when unmatched.
    """
    canon = canonicalize(raw)
    loose = _loose(raw)
    if not canon and not loose:
        return {"raw": raw, "canonical": None, "family": None, "kind": None, "match_method": "empty"}

    for family, rx, kind in _COMPILED:
        if rx.search(canon) or rx.search(loose):
            variant = VARIANT_MAP.get(canon, (canon or loose).title())
            return {"raw": raw, "canonical": variant, "family": family,
                    "kind": kind, "match_method": "registry"}

    return {"raw": raw, "canonical": (canon or loose).title(), "family": None,
            "kind": None, "match_method": "unmatched"}


def fuzzy_family(canon: str, known: list[str], cutoff: float = 0.86) -> str | None:
    """Stage-3 fuzzy fallback: nearest known canonical family string."""
    m = difflib.get_close_matches(canon, known, n=1, cutoff=cutoff)
    return m[0] if m else None


def normalize_model_series(raw_values) -> pd.DataFrame:
    """Normalize a series/list of raw model strings to a tidy mapping frame."""
    rows = [assign_family(v) for v in raw_values]
    return pd.DataFrame(rows)


def review_queue(mapping_df: pd.DataFrame) -> pd.DataFrame:
    """Frequency-sorted table of strings the registry could not place.

    This is what a human reviews on each corpus expansion -- *new terms only*.
    """
    unmatched = mapping_df[mapping_df["family"].isna()]
    if unmatched.empty:
        return pd.DataFrame(columns=["raw", "canonical", "count"])
    return (
        unmatched.groupby(["raw", "canonical"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
        .reset_index(drop=True)
    )


# --------------------------------------------------------------------------- #
# Numeric parsers (enable distribution plots for tile design / scale / duration)
# --------------------------------------------------------------------------- #

_LEN_UNITS_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0}
_AREA_UNITS_TO_KM2 = {"m2": 1e-6, "ha": 0.01, "km2": 1.0}
# Unit tokens tried immediately after a number (specific before generic "m").
_LEN_TAIL = ("mm", "cm", "km", "m")


# A range separator sitting directly between two numbers ("0.6 - 1.2", "5 to 10").
_RANGE_SEP = re.compile(r"\d\s*(?:-|–|—|~|to)\s*\d", re.IGNORECASE)


def _num_unit_tokens(s: str) -> list[list]:
    """Every number in ``s`` paired with the length unit written right after it.

    Returns ``[[value, unit_or_None], ...]``. The unit is whatever length token
    (mm/cm/km/m) immediately follows the number (allowing a space or hyphen);
    ``None`` when the number carries no explicit unit of its own.
    """
    out = []
    for mnum in re.finditer(r"\d+(?:\.\d+)?", s):
        tail = s[mnum.end(): mnum.end() + 6].lower().lstrip(" -\t")
        unit = None
        for cand in _LEN_TAIL:
            if re.match(rf"{cand}\b", tail):
                unit = cand
                break
        out.append([float(mnum.group()), unit])
    return out


def parse_lengths_m(text: str | None) -> dict:
    """Parse a free-text length field (depth/spacing) into meters WITHOUT averaging.

    Each number is converted with the unit written next to it; a number with no
    unit inherits the nearest neighbour's unit (units are usually stated once at
    the end of a list, e.g. "5, 10, and 20 m"), falling back to metres. This
    fixes mixed-unit strings ("1 m ... 40 cm") that a single whole-string unit
    would mis-scale. Structure is preserved, not averaged:
      * ``range``  -- two numbers joined by a range separator -> [min, max]
      * ``list``   -- several discrete numbers -> all kept
      * ``single`` -- one number
    """
    empty = {"value_type": None, "values": [], "min_m": None, "max_m": None, "n_values": 0}
    if text is None:
        return empty
    s = str(text)
    toks = _num_unit_tokens(s)
    if not toks:
        return empty

    units = [u for _, u in toks]
    # nearest following explicit unit, then nearest preceding, else metres
    nfollow = [None] * len(units)
    last = None
    for i in range(len(units) - 1, -1, -1):
        if units[i]:
            last = units[i]
        nfollow[i] = last
    nprec = [None] * len(units)
    last = None
    for i in range(len(units)):
        if units[i]:
            last = units[i]
        nprec[i] = last
    resolved = [units[i] or nfollow[i] or nprec[i] or "m" for i in range(len(units))]
    vals = [round(toks[i][0] * _LEN_UNITS_TO_M[resolved[i]], 4) for i in range(len(toks))]

    if len(vals) == 1:
        vtype, keep = "single", vals
    elif len(vals) == 2 and _RANGE_SEP.search(s) and "," not in s:
        vtype, keep = "range", [min(vals), max(vals)]
    else:
        vtype, keep = "list", vals
    return {"value_type": vtype, "values": keep,
            "min_m": min(keep), "max_m": max(keep), "n_values": len(keep)}


def area_to_km2(value: float | None, unit: str | None) -> float | None:
    """Harmonize a (value, unit) area pair to km2."""
    if value is None or pd.isna(value) or unit is None:
        return None
    f = _AREA_UNITS_TO_KM2.get(str(unit).strip().lower())
    return float(value) * f if f else None


def parse_year_span(text: str | None) -> dict:
    """Extract start/end year and span (years) from a study_duration string."""
    if text is None:
        return {"start_year": None, "end_year": None, "span_years": None}
    years = [int(y) for y in re.findall(r"\b(19|20)\d{2}\b", str(text))]
    # re.findall with the group above returns the prefix; redo without a group:
    years = [int(y) for y in re.findall(r"\b(?:19|20)\d{2}\b", str(text))]
    if not years:
        return {"start_year": None, "end_year": None, "span_years": None}
    start, end = min(years), max(years)
    return {"start_year": start, "end_year": end, "span_years": end - start + 1}


# --------------------------------------------------------------------------- #
# Temporal resolution -> ordinal bucket
# --------------------------------------------------------------------------- #

# Finest mentioned resolution wins (sub-daily beats annual when both appear).
_TEMPORAL_BUCKETS: list[tuple[str, str]] = [
    ("sub-daily", r"sub[- ]?daily|minute|hourly|hour|fps|\bsec|15[- ]?min|\bmin\b|transient|continuous"),
    ("daily",     r"daily|\bday\b"),
    ("weekly",    r"weekly|twice a month|biweekly|fortnight"),
    ("monthly",   r"monthly|month"),
    ("seasonal",  r"season|spring|summer|fall|autumn|winter|growing season"),
    ("annual",    r"annual|yearly|year|decad"),
]


def temporal_bucket(text: str | None) -> str | None:
    """Map a free-text temporal_resolution to a coarse ordinal bucket."""
    if text is None:
        return None
    s = str(text).lower()
    for label, pat in _TEMPORAL_BUCKETS:
        if re.search(pat, s):
            return label
    return None
