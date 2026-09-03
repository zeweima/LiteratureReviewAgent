"""
Spatial & temporal variation of the tile-drainage literature (clean categoricals).

Characterizes HOW the clean categorical fields vary across space (region) and
time (period) -- no NLP required. Topic-level variation (themes x space/time) is
handled downstream with the theme clustering.

Slices produced (each as stat_*.csv + fig_*.png):
    temporal (x period):  drainage practice, study type, model family, scale
    spatial  (x region):  drainage practice, study type, model family

Notes
-----
* Multi-country papers are exploded to one (paper, region) row per region, so a
  pan-regional study counts in each region (consistent with the country map).
* Region/period taxonomy lives in geo_time.py -- the curated, slow-growing asset.

Usage:
    python 5_spatiotemporal.py --in-dir . --out-dir .
    python 5_spatiotemporal.py --all
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from geo_time import to_region, to_period, order_cols, REGION_ORDER, PERIOD_ORDER
import paths

TILE_RELEVANCE = {"Tile-centered", "Tile-related"}


def heatmap(mat: pd.DataFrame, title: str, path: Path):
    if mat.empty:
        return
    fig, ax = plt.subplots(figsize=(1.5 + 0.8 * mat.shape[1], 1.2 + 0.5 * mat.shape[0]))
    im = ax.imshow(mat.values, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(mat.shape[1]), mat.columns, rotation=30, ha="right", fontsize=8)
    ax.set_yticks(range(mat.shape[0]), mat.index, fontsize=8)
    mx = mat.values.max() or 1
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.values[i, j]
            if v:
                ax.text(j, i, int(v), ha="center", va="center", fontsize=8,
                        color="white" if v > mx * 0.6 else "black")
    ax.set_title(title, fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.7)
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--out-dir", default=str(paths.TABLES))
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    paths.ensure()
    in_dir, out_dir = Path(args.in_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    master = pd.read_csv(in_dir / "papers_master.csv")
    dp = pd.read_csv(in_dir / "long_drainage_practices.csv")
    st = pd.read_csv(in_dir / "long_study_type.csv")
    pm = pd.read_csv(in_dir / "long_physical_models_normalized.csv")

    if args.all:
        ids = set(master["paper_id"]); scope = "all"
    else:
        ids = set(master.loc[master["relevance_level"].isin(TILE_RELEVANCE), "paper_id"])
        scope = "in-scope"

    pap = master[master["paper_id"].isin(ids)].copy()
    pap["period"] = pap["year"].apply(to_period)
    region_rows = []
    for _, r in pap.iterrows():
        for reg in to_region(r["country"]):
            region_rows.append({"paper_id": r["paper_id"], "region": reg})
    region_df = pd.DataFrame(region_rows)
    print(f"Scope: {scope} ({len(ids)} papers)")
    print("Region coverage:\n", region_df["region"].value_counts().to_string())

    period_map = pap.set_index("paper_id")["period"]

    def by_period(long_df, col, fill_ids):
        d = long_df[long_df["paper_id"].isin(fill_ids)].copy()
        d["period"] = d["paper_id"].map(period_map)
        return order_cols(pd.crosstab(d[col], d["period"]), PERIOD_ORDER)

    def by_region(long_df, col, fill_ids):
        d = long_df[long_df["paper_id"].isin(fill_ids)].merge(region_df, on="paper_id")
        return order_cols(pd.crosstab(d[col], d["region"]), REGION_ORDER)

    # Dedupe variants within a paper so a paper listing several variants of one
    # family (e.g. SWAT2005 + SWAT2009, DRAINMOD + DRAINMOD-N) counts once per
    # family, not once per variant, in the row-counted crosstabs below.
    pm_model = (pm[pm["kind"] == "model"]
                .rename(columns={"family": "model_family"})
                .drop_duplicates(["paper_id", "model_family"]))

    jobs = [
        ("drainage practice", dp, "drainage_practices"),
        ("study type", st, "study_type"),
        ("model family", pm_model, "model_family"),
    ]
    for label, df, col in jobs:
        mp = by_period(df, col, ids)
        mp.to_csv(out_dir / f"stat_{col}_x_period.csv", encoding="utf-8-sig")
        heatmap(mp, f"{label.title()} x period ({scope})", paths.FIGURES / f"fig_{col}_x_period.png")

        mr = by_region(df, col, ids)
        mr.to_csv(out_dir / f"stat_{col}_x_region.csv", encoding="utf-8-sig")
        heatmap(mr, f"{label.title()} x region ({scope})", paths.FIGURES / f"fig_{col}_x_region.png")

    scdf = pap.dropna(subset=["scale_category", "period"])
    msc = order_cols(pd.crosstab(scdf["scale_category"], scdf["period"]), PERIOD_ORDER)
    msc.to_csv(out_dir / "stat_scale_x_period.csv", encoding="utf-8-sig")
    heatmap(msc, f"Scale x period ({scope})", paths.FIGURES / "fig_scale_x_period.png")

    print("\nDone. stat_*_x_period.csv / *_x_region.csv + figures in", out_dir)
    print("\ndrainage practice x period:\n", by_period(dp, "drainage_practices", ids).to_string())


if __name__ == "__main__":
    main()
