"""Combined choropleth + study-site maps, for the world and for the US.

Merges the two layers that ``3_geography.py`` renders separately -- the
country/state choropleth of paper counts, and the point cloud of study sites
parsed from ``coordinates_text`` -- into one map each, so coverage (where work
is reported) and precision (where it was actually done) can be read together.

Reads the tables ``3_geography.py`` already wrote, so no re-geocoding happens
here; run that script first if the corpus changed.

Inputs (code/analysis/tables/):
    geo_country_counts.csv, geo_state_counts.csv, geo_points.csv

Outputs (code/analysis/figures/):
    fig_geo_world_combined.png   -- world choropleth + all study sites
    fig_geo_us_combined.png      -- CONUS state choropleth + US study sites

Counts are strongly right-skewed (the US alone holds ~40% of the corpus), so the
colour scale is logarithmic by default; ``--linear`` restores the linear scale
used by the original single-layer figures.

Usage:
    python 3b_geo_combined_maps.py
    python 3b_geo_combined_maps.py --linear --dpi 300
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import LogNorm, Normalize
from matplotlib.lines import Line2D

import country_mapping as cm
import paths
import us_state_mapping as usm

CMAP = "YlOrRd"
LAND_ZERO = "#f0f0f0"      # countries/states with no paper
LAND_EDGE = "#9a9a9a"
PT_COLOR = "#08306b"       # cool colour so sites stay legible over warm fill
PT_EDGE = "#ffffff"
NON_CONUS = ["AK", "HI", "PR", "VI", "GU", "MP", "AS"]


def norm_for(vmax: int, log: bool):
    return LogNorm(vmin=1, vmax=max(vmax, 2)) if log else Normalize(vmin=0, vmax=vmax)


def add_colorbar(fig, ax, mappable, label: str):
    cb = fig.colorbar(mappable, ax=ax, shrink=0.55, pad=0.02)
    cb.set_label(label, fontsize=10)
    cb.ax.tick_params(labelsize=9)
    return cb


def point_legend(ax, n_sites: int, n_papers: int):
    handle = Line2D([], [], marker="o", linestyle="none", markersize=5,
                    markerfacecolor=PT_COLOR, markeredgecolor=PT_EDGE,
                    markeredgewidth=0.4)
    ax.legend([handle],
              [f"study site ({n_sites:,} sites, {n_papers:,} papers)"],
              loc="lower left", frameon=False, fontsize=9,
              handletextpad=0.6, borderaxespad=0.8)


# --------------------------------------------------------------------------- #
# WORLD
# --------------------------------------------------------------------------- #

def world_combined(world, counts, points, log: bool, dpi: int) -> dict:
    gdf = cm.merge_counts_to_map(world, counts)
    gdf = gdf[gdf["COUNTRY"].astype(str) != "Antarctica"]
    vmax = int(gdf["study_count"].max())

    # figure aspect follows the plotted extent (360 x 142 deg) so the equal-aspect
    # axes fills the canvas instead of floating in whitespace
    fig, ax = plt.subplots(figsize=(15, 6.0))
    gdf.plot(ax=ax, color=LAND_ZERO, edgecolor=LAND_EDGE, linewidth=0.25)
    hot = gdf[gdf["study_count"] > 0]
    hot.plot(ax=ax, column="study_count", cmap=CMAP, norm=norm_for(vmax, log),
             edgecolor=LAND_EDGE, linewidth=0.25)

    ax.scatter(points["lon"], points["lat"], s=11, c=PT_COLOR, alpha=0.75,
               linewidths=0.25, edgecolors=PT_EDGE, zorder=3)

    sm = plt.cm.ScalarMappable(cmap=CMAP, norm=norm_for(vmax, log))
    add_colorbar(fig, ax, sm, "papers per country")
    point_legend(ax, len(points), points["paper_id"].nunique())

    ax.set_xlim(-180, 180)
    ax.set_ylim(-58, 84)
    ax.set_title("Global distribution of the tile-drainage corpus", fontsize=13)
    ax.set_axis_off()
    fig.tight_layout()
    out = paths.FIGURES / "fig_geo_world_combined.png"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)

    return {"countries": int((gdf["study_count"] > 0).sum()),
            "max_country": vmax,
            "sites": len(points),
            "papers_with_sites": int(points["paper_id"].nunique()),
            "output": str(out)}


# --------------------------------------------------------------------------- #
# UNITED STATES
# --------------------------------------------------------------------------- #

def us_combined(states, counts, points, log: bool, dpi: int) -> dict:
    gdf = usm.merge_counts_to_states(states, counts)
    conus = gdf[~gdf["STUSPS"].isin(NON_CONUS)].to_crs(5070)
    vmax = int(conus["study_count"].max())

    # Select sites by falling inside a US state polygon, not by the stated
    # country string: a point is US if it lands there.
    gpts = gpd.GeoDataFrame(
        points, geometry=gpd.points_from_xy(points["lon"], points["lat"]), crs=4326)
    joined = gpd.sjoin(gpts, gdf[["STUSPS", "geometry"]].to_crs(4326),
                       how="inner", predicate="within")
    n_us_all = len(joined)
    joined = joined[~joined["STUSPS"].isin(NON_CONUS)]
    us_pts = joined.to_crs(5070)

    fig, ax = plt.subplots(figsize=(12.5, 6.6))
    conus.plot(ax=ax, color=LAND_ZERO, edgecolor=LAND_EDGE, linewidth=0.4)
    hot = conus[conus["study_count"] > 0]
    hot.plot(ax=ax, column="study_count", cmap=CMAP, norm=norm_for(vmax, log),
             edgecolor=LAND_EDGE, linewidth=0.4)

    ax.scatter(us_pts.geometry.x, us_pts.geometry.y, s=18, c=PT_COLOR,
               alpha=0.75, linewidths=0.3, edgecolors=PT_EDGE, zorder=3)

    sm = plt.cm.ScalarMappable(cmap=CMAP, norm=norm_for(vmax, log))
    add_colorbar(fig, ax, sm, "papers per state")
    point_legend(ax, len(us_pts), us_pts["paper_id"].nunique())

    ax.set_title("United States distribution of the tile-drainage corpus",
                 fontsize=13)
    ax.set_axis_off()
    fig.tight_layout()
    out = paths.FIGURES / "fig_geo_us_combined.png"
    fig.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(fig)

    return {"states": int((conus["study_count"] > 0).sum()),
            "max_state": vmax,
            "us_sites": n_us_all,
            "conus_sites": len(us_pts),
            "papers_with_conus_sites": int(us_pts["paper_id"].nunique()),
            "output": str(out)}


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--maps-dir", default=str(paths.MAPS))
    ap.add_argument("--linear", action="store_true",
                    help="linear colour scale instead of the default log scale")
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()

    paths.ensure()
    in_dir, maps = Path(args.in_dir), Path(args.maps_dir)
    log = not args.linear

    for name in ("geo_country_counts.csv", "geo_state_counts.csv", "geo_points.csv"):
        if not (in_dir / name).is_file():
            raise SystemExit(f"missing input {in_dir / name}; run 3_geography.py first")

    country_counts = pd.read_csv(in_dir / "geo_country_counts.csv")
    state_counts = pd.read_csv(in_dir / "geo_state_counts.csv")
    points = pd.read_csv(in_dir / "geo_points.csv")

    print("loading basemaps ...")
    world = gpd.read_file(maps / "World_Countries.geojson")
    states = gpd.read_file(f"zip://{maps / 'tl_2025_us_state.zip'}")

    print(f"scale: {'log' if log else 'linear'}")
    print("WORLD ...")
    for k, v in world_combined(world, country_counts, points, log, args.dpi).items():
        print(f"  {k:24s} {v}")

    print("UNITED STATES ...")
    for k, v in us_combined(states, state_counts, points, log, args.dpi).items():
        print(f"  {k:24s} {v}")


if __name__ == "__main__":
    main()
