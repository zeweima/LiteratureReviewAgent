"""Hierarchical geography of the tile-drainage corpus.

Produces, for the in-scope corpus (Tile-centered + Tile-related by default):

WORLD
  * geo_country_counts.csv + fig_geo_world_country.png   -- country choropleth
  * geo_region_counts.csv  + fig_geo_region.png          -- region bar chart
  * geo_points.csv         + fig_geo_world_points.png    -- parsed study locations

UNITED STATES (nested)
  * geo_state_counts.csv   + fig_geo_us_state.png        -- state choropleth
  * geo_county_counts.csv  + fig_geo_us_county.png       -- county choropleth
                                                            (explicit county field +
                                                             coordinate reverse-geocode)

WATERSHED (cross-cutting scale)
  * geo_watershed_counts.csv + fig_geo_watershed_top.png -- most-studied watersheds

County geography deliberately combines two independent signals: the explicit
``county`` field, and points obtained by parsing ``coordinates_text`` and
spatial-joining them to county polygons. Watersheds are handled separately from
the admin hierarchy because a watershed can straddle several counties/states.

Usage
-----
    python 3_geography.py
    python 3_geography.py --in-dir . --maps-dir ../../Data/Maps/BaseMap --relevance Tile-centered
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from shapely.ops import unary_union

import country_mapping as cm
import us_state_mapping as usm
from geo_coords import parse_coords, parse_coords_multi
from geo_time import to_region, REGION_ORDER, order_cols
import paths

US_LABELS = usm.US_COUNTRY_LABELS
_COUNTY_SUFFIX = (" county", " parish", " borough", " census area",
                  " municipality", " city and borough", " co.")


def _clean_county(name: str) -> str:
    s = str(name).strip().casefold().replace(".", "").replace("'", "")
    for suf in _COUNTY_SUFFIX:
        if s.endswith(suf.replace(".", "")):
            s = s[: -len(suf.replace(".", ""))]
            break
    return s.strip()


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def load_scope(in_dir: Path, relevance: set[str]) -> pd.DataFrame:
    """Load papers_master.csv and filter to the in-scope relevance levels."""
    m = pd.read_csv(in_dir / "papers_master.csv")
    m = m[m["relevance_level"].isin(relevance)].copy()
    # CSV read turns missing text into float NaN; coerce to None for the mappers.
    for col in ("country", "state_province", "county", "watershed", "coordinates_text"):
        m[col] = m[col].apply(lambda x: x if isinstance(x, str) else None)
    print(f"in-scope papers: {len(m)}  (relevance {sorted(relevance)})")
    return m


# --------------------------------------------------------------------------- #
# Geolocation + plausibility (parse once, sanity-check against stated country)
# --------------------------------------------------------------------------- #

def geolocate(master, world, offshore_deg=1.5):
    """Parse every ``coordinates_text`` once and sanity-check each point.

    Parsing yields a syntactically valid pair even when lat/lon are swapped or a
    hemisphere is wrong. We cross-check by spatial-joining each parsed point to
    the world polygons and comparing the country it *falls in* to the country the
    paper *states*. ``in_country`` is:
        True   -- point lands in a stated country, OR (for a point in water) lies
                  within ``offshore_deg`` degrees of a stated country's coastline
                  (keeps coastal/offshore field stations against coarse polygons)
        False  -- point lands in a different country / far out at sea: SUSPECT
        None   -- can't judge (unparsed, or no stated / mappable country)
    A bounding box is deliberately NOT used for the water case: the US bbox spans
    almost all longitudes (the Aleutians cross the dateline), so a mid-Pacific
    point would pass. Distance-to-polygon rejects open-ocean points instead.
    Points are never dropped here; the flag lets the map exclude bad points and
    the audit surface them.
    """
    lookup = cm.build_country_lookup(world)
    names = sorted({n for n in world["COUNTRY"].dropna().unique()})
    iso_geom = {iso: unary_union(list(grp.geometry))
                for iso, grp in world.groupby("ISO")}

    recs = []
    for _, r in master.iterrows():
        ct = r.get("coordinates_text")
        if not isinstance(ct, str) or not ct.strip():
            continue
        stated = set()
        for t in cm.split_country_field(r.get("country")):
            iso = cm.map_country_to_iso(t, lookup, names)
            if iso:
                stated.add(iso)
        stated_str = ";".join(sorted(stated))
        sites = parse_coords_multi(ct)   # every site in the study, no averaging
        base = {"paper_id": r["paper_id"], "doi": r["doi"],
                "country": r["country"], "original_value": ct,
                "stated_iso": stated_str, "n_points": len(sites)}
        if not sites:
            recs.append({**base, "lat": float("nan"), "lon": float("nan"),
                         "coord_method": "unparsed", "parsed": False,
                         "point_index": 0})
        else:
            for i, (lat, lon, method) in enumerate(sites):
                recs.append({**base, "lat": lat, "lon": lon,
                             "coord_method": method, "parsed": True,
                             "point_index": i})
    pts = pd.DataFrame(recs)
    if pts.empty:
        pts["matched_iso"] = pts["in_country"] = None
        return pts

    # spatial-join parsed points to world polygons -> the ISO they land in
    par = pts[pts["parsed"]].copy()
    matched = pd.Series(index=par.index, dtype=object)
    if len(par):
        g = gpd.GeoDataFrame(
            par, geometry=gpd.points_from_xy(par["lon"], par["lat"]), crs=4326)
        j = gpd.sjoin(g, world[["ISO", "geometry"]], how="left", predicate="within")
        j = j[~j.index.duplicated(keep="first")]
        matched = j["ISO"]
    pts["matched_iso"] = matched.reindex(pts.index)

    def judge(row):
        if not row["parsed"]:
            return None
        stated = set(row["stated_iso"].split(";")) if row["stated_iso"] else set()
        if not stated:
            return None                      # nothing to check against
        mi = row["matched_iso"]
        if isinstance(mi, str):
            return mi in stated              # landed in a country -> exact test
        # point in water: accept only if close to a stated country's coastline
        p = Point(row["lon"], row["lat"])
        for iso in stated:
            gm = iso_geom.get(iso)
            if gm is not None and p.distance(gm) <= offshore_deg:
                return True
        return False

    pts["in_country"] = pts.apply(judge, axis=1)
    n_pts = int(pts["parsed"].sum())
    n_multi = int(pts.loc[pts["parsed"], "n_points"].gt(1).groupby(
        pts.loc[pts["parsed"], "paper_id"]).any().sum())
    n_susp = int((pts["in_country"] == False).sum())
    print(f"  geolocate: {n_pts} points across "
          f"{pts.loc[pts['parsed'], 'paper_id'].nunique()} papers "
          f"({n_multi} multi-site); {n_susp} points outside stated country "
          f"(flagged suspect, excluded from the map)")
    return pts


# --------------------------------------------------------------------------- #
# WORLD: country choropleth + region
# --------------------------------------------------------------------------- #

def world_country(master, world, out_dir):
    rows = []
    for _, r in master.iterrows():
        for tok in cm.split_country_field(r["country"]):
            rows.append({"paper_id": r["paper_id"], "country_token": tok})
    cr = pd.DataFrame(rows)
    cr = cm.apply_country_mapping(cr, world)
    counts = cm.count_studies_by_iso(cr)
    counts.to_csv(out_dir / "geo_country_counts.csv", index=False, encoding="utf-8-sig")

    unmapped = sorted(set(cr[~cr["is_mapped"]]["country_token"]))
    print(f"  country: {counts['study_count'].sum()} paper-country links, "
          f"{len(counts)} countries; {len(unmapped)} unmapped tokens")
    if unmapped:
        print("    unmapped:", unmapped[:20])

    gdf = cm.merge_counts_to_map(world, counts)
    fig, ax = plt.subplots(figsize=(15, 7.5))
    gdf.plot(ax=ax, color="#f0f0f0", edgecolor="#bbbbbb", linewidth=0.3)
    plotted = gdf[gdf["study_count"] > 0]
    plotted.plot(ax=ax, column="study_count", cmap="YlOrRd", legend=True,
                 edgecolor="#999999", linewidth=0.3,
                 legend_kwds={"label": "studies", "shrink": 0.5})
    ax.set_title(f"Tile-drainage studies by country (n={len(master)} papers)")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(paths.FIGURES / "fig_geo_world_country.png", dpi=150)
    plt.close(fig)
    return cr


def world_region(master, out_dir):
    counter = Counter()
    for _, r in master.iterrows():
        seen = set(to_region(r["country"]))
        for reg in seen:
            counter[reg] += 1
    s = pd.Series(counter).reindex([x for x in REGION_ORDER if x in counter]).dropna()
    s = s.sort_values(ascending=True)
    s.to_frame("study_count").to_csv(out_dir / "geo_region_counts.csv", encoding="utf-8-sig")

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(s.index, s.values, color="#4C72B0")
    for i, v in enumerate(s.values):
        ax.text(v + 0.5, i, str(int(v)), va="center", fontsize=9)
    ax.set_xlabel("papers")
    ax.set_title("Studies by world region")
    fig.tight_layout()
    fig.savefig(paths.FIGURES / "fig_geo_region.png", dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# WORLD: parsed study points
# --------------------------------------------------------------------------- #

def world_points(geo, world, out_dir):
    """Plot the plausible parsed points; write ALL parsed points (with flag).

    One row per (paper, site): a multi-site study contributes several points.
    """
    parsed = geo[geo["parsed"]].copy()
    good = parsed[parsed["in_country"] != False].copy()    # keep True + None(unknown)
    # geo_points.csv = map-ready plausible sites; the full record (unparsed +
    # suspect + flags) lives in internal/audit_coordinates.csv.
    good[["paper_id", "doi", "country", "point_index", "n_points",
          "lat", "lon", "coord_method", "in_country"]].to_csv(
        out_dir / "geo_points.csv", index=False, encoding="utf-8-sig")
    n_susp = int((parsed["in_country"] == False).sum())
    print(f"  points: {len(parsed)} sites across {parsed['paper_id'].nunique()} "
          f"papers ({len(good)} plausible sites written, {n_susp} suspect excluded)")

    fig, ax = plt.subplots(figsize=(15, 7.5))
    world.plot(ax=ax, color="#f5f5f5", edgecolor="#cccccc", linewidth=0.3)
    ax.scatter(good["lon"], good["lat"], s=14, c="#c02020", alpha=0.55,
               edgecolors="none")
    ax.set_title(f"Study locations parsed from coordinates ({len(good)} papers)")
    ax.set_xlim(-180, 180)
    ax.set_ylim(-60, 85)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(paths.FIGURES / "fig_geo_world_points.png", dpi=150)
    plt.close(fig)
    return good


# --------------------------------------------------------------------------- #
# US: state choropleth
# --------------------------------------------------------------------------- #

def _us_only(master):
    return master[master["country"].fillna("").str.strip().str.casefold().isin(US_LABELS)].copy()


def us_state(master, states, out_dir):
    us = _us_only(master)
    rows = []
    for _, r in us.iterrows():
        for tok in usm.split_state_field(r["state_province"]):
            rows.append({"paper_id": r["paper_id"], "doi": r["doi"],
                         "state_token": tok})
    sr = pd.DataFrame(rows)
    sr = usm.apply_state_mapping(sr, states)
    counts = usm.count_studies_by_state(sr)
    counts.to_csv(out_dir / "geo_state_counts.csv", index=False, encoding="utf-8-sig")
    print(f"  US state: {len(us)} US papers, {counts['study_count'].sum()} "
          f"paper-state links across {len(counts)} states")

    gdf = usm.merge_counts_to_states(states, counts).to_crs(5070)
    conus = gdf[~gdf["STUSPS"].isin(["AK", "HI", "PR", "VI", "GU", "MP", "AS"])]
    fig, ax = plt.subplots(figsize=(12, 7))
    conus.plot(ax=ax, color="#f0f0f0", edgecolor="#999999", linewidth=0.4)
    hot = conus[conus["study_count"] > 0]
    hot.plot(ax=ax, column="study_count", cmap="YlOrRd", legend=True,
             edgecolor="#777777", linewidth=0.4,
             legend_kwds={"label": "studies", "shrink": 0.6})
    ax.set_title(f"US tile-drainage studies by state (n={len(us)} US papers)")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(paths.FIGURES / "fig_geo_us_state.png", dpi=150)
    plt.close(fig)
    return sr


# --------------------------------------------------------------------------- #
# US: county choropleth (explicit county + coordinate reverse-geocode)
# --------------------------------------------------------------------------- #

def us_county(master, states, counties, points, out_dir):
    us = _us_only(master)
    # state NAME -> STATEFP
    name_to_fp = dict(zip(states["NAME"], states["STATEFP"]))
    state_lookup = usm.build_state_lookup(states)
    state_names = sorted(states["NAME"].dropna().unique())

    # county lookup keyed by (STATEFP, cleaned NAME) -> GEOID
    counties = counties.copy()
    counties["clean"] = counties["NAME"].map(_clean_county)
    ckey = {(fp, cn): gid for fp, cn, gid in
            zip(counties["STATEFP"], counties["clean"], counties["GEOID"])}

    # Collect (paper_id, GEOID) pairs. A study may span several counties -- from
    # several named counties or several coordinate sites -- so we keep every
    # distinct county per paper (deduped), never collapsing multi-site studies.
    pairs = {}   # (paper_id, GEOID) -> source label ("explicit" wins over "coord")

    # 1) explicit county field (all counties that resolve, not just the first)
    for _, r in us.iterrows():
        county = r.get("county")
        if not isinstance(county, str) or not county.strip():
            continue
        st_tokens = usm.split_state_field(r.get("state_province"))
        fp = None
        for t in st_tokens:
            m = usm.map_state_token(t, state_lookup, state_names)
            if m["state"]:
                fp = name_to_fp.get(m["state"])
                break
        if fp is None:
            continue
        for cpart in str(county).replace(";", ",").split(","):
            gid = ckey.get((fp, _clean_county(cpart)))
            if gid:
                pairs[(r["paper_id"], gid)] = "explicit"

    # 2) coordinate reverse-geocode: every plausible US site -> its county
    us_ids = set(us["paper_id"])
    pts = points[points["paper_id"].isin(us_ids)].copy()
    if len(pts):
        gpts = gpd.GeoDataFrame(
            pts, geometry=gpd.points_from_xy(pts["lon"], pts["lat"]), crs=4326
        ).to_crs(counties.crs)
        joined = gpd.sjoin(gpts, counties[["GEOID", "geometry"]],
                           how="left", predicate="within")
        for _, r in joined.iterrows():
            gid = r.get("GEOID")
            if isinstance(gid, str):
                pairs.setdefault((r["paper_id"], gid), "coord")

    placed_papers = {pid for pid, _ in pairs}
    n_expl = len({pid for (pid, _), s in pairs.items() if s == "explicit"})
    n_multi = sum(1 for pid in placed_papers
                  if sum(1 for (p, _) in pairs if p == pid) > 1)
    print(f"  US county: {len(placed_papers)} papers placed to "
          f"{len(pairs)} (paper,county) pairs "
          f"({n_expl} papers w/ explicit county, {n_multi} multi-county)")

    if not pairs:
        print("  (no county assignments; skipping county map)")
        return

    ca = pd.DataFrame([{"paper_id": pid, "GEOID": gid, "source": src}
                       for (pid, gid), src in pairs.items()])
    ca = ca.merge(us[["paper_id", "doi", "state_province", "county"]],
                  on="paper_id", how="left")
    ca = ca.merge(counties[["GEOID", "NAMELSAD"]], on="GEOID", how="left")
    ca.to_csv(paths.INTERNAL / "geo_county_assignments.csv", index=False, encoding="utf-8-sig")

    counts = (ca.groupby("GEOID", as_index=False)
                .agg(study_count=("paper_id", "nunique")))
    counts = counts.merge(counties[["GEOID", "NAMELSAD", "STATEFP"]], on="GEOID", how="left")
    counts.sort_values("study_count", ascending=False).to_csv(
        out_dir / "geo_county_counts.csv", index=False, encoding="utf-8-sig")

    gdf = counties.merge(counts[["GEOID", "study_count"]], on="GEOID", how="left")
    gdf["study_count"] = gdf["study_count"].fillna(0).astype(int)
    gdf = gdf.to_crs(5070)
    conus = gdf[~gdf["STATEFP"].isin(["02", "15", "72", "78", "66", "69", "60"])]
    st_conus = states[~states["STUSPS"].isin(
        ["AK", "HI", "PR", "VI", "GU", "MP", "AS"])].to_crs(5070)

    fig, ax = plt.subplots(figsize=(13, 8))
    conus.plot(ax=ax, color="#f5f5f5", edgecolor="#dddddd", linewidth=0.15)
    hot = conus[conus["study_count"] > 0]
    hot.plot(ax=ax, column="study_count", cmap="YlOrRd", legend=True,
             edgecolor="#888888", linewidth=0.2,
             legend_kwds={"label": "studies", "shrink": 0.6})
    st_conus.boundary.plot(ax=ax, color="#888888", linewidth=0.5)
    ax.set_title(f"US tile-drainage studies by county "
                 f"({len(placed_papers)} papers placed to a county)")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(paths.FIGURES / "fig_geo_us_county.png", dpi=160)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# WATERSHED (cross-cutting)
# --------------------------------------------------------------------------- #

def watershed(master, out_dir, top=25):
    ws = master["watershed"].dropna().astype(str).str.strip()
    ws = ws[ws != ""]
    # normalize light: collapse whitespace, title-ish compare
    norm = ws.str.replace(r"\s+", " ", regex=True)
    counts = norm.value_counts()
    counts.to_frame("study_count").to_csv(
        out_dir / "geo_watershed_counts.csv", encoding="utf-8-sig")
    print(f"  watershed: {master['watershed'].notna().sum()} papers name a watershed; "
          f"{counts.size} distinct names")

    head = counts.head(top).sort_values(ascending=True)
    fig, ax = plt.subplots(figsize=(9, max(5, 0.32 * len(head))))
    ax.barh(head.index, head.values, color="#2A9D8F")
    for i, v in enumerate(head.values):
        ax.text(v + 0.05, i, str(int(v)), va="center", fontsize=8)
    ax.set_xlabel("papers")
    ax.set_title(f"Most-studied watersheds (top {len(head)})")
    fig.tight_layout()
    fig.savefig(paths.FIGURES / "fig_geo_watershed_top.png", dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------- #

def write_coord_audit(geo):
    """Per-paper coordinate parsing check: original text vs parsed lat/lon/method.

    Covers EVERY in-scope paper that stated coordinates -- the parser failures
    (parsed=False) AND the syntactically-valid-but-wrong points (in_country=False)
    -- so parse quality is auditable and nothing is silently dropped.
    -> internal/audit_coordinates.csv
    """
    audit = geo[["paper_id", "doi", "country", "original_value",
                 "point_index", "n_points", "lat", "lon", "coord_method",
                 "parsed", "matched_iso", "stated_iso", "in_country"]].copy()
    audit["lat"] = audit["lat"].apply(lambda v: None if pd.isna(v) else round(v, 5))
    audit["lon"] = audit["lon"].apply(lambda v: None if pd.isna(v) else round(v, 5))
    audit.to_csv(paths.INTERNAL / "audit_coordinates.csv",
                 index=False, encoding="utf-8-sig")
    n_papers = audit["paper_id"].nunique()
    n_ok_papers = audit.loc[audit["parsed"], "paper_id"].nunique()
    n_susp = int((audit["in_country"] == False).sum())
    print(f"  audit_coordinates.csv: {len(audit)} rows / {n_papers} papers, "
          f"{n_ok_papers} papers parsed "
          f"({(n_ok_papers / n_papers * 100 if n_papers else 0):.0f}%), "
          f"{n_susp} suspect sites (outside stated country)  [internal/]")
    return audit


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--out-dir", default=str(paths.TABLES))
    ap.add_argument("--maps-dir", default=str(paths.MAPS))
    ap.add_argument("--relevance", nargs="+",
                    default=["Tile-centered", "Tile-related"])
    args = ap.parse_args()

    paths.ensure()
    in_dir, out_dir, maps = Path(args.in_dir), Path(args.out_dir), Path(args.maps_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    master = load_scope(in_dir, set(args.relevance))

    print("loading basemaps ...")
    world = gpd.read_file(maps / "World_Countries.geojson")
    states = gpd.read_file(f"zip://{maps / 'tl_2025_us_state.zip'}")
    counties = gpd.read_file(f"zip://{maps / 'tl_2019_us_county.zip'}").to_crs(4326)

    print("GEOLOCATE (parse + plausibility) ...")
    geo = geolocate(master, world)
    write_coord_audit(geo)

    print("WORLD ...")
    world_country(master, world, out_dir)
    world_region(master, out_dir)
    points = world_points(geo, world, out_dir)

    print("UNITED STATES ...")
    us_state(master, states, out_dir)
    us_county(master, states, counties, points, out_dir)

    print("WATERSHED ...")
    watershed(master, out_dir)

    print("\nDone. Geography figures + CSVs written to", out_dir.resolve())


if __name__ == "__main__":
    main()
