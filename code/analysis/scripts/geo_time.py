"""Shared country->region and year->period helpers (used by spatial/temporal viz).

COUNTRY_REGION is the curated, slow-growing asset (regions grow far slower than
papers). Extend it as new countries appear in the corpus.
"""

from __future__ import annotations

import re

import pandas as pd

COUNTRY_REGION = {
    # North America (incl. common US aliases so they don't leak into "Other")
    "united states": "N. America", "usa": "N. America", "canada": "N. America",
    "united states of america": "N. America", "u.s.a.": "N. America",
    "u.s.a": "N. America", "u.s.": "N. America", "u.s": "N. America",
    "us": "N. America", "america": "N. America", "north america": "N. America",
    "mexico": "N. America",
    # Latin America
    "brazil": "Latin America", "bolivia": "Latin America", "venezuela": "Latin America",
    "argentina": "Latin America", "chile": "Latin America", "colombia": "Latin America",
    "peru": "Latin America", "uruguay": "Latin America", "ecuador": "Latin America",
    # East Asia
    "china": "E. Asia", "japan": "E. Asia", "south korea": "E. Asia",
    "korea": "E. Asia", "taiwan": "E. Asia",
    # SE Asia
    "malaysia": "SE Asia", "indonesia": "SE Asia", "thailand": "SE Asia",
    "vietnam": "SE Asia", "philippines": "SE Asia", "myanmar": "SE Asia",
    # South Asia
    "india": "S. Asia", "pakistan": "S. Asia", "bangladesh": "S. Asia",
    "sri lanka": "S. Asia", "nepal": "S. Asia",
    # Central Asia
    "uzbekistan": "Central Asia", "kazakhstan": "Central Asia",
    "turkmenistan": "Central Asia", "tajikistan": "Central Asia",
    "kyrgyzstan": "Central Asia",
    # MENA
    "iran": "MENA", "israel": "MENA", "egypt": "MENA", "turkey": "MENA",
    "tunisia": "MENA", "libya": "MENA", "algeria": "MENA", "morocco": "MENA",
    "syria": "MENA", "iraq": "MENA", "saudi arabia": "MENA", "jordan": "MENA",
    # Oceania
    "new zealand": "Oceania", "australia": "Oceania",
    # Sub-Saharan Africa
    "south africa": "Sub-Saharan Africa", "kenya": "Sub-Saharan Africa",
    "rwanda": "Sub-Saharan Africa", "burkina faso": "Sub-Saharan Africa",
    "ethiopia": "Sub-Saharan Africa", "nigeria": "Sub-Saharan Africa",
    "tanzania": "Sub-Saharan Africa", "ghana": "Sub-Saharan Africa",
}
_EUROPE = {
    "poland", "france", "sweden", "finland", "netherlands", "the netherlands",
    "ireland", "republic of ireland", "norway", "estonia", "latvia", "lithuania",
    "denmark", "czech republic", "czechia", "spain", "germany", "united kingdom",
    "uk", "england", "scotland", "wales", "italy", "belgium", "austria",
    "switzerland", "portugal", "serbia", "croatia", "albania", "romania",
    "russia", "greece", "hungary", "slovakia", "slovenia", "bulgaria",
}
for _c in _EUROPE:
    COUNTRY_REGION[_c] = "Europe"

REGION_ORDER = ["N. America", "Latin America", "Europe", "E. Asia", "SE Asia",
                "S. Asia", "Central Asia", "MENA", "Oceania",
                "Sub-Saharan Africa", "Global", "Other"]
# 5-year publication-year bins (finer than decades). Pre-2000 is sparse in the
# corpus, so it stays a single catch-all; everything from 2000 on is binned by 5.
PERIOD_ORDER = ["<2000"] + [f"{y}-{y + 4}" for y in range(2000, 2030, 5)]


def to_region(country) -> list[str]:
    """Explode a (possibly multi-) country field into region labels."""
    if not isinstance(country, str) or not country.strip():
        return []
    if country.strip().lower() in {"global", "worldwide", "international"}:
        return ["Global"]
    tokens = re.split(r"\s*(?:,|;|/|\band\b)\s*", country, flags=re.IGNORECASE)
    regions = []
    for t in tokens:
        t = t.strip().lower()
        if t:
            regions.append(COUNTRY_REGION.get(t, "Other"))
    return sorted(set(regions))


def to_period(year) -> str | None:
    """Publication year -> 5-year bin label ("2015-2019"); pre-2000 lumped."""
    if pd.isna(year):
        return None
    y = int(year)
    if y < 2000:
        return "<2000"
    lo = (y // 5) * 5
    return f"{lo}-{lo + 4}"


def order_cols(mat, order):
    return mat.reindex(columns=[c for c in order if c in mat.columns])
