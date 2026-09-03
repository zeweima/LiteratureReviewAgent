"""
Phase 4 step 4: geography x topic -- where is each theme studied?

Joins the parsed study points (geo_points.csv) to the paper-level theme clusters
and draws a small-multiples grid of mini world maps, one per theme, each showing
that theme's study locations over a light basemap. This is the "new at scale"
deliverable: at 1.4k papers the spatial footprint of individual research themes
becomes legible (e.g. salinity clusters in arid regions, bioreactors in the US
Corn Belt + N. Europe).

Also writes stat_theme_geo_summary.csv: per theme, #geolocated papers, lon/lat
centroid, and dominant region.

Usage:
    python 9_geo_topic.py --name summary_bullets-highlights
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import geopandas as gpd

from geo_time import to_region
import paths


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--out-dir", default=str(paths.TABLES))
    ap.add_argument("--maps-dir", default=str(paths.MAPS))
    args = ap.parse_args()

    paths.ensure()
    in_dir, out_dir, maps = Path(args.in_dir), Path(args.out_dir), Path(args.maps_dir)

    clusters = pd.read_csv(in_dir / f"clusters_{args.name}.csv")      # paper_id, doi, cluster
    points = pd.read_csv(in_dir / "geo_points.csv")                    # paper_id, lat, lon, ...
    master = pd.read_csv(in_dir / "papers_master.csv")

    labeled_fp = in_dir / f"cluster_summary_{args.name}_labeled.csv"
    if labeled_fp.exists():
        summ = pd.read_csv(labeled_fp)
        theme_name = {int(r["cluster"]): r["theme"] for _, r in summ.iterrows()}
    else:
        summ = pd.read_csv(in_dir / f"cluster_summary_{args.name}.csv")
        theme_name = {int(r["cluster"]): str(r["top_terms"])[:40] for _, r in summ.iterrows()}
    n_paper = dict(zip(summ["cluster"].astype(int), summ["n_papers"]))

    pts = points.merge(clusters[["paper_id", "cluster"]], on="paper_id", how="inner")
    print(f"{len(pts)} geolocated papers across {pts['cluster'].nunique()} themes")

    world = gpd.read_file(maps / "World_Countries.geojson")

    # order themes by size (largest first)
    order = sorted(theme_name, key=lambda c: -n_paper.get(c, 0))
    ncol = 4
    nrow = int(np.ceil(len(order) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.2 * ncol, 2.4 * nrow))
    axes = np.array(axes).reshape(-1)

    for ax, c in zip(axes, order):
        world.plot(ax=ax, color="#f2f2f2", edgecolor="#dddddd", linewidth=0.2)
        sub = pts[pts["cluster"] == c]
        ax.scatter(sub["lon"], sub["lat"], s=10, c="#c02020", alpha=0.7, edgecolors="none")
        ax.set_title(f"[{c}] {theme_name[c]}\n{len(sub)} geoloc / {n_paper.get(c,0)} papers",
                     fontsize=7.5)
        ax.set_xlim(-170, 180); ax.set_ylim(-58, 84)
        ax.set_xticks([]); ax.set_yticks([])
    for ax in axes[len(order):]:
        ax.set_visible(False)

    fig.suptitle(f"Where each theme is studied ({args.name})", fontsize=13, y=1.001)
    fig.tight_layout()
    fig.savefig(paths.FIGURES / f"fig_geo_topic_{args.name}.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    # per-theme geo summary
    rows = []
    for c in order:
        sub = pts[pts["cluster"] == c]
        regions = []
        for pid in clusters[clusters["cluster"] == c]["paper_id"]:
            regions += to_region(master.set_index("paper_id")["country"].get(pid)
                                 if pid in master["paper_id"].values else None)
        dom = pd.Series(regions).value_counts()
        rows.append({
            "cluster": c, "theme": theme_name[c],
            "n_papers": n_paper.get(c, 0), "n_geolocated": len(sub),
            "lon_centroid": round(sub["lon"].mean(), 2) if len(sub) else None,
            "lat_centroid": round(sub["lat"].mean(), 2) if len(sub) else None,
            "dominant_region": dom.index[0] if len(dom) else None,
        })
    pd.DataFrame(rows).to_csv(out_dir / "stat_theme_geo_summary.csv",
                             index=False, encoding="utf-8-sig")
    print(f"Wrote fig_geo_topic_{args.name}.png and stat_theme_geo_summary.csv")


if __name__ == "__main__":
    main()
