"""Map extracted ``state_province`` strings to US states in the TIGER state shapefile.

Mirrors ``country_mapping.py`` but at the US-state level. Handles multi-state
fields (e.g. ``"Ohio and Michigan"`` or comma-separated lists) by splitting into
one row per state token, and accepts both full names ("Minnesota") and USPS
abbreviations ("MN").
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

US_COUNTRY_LABELS = {
    "united states", "united states of america", "usa", "u.s.a.", "u.s.",
    "us", "america",
}

# Non-state tokens that sometimes appear in the field and should be dropped.
_DROP_TOKENS = {"midwest", "upper midwest", "corn belt", "great lakes", "great plains"}


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    return text.strip(" .")


def split_state_field(state: str | None) -> list[str]:
    """Split a raw ``state_province`` field into individual state tokens."""
    if state is None:
        return []
    text = _clean(state)
    if not text:
        return []
    parts = re.split(r"\s*(?:,|;|/|\band\b)\s*", text, flags=re.IGNORECASE)
    out = []
    for p in parts:
        p = _clean(p)
        if p and p.lower() not in _DROP_TOKENS:
            out.append(p)
    return out


def build_state_lookup(gdf: gpd.GeoDataFrame) -> dict[str, str]:
    """Lowercase {full name, abbreviation} -> canonical state NAME."""
    lookup: dict[str, str] = {}
    for name, abbr in zip(gdf["NAME"], gdf["STUSPS"], strict=False):
        if name:
            lookup[name.casefold()] = name
        if abbr:
            lookup[abbr.casefold()] = name
    return lookup


def map_state_token(
    token: str,
    lookup: dict[str, str],
    state_names: list[str],
    *,
    fuzzy_cutoff: float = 0.9,
) -> dict[str, Any]:
    """Resolve one state token to a canonical state NAME."""
    token = _clean(token)
    key = token.casefold()
    if not token:
        return {"state": None, "match_method": "unmapped"}
    if key in lookup:
        return {"state": lookup[key], "match_method": "exact"}
    matches = difflib.get_close_matches(token, state_names, n=1, cutoff=fuzzy_cutoff)
    if matches:
        return {"state": matches[0], "match_method": "fuzzy"}
    return {"state": None, "match_method": "unmapped"}


def load_us_state_rows(
    results_dir: str | Path,
    *,
    relevance_levels: set[str] | None = None,
) -> pd.DataFrame:
    """Expand merged extractions to one row per (US paper, state token).

    Only papers whose ``country`` is the United States and whose relevance is in
    ``relevance_levels`` are kept.
    """
    if relevance_levels is None:
        relevance_levels = {"Tile-centered", "Tile-related"}

    results_dir = Path(results_dir)
    rows: list[dict[str, Any]] = []
    for path in sorted(results_dir.glob("*/merged_extraction.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        doi = (data.get("bib_metadata") or {}).get("doi") or path.parent.name
        level = (data.get("relevance") or {}).get("level")
        location = (data.get("metadata") or {}).get("location") or {}
        country = (location.get("country") or "").strip()
        state_raw = location.get("state_province")

        if level not in relevance_levels:
            continue
        if country.casefold() not in US_COUNTRY_LABELS:
            continue

        for token in split_state_field(state_raw):
            rows.append({
                "paper_id": path.parent.name,
                "doi": doi,
                "relevance_level": level,
                "state_raw": state_raw,
                "state_token": token,
            })
    return pd.DataFrame(rows)


def apply_state_mapping(
    state_rows: pd.DataFrame,
    gdf: gpd.GeoDataFrame,
    *,
    fuzzy_cutoff: float = 0.9,
) -> pd.DataFrame:
    """Add ``state`` (canonical NAME) and ``match_method`` columns."""
    lookup = build_state_lookup(gdf)
    state_names = sorted(gdf["NAME"].dropna().unique())
    out = state_rows.copy()
    mapped = [
        map_state_token(t, lookup, state_names, fuzzy_cutoff=fuzzy_cutoff)
        for t in out["state_token"]
    ]
    out["state"] = [m["state"] for m in mapped]
    out["match_method"] = [m["match_method"] for m in mapped]
    out["is_mapped"] = out["state"].notna()
    return out


def count_studies_by_state(state_rows: pd.DataFrame) -> pd.DataFrame:
    """Count distinct papers per canonical state NAME."""
    mapped = state_rows[state_rows["state"].notna()]
    if mapped.empty:
        return pd.DataFrame(columns=["NAME", "study_count"])
    return (
        mapped.groupby("state", as_index=False)
        .agg(study_count=("paper_id", "nunique"))
        .rename(columns={"state": "NAME"})
        .sort_values("study_count", ascending=False)
        .reset_index(drop=True)
    )


def merge_counts_to_states(gdf: gpd.GeoDataFrame, counts: pd.DataFrame) -> gpd.GeoDataFrame:
    """Left-join state study counts onto the TIGER state GeoDataFrame."""
    out = gdf.merge(counts, on="NAME", how="left")
    out["study_count"] = out["study_count"].fillna(0).astype(int)
    return out
