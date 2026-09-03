"""Map extracted country strings to ISO codes in World_Countries.geojson."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

# Common aliases from LLM extraction -> GeoJSON ``COUNTRY`` / ``ISO``.
COUNTRY_ALIASES: dict[str, str] = {
    "usa": "United States",
    "u.s.a.": "United States",
    "u.s.": "United States",
    "us": "United States",
    "u.s": "United States",
    "u.s.a": "United States",
    "united states of america": "United States",
    "america": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "northern ireland": "United Kingdom",
    "the netherlands": "Netherlands",
    "republic of ireland": "Ireland",
    "russia": "Russian Federation",
    "south korea": "South Korea",
    "north korea": "North Korea",
    "czechia": "Czech Republic",
    "ivory coast": "Côte d'Ivoire",
    "cote d'ivoire": "Côte d'Ivoire",
}

GLOBAL_LABELS = {"global", "worldwide", "international", "multi-country", "multiple countries"}


def _clean_country_token(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" .")
    return text


def split_country_field(country: str | None) -> list[str]:
    """Split a raw extraction country field into individual country tokens."""
    if country is None:
        return []
    text = _clean_country_token(country)
    if not text:
        return []
    if text.lower() in GLOBAL_LABELS:
        return []

    parts = re.split(r"\s*(?:,|;|/|\band\b)\s*", text, flags=re.IGNORECASE)
    return [_clean_country_token(p) for p in parts if _clean_country_token(p)]


def build_country_lookup(gdf: gpd.GeoDataFrame) -> dict[str, str]:
    """Build lowercase name -> ISO lookup from GeoJSON ``COUNTRY`` column."""
    lookup: dict[str, str] = {}
    for country, iso in zip(gdf["COUNTRY"], gdf["ISO"], strict=False):
        if country and iso:
            lookup[country.casefold()] = iso
    for alias, canonical in COUNTRY_ALIASES.items():
        iso = lookup.get(canonical.casefold())
        if iso:
            lookup[alias.casefold()] = iso
    return lookup


def map_country_token(
    country: str,
    lookup: dict[str, str],
    country_names: list[str],
    iso_to_name: dict[str, str],
    *,
    fuzzy_cutoff: float = 0.88,
) -> dict[str, Any]:
    """Resolve one country token to ISO and canonical GeoJSON country name."""
    token = _clean_country_token(country)
    if not token:
        return {
            "iso": None,
            "matched_country": None,
            "match_method": "unmapped",
        }

    key = token.casefold()
    iso: str | None = None
    match_method = "unmapped"

    if key in lookup:
        iso = lookup[key]
        match_method = "exact"
    else:
        alias = COUNTRY_ALIASES.get(key)
        if alias:
            iso = lookup.get(alias.casefold())
            if iso:
                match_method = "alias"
        if iso is None:
            matches = difflib.get_close_matches(
                token,
                country_names,
                n=1,
                cutoff=fuzzy_cutoff,
            )
            if matches:
                iso = lookup.get(matches[0].casefold())
                if iso:
                    match_method = "fuzzy"

    return {
        "iso": iso,
        "matched_country": iso_to_name.get(iso) if iso else None,
        "match_method": match_method if iso else "unmapped",
    }


def map_country_to_iso(
    country: str,
    lookup: dict[str, str],
    country_names: list[str],
    *,
    fuzzy_cutoff: float = 0.88,
) -> str | None:
    """Resolve one country token to an ISO code."""
    iso_to_name: dict[str, str] = {}
    result = map_country_token(
        country,
        lookup,
        country_names,
        iso_to_name,
        fuzzy_cutoff=fuzzy_cutoff,
    )
    return result["iso"]


def apply_country_mapping(
    country_rows: pd.DataFrame,
    gdf: gpd.GeoDataFrame,
    *,
    fuzzy_cutoff: float = 0.88,
) -> pd.DataFrame:
    """Add ``iso``, ``matched_country``, and ``match_method`` columns to country rows."""
    lookup = build_country_lookup(gdf)
    country_names = sorted({name for name in gdf["COUNTRY"].dropna().unique()})
    iso_to_name = (
        gdf.drop_duplicates("ISO")
        .set_index("ISO")["COUNTRY"]
        .to_dict()
    )

    out = country_rows.copy()
    mapped = [
        map_country_token(
            token,
            lookup,
            country_names,
            iso_to_name,
            fuzzy_cutoff=fuzzy_cutoff,
        )
        for token in out["country_token"]
    ]
    out["iso"] = [m["iso"] for m in mapped]
    out["matched_country"] = [m["matched_country"] for m in mapped]
    out["match_method"] = [m["match_method"] for m in mapped]
    out["is_mapped"] = out["iso"].notna()
    return out


def load_study_country_rows(
    results_dir: str | Path,
    *,
    relevance_levels: set[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load merged extractions and expand to one row per (paper, country token).

    Returns:
        studies: one row per paper with raw country field and relevance
        country_rows: one row per mapped country token (may be empty ISO)
    """
    if relevance_levels is None:
        relevance_levels = {"Tile-centered"}

    results_dir = Path(results_dir)
    studies: list[dict[str, Any]] = []
    country_rows: list[dict[str, Any]] = []

    for path in sorted(results_dir.glob("*/merged_extraction.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        doi = data.get("bib_metadata", {}).get("doi") or path.parent.name
        relevance = data.get("relevance") or {}
        level = relevance.get("level")
        location = (data.get("metadata") or {}).get("location") or {}
        country_raw = location.get("country")

        studies.append(
            {
                "paper_id": path.parent.name,
                "doi": doi,
                "relevance_level": level,
                "country_raw": country_raw,
            }
        )
        if level not in relevance_levels:
            continue

        for token in split_country_field(country_raw):
            country_rows.append(
                {
                    "paper_id": path.parent.name,
                    "doi": doi,
                    "relevance_level": level,
                    "country_raw": country_raw,
                    "country_token": token,
                }
            )

    return pd.DataFrame(studies), pd.DataFrame(country_rows)


def count_studies_by_iso(
    country_rows: pd.DataFrame,
    gdf: gpd.GeoDataFrame | None = None,
    *,
    fuzzy_cutoff: float = 0.88,
) -> pd.DataFrame:
    """
    Count distinct papers per ISO from country rows.

    If ``iso`` is not already present, applies :func:`apply_country_mapping` first
  when ``gdf`` is provided.
    """
    if "iso" not in country_rows.columns:
        if gdf is None:
            raise ValueError("gdf is required when country_rows has no iso column")
        country_rows = apply_country_mapping(
            country_rows,
            gdf,
            fuzzy_cutoff=fuzzy_cutoff,
        )

    mapped = country_rows[country_rows["iso"].notna()]
    if mapped.empty:
        return pd.DataFrame(columns=["ISO", "study_count", "matched_country"])

    counts = (
        mapped.groupby("iso", as_index=False)
        .agg(study_count=("paper_id", "nunique"))
        .rename(columns={"iso": "ISO"})
    )
    if "matched_country" in mapped.columns:
        iso_to_name = (
            mapped.drop_duplicates("iso")
            .set_index("iso")["matched_country"]
        )
        counts["matched_country"] = counts["ISO"].map(iso_to_name)
    return counts


def merge_counts_to_map(gdf: gpd.GeoDataFrame, counts: pd.DataFrame) -> gpd.GeoDataFrame:
    """Left-join study counts onto the world GeoJSON."""
    out = gdf.merge(counts, on="ISO", how="left")
    out["study_count"] = out["study_count"].fillna(0).astype(int)
    return out
