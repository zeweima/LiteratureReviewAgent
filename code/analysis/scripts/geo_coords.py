"""Best-effort parser: free-text ``coordinates_text`` -> decimal (lat, lon).

The extraction field is highly heterogeneous (DMS, decimal degrees, swapped
lat/lon order, bounding-box ranges, multi-site lists, UTM, national grid refs,
OCR noise). We do NOT try to parse everything -- the goal is a usable point for
the *first* site mentioned, good enough for a global point map and for
spatial-joining US points to county polygons. Unparseable strings return NaN and
are reported, never silently dropped.

A short normalization pass (`_prep`) repairs the common OCR corruptions before
matching:
  * runs of control bytes that replaced the degree/minute glyphs (``33\\x02\\x18N``)
  * caret-OCR degree marks (``29^0 10'``)
  * a *dropped* minute marker between the degree sign and the hemisphere
    (``30°21N`` -> ``30°21'N``): without this the token re-anchors on the minutes
    and silently returns 21.0 instead of 30.35 -- a confidently-wrong value.

Returns (lat, lon, method) where method is one of:
    'dms'      -- degrees/minutes/seconds with N/S/E/W hemisphere
    'decimal'  -- decimal degrees with N/S/E/W hemisphere
    'bare'     -- bare signed decimal pair, order inferred
    'labeled'  -- explicitly labeled Lat/Long decimals
    'unparsed' -- nothing usable found

Parsing gives a point but not a *sanity check*: a swapped lat/lon or a wrong
hemisphere still yields a syntactically valid pair. Cross-checking the point
against the stated country's bounding box (see ``3_geography.py``) is what flags
those; this module only reports how the string was read.
"""

from __future__ import annotations

import math
import re

# A degree/minute/second token that ends in a hemisphere letter.
# Degree sign is optional (often OCR-dropped); minute/second markers vary.
_TOKEN = re.compile(
    r"""
    (?P<deg>\d{1,3}(?:\.\d+)?)          # degrees (may be decimal)
    \s*[°º˚]?\s*     # optional degree sign
    (?:
        (?P<min>\d{1,2}(?:\.\d+)?)\s*['′’]   # minutes + marker
        \s*
        (?:(?P<sec>\d{1,2}(?:\.\d+)?)\s*
           (?:''|"|″|’’|”))?        # optional seconds
    )?
    # hemisphere: a lone N/S/E/W letter or the spelled-out word, but NOT the
    # first letter of an ordinary prose word (the trailing letter-boundary keeps
    # "1 was" / "Wuhan" from reading as "1°W").
    \s*(?P<hemi>[Nn]orth|[Ss]outh|[Ee]ast|[Ww]est|[NSEWnsew])(?![A-Za-z])
    """,
    re.VERBOSE,
)

# Bare signed decimal pair fallback, e.g. "(47.791888, -96.593600)" or, with a
# stray degree sign and no hemisphere, "(53.936°, 11.877°)".
_BARE = re.compile(
    r"(-?\d{1,3}\.\d+)\s*[°º˚]?\s*[,;]\s*(-?\d{1,3}\.\d+)\s*[°º˚]?"
)

# Labeled decimal fallback, e.g. "Lat. 41.68; Long. -91.59" / "Latitude: 40.0, Longitude: -88.6".
_LAT_LABEL = re.compile(r"lat(?:itude)?\.?\s*[:=]?\s*(-?\d{1,3}\.\d+)", re.I)
_LON_LABEL = re.compile(r"lon(?:g|gitude)?\.?\s*[:=]?\s*(-?\d{1,3}\.\d+)", re.I)

# OCR repairs applied before tokenizing.
_CTRL_RUN = re.compile(r"[\x00-\x1f]+")          # mojibake for a degree/minute glyph
_CARET_DEG = re.compile(r"\^[0oO]")              # "^0" / "^o" used for the degree sign
_LATEX_DEG = re.compile(r"\^?\s*\{?\s*\\circ\s*\}?")   # LaTeX "^{\circ}" degree markup
# A minute run wedged between the degree sign and the hemisphere with its prime
# marker dropped: "30°21N" -> minutes = 21, not a second degrees token.
_DROPPED_MIN = re.compile(r"([°º˚])\s*(\d{1,2})\s*(?=[NSEWnsew]\b)")
# A seconds run wedged between the minute marker and the hemisphere with its
# double-prime dropped: "03°03′37.3E" -> seconds = 37.3, not a second lon token.
_DROPPED_SEC = re.compile(r"(['′’])\s*(\d{1,2}(?:\.\d+)?)\s*(?=[NSEW]\b)")


def _prep(text: str) -> str:
    """Repair common OCR/markup corruptions so the degree/minute/second structure survives."""
    text = _LATEX_DEG.sub("°", text)
    text = _CTRL_RUN.sub("°", text)
    text = _CARET_DEG.sub("°", text)
    text = _DROPPED_MIN.sub(r"\1\2'", text)
    text = _DROPPED_SEC.sub(r"\1\2''", text)
    return text


def _to_dd(deg: float, minute, sec, hemi: str) -> float | None:
    """DMS -> signed decimal degrees; reject impossible minutes/seconds (OCR noise)."""
    m = float(minute) if minute is not None else 0.0
    s = float(sec) if sec is not None else 0.0
    if m >= 60 or s >= 60:
        return None
    dd = float(deg) + m / 60.0 + s / 3600.0
    if hemi.upper() in ("S", "W"):
        dd = -dd
    return dd


def _hemi_points(text):
    """Pair consecutive N/S and E/W hemisphere tokens into (lat, lon, method) sites.

    A study often reports several sites ("...(30°21N,112°31E)...(30°33N,114°22E)").
    We walk the hemisphere tokens in text order and flush a point whenever both a
    latitude and a longitude are in hand (or a duplicate axis starts a new site),
    so each site is recovered independently -- no averaging across sites.
    """
    points = []
    cur = {}

    def flush():
        if "lat" in cur and "lon" in cur:
            la, lam = cur["lat"]
            lo, lom = cur["lon"]
            points.append((la, lo, lam if lam == lom else "dms"))

    for mt in _TOKEN.finditer(text):
        hemi = mt.group("hemi")[0].upper()   # "South" -> "S"
        dd = _to_dd(mt.group("deg"), mt.group("min"), mt.group("sec"), hemi)
        if dd is None:
            continue
        method = "dms" if mt.group("min") is not None else "decimal"
        kind = "lat" if hemi in ("N", "S") else "lon"
        if (kind == "lat" and abs(dd) > 90) or (kind == "lon" and abs(dd) > 180):
            continue
        if kind in cur:            # same axis again -> previous site was incomplete
            flush()
            cur = {}
        cur[kind] = (dd, method)
        if "lat" in cur and "lon" in cur:
            flush()
            cur = {}
    return points


def _dedup(points, max_points=20):
    seen, out = set(), []
    for p in points:
        key = (round(p[0], 4), round(p[1], 4))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out[:max_points]


def parse_coords_multi(text) -> list[tuple[float, float, str]]:
    """Extract EVERY distinct (lat, lon) site from a free-text field (order preserved).

    Returns [] when nothing is usable. Multi-site studies keep all their points;
    callers decide how to use them (we map each point and never average them).
    """
    if not isinstance(text, str) or not text.strip():
        return []
    text = _prep(text)

    pts = _hemi_points(text)
    if pts:
        return _dedup(pts)

    # Fallback: bare decimal pairs, possibly several ("41.17,-96.47; 40.57,-98.15").
    bares = []
    for bm in _BARE.finditer(text):
        a, b = float(bm.group(1)), float(bm.group(2))
        if abs(a) > 90 and abs(b) <= 90:      # first can't be lat but could be lon
            a, b = b, a
        if abs(a) <= 90 and abs(b) <= 180:
            bares.append((a, b, "bare"))
    if bares:
        return _dedup(bares)

    # Fallback: explicitly labeled lat/long decimals (single site).
    la, lo = _LAT_LABEL.search(text), _LON_LABEL.search(text)
    if la and lo:
        a, b = float(la.group(1)), float(lo.group(1))
        if abs(a) <= 90 and abs(b) <= 180:
            return [(a, b, "labeled")]

    return []


def parse_coords(text) -> tuple[float, float, str]:
    """First usable (lat, lon) site -- back-compat single-point view of the field."""
    pts = parse_coords_multi(text)
    if pts:
        return pts[0]
    return float("nan"), float("nan"), "unparsed"


def parse_series(texts):
    """Vectorized-ish helper: list/Series of strings -> list of (lat, lon, method)."""
    return [parse_coords(t) for t in texts]


if __name__ == "__main__":
    import os
    import pandas as pd

    here = os.path.dirname(os.path.abspath(__file__))
    master = pd.read_csv(os.path.join(here, "papers_master.csv"))
    coords = master["coordinates_text"].dropna()
    parsed = [parse_coords(s) for s in coords]
    ok = sum(1 for la, lo, mth in parsed if not math.isnan(la))
    from collections import Counter
    methods = Counter(m for _, _, m in parsed)
    print(f"coordinate strings: {len(coords)}")
    print(f"parsed to lat/lon: {ok} ({ok/len(coords)*100:.1f}%)")
    print("methods:", dict(methods))
    # show a few failures for inspection
    fails = [s for s, (la, _, _) in zip(coords, parsed) if math.isnan(la)]
    print(f"\n{len(fails)} unparsed; first 15:")
    for s in fails[:15]:
        print("  ", repr(s)[:80])
