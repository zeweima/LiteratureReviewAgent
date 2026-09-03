"""Phase 4 step 6: how MACRO themes vary in time and space.

Uses the paper->macro assignment (clusters_<name>.csv) -- no embeddings needed,
so this is fast. For a clustered field (default the knowledge track) it writes:

  temporal   fig_macro_x_period_<name>.png            -- counts, macro x 5-yr bin
             fig_macro_share_period_<name>.png        -- 100% share (compositional
                                                          shift over time)
  spatial    fig_macro_x_region_<name>.png            -- macro x world region
  within-US  fig_macro_x_state_<name>.png             -- macro x top US states
             fig_macro_us_maps_<name>.png             -- one CONUS choropleth per
                                                          macro theme

Each figure has a companion stat_*.csv. A paper carries one macro theme; for
region/state it is exploded over its country/state tokens (a multi-state study
counts in each state), consistent with the rest of the geography.

Usage:
    python 11_macro_geography.py --name summary_bullets-highlights
    python 11_macro_geography.py --name future_directions
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

import us_state_mapping as usm
from geo_time import (PERIOD_ORDER, REGION_ORDER, order_cols, to_period, to_region)
import paths

US_LABELS = usm.US_COUNTRY_LABELS
_TERR = ["AK", "HI", "PR", "VI", "GU", "MP", "AS"]


def heatmap(mat, title, path, label_map=None, fmt_int=True):
    if mat.empty:
        return
    if label_map:
        mat = mat.rename(index=label_map)
    fig, ax = plt.subplots(figsize=(1.6 + 0.7 * mat.shape[1], 1.5 + 0.5 * mat.shape[0]))
    im = ax.imshow(mat.values, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(mat.shape[1]), mat.columns, rotation=35, ha="right", fontsize=8)
    ax.set_yticks(range(mat.shape[0]), mat.index, fontsize=8)
    mx = mat.values.max() or 1
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.values[i, j]
            if v:
                ax.text(j, i, f"{int(v)}" if fmt_int else f"{v:.0f}",
                        ha="center", va="center", fontsize=7,
                        color="white" if v > mx * 0.6 else "black")
    ax.set_title(title, fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.7)
    fig.tight_layout(); fig.savefig(path, dpi=200, bbox_inches="tight"); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default="summary_bullets-highlights")
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--out-dir", default=str(paths.TABLES))
    ap.add_argument("--maps-dir", default=str(paths.MAPS))
    args = ap.parse_args()

    paths.ensure()
    in_dir, out_dir, maps = Path(args.in_dir), Path(args.out_dir), Path(args.maps_dir)

    clusters = pd.read_csv(in_dir / f"clusters_{args.name}.csv")
    if "macro_cluster" not in clusters.columns:
        raise SystemExit(f"clusters_{args.name}.csv has no macro_cluster column; "
                         "re-run 6_topic_clusters.py with --macro-k first.")
    msum = pd.read_csv(in_dir / f"cluster_summary_macro_{args.name}.csv")
    master = pd.read_csv(in_dir / "papers_master.csv")

    # order macro themes by size; label = "id: theme"
    msum = msum.sort_values("n_papers", ascending=False)
    order = msum["macro_cluster"].tolist()
    mlabel = {int(r["macro_cluster"]): f"{int(r['macro_cluster'])}: {r['macro_theme']}"
              for _, r in msum.iterrows()}

    df = clusters.merge(master[["paper_id", "year", "country", "state_province"]],
                        on="paper_id", how="left")
    df["period"] = df["year"].apply(to_period)

    def order_rows(mat):
        return mat.reindex(index=[m for m in order if m in mat.index])

    # ---- 1) temporal: macro x period (counts) ------------------------------
    mp = order_cols(order_rows(pd.crosstab(df["macro_cluster"], df["period"])), PERIOD_ORDER)
    mp.to_csv(out_dir / f"stat_macro_x_period_{args.name}.csv", encoding="utf-8-sig")
    heatmap(mp, f"Macro-theme x period (counts): {args.name}",
            paths.FIGURES / f"fig_macro_x_period_{args.name}.png", mlabel)

    # ---- 2) temporal: 100% share per period (compositional shift) ----------
    share = (mp / mp.sum(axis=0)).fillna(0) * 100          # column-normalized
    share.to_csv(out_dir / f"stat_macro_share_period_{args.name}.csv", encoding="utf-8-sig")
    periods = list(mp.columns)
    x = np.arange(len(periods))
    fig, ax = plt.subplots(figsize=(1.6 + 1.1 * len(periods), 6))
    cmap = plt.get_cmap("tab10").resampled(len(order))
    ax.stackplot(x, *[share.loc[m].values for m in order],
                 labels=[mlabel[m] for m in order],
                 colors=[cmap(i) for i in range(len(order))], alpha=0.9)
    ax.set_xticks(x, periods, rotation=35, ha="right")
    ax.set_ylim(0, 100); ax.set_ylabel("Share of papers in period (%)")
    ax.set_title(f"Macro-theme composition over time: {args.name}")
    ax.legend(fontsize=7, loc="center left", bbox_to_anchor=(1.0, 0.5))
    fig.tight_layout()
    fig.savefig(paths.FIGURES / f"fig_macro_share_period_{args.name}.png",
                dpi=200, bbox_inches="tight"); plt.close(fig)

    # ---- 3) spatial: macro x world region ----------------------------------
    reg_rows = [(r["macro_cluster"], reg)
                for _, r in df.iterrows() for reg in to_region(r["country"])]
    reg_df = pd.DataFrame(reg_rows, columns=["macro_cluster", "region"])
    mr = order_cols(order_rows(pd.crosstab(reg_df["macro_cluster"], reg_df["region"])),
                    REGION_ORDER)
    mr.to_csv(out_dir / f"stat_macro_x_region_{args.name}.csv", encoding="utf-8-sig")
    heatmap(mr, f"Macro-theme x world region: {args.name}",
            paths.FIGURES / f"fig_macro_x_region_{args.name}.png", mlabel)

    # ---- 4) within-US: macro x state ---------------------------------------
    states = gpd.read_file(f"zip://{maps / 'tl_2025_us_state.zip'}")
    lookup = usm.build_state_lookup(states)
    state_names = sorted(states["NAME"].dropna().unique())
    us = df[df["country"].fillna("").str.strip().str.casefold().isin(US_LABELS)]
    srows = []
    for _, r in us.iterrows():
        sp = r["state_province"]
        for tok in usm.split_state_field(sp if isinstance(sp, str) else None):
            st = usm.map_state_token(tok, lookup, state_names)["state"]
            if st:
                srows.append({"paper_id": r["paper_id"], "macro_cluster": r["macro_cluster"],
                              "state": st})
    sdf = pd.DataFrame(srows).drop_duplicates(["paper_id", "macro_cluster", "state"])
    ms = order_rows(pd.crosstab(sdf["macro_cluster"], sdf["state"]))
    top_states = ms.sum(axis=0).sort_values(ascending=False).head(15).index.tolist()
    ms_top = ms[top_states]
    ms.to_csv(out_dir / f"stat_macro_x_state_{args.name}.csv", encoding="utf-8-sig")
    heatmap(ms_top, f"Macro-theme x US state (top {len(top_states)}): {args.name}",
            paths.FIGURES / f"fig_macro_x_state_{args.name}.png", mlabel)

    # ---- 5) within-US: one CONUS choropleth per macro theme ----------------
    conus = states[~states["STUSPS"].isin(_TERR)].to_crs(5070)
    n = len(order)
    ncol = 3
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.2 * ncol, 3.2 * nrow))
    axes = np.array(axes).reshape(-1)
    for i, m in enumerate(order):
        ax = axes[i]
        cnt = ms.loc[m] if m in ms.index else pd.Series(dtype=int)
        g = conus.merge(cnt.rename("c"), left_on="NAME", right_index=True, how="left")
        g["c"] = g["c"].fillna(0)
        g.plot(ax=ax, color="#f2f2f2", edgecolor="#cccccc", linewidth=0.3)
        hot = g[g["c"] > 0]
        if len(hot):
            hot.plot(ax=ax, column="c", cmap="YlOrRd", edgecolor="#999999",
                     linewidth=0.3, legend=True, legend_kwds={"shrink": 0.6})
        theme = msum.loc[msum["macro_cluster"] == m, "macro_theme"].iloc[0]
        ax.set_title(f"{theme}\n({int(cnt.sum())} US papers)", fontsize=9)
        ax.set_axis_off()
    for j in range(n, len(axes)):
        axes[j].set_axis_off()
    fig.suptitle(f"Where each macro theme is studied within the US: {args.name}",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(paths.FIGURES / f"fig_macro_us_maps_{args.name}.png",
                dpi=160, bbox_inches="tight"); plt.close(fig)

    print(f"Macro geography for '{args.name}':")
    print(f"  temporal: fig_macro_x_period + fig_macro_share_period")
    print(f"  spatial:  fig_macro_x_region")
    print(f"  US:       fig_macro_x_state (top {len(top_states)}) + fig_macro_us_maps")
    print(f"  US papers with a mapped state: {sdf['paper_id'].nunique()}")


if __name__ == "__main__":
    main()
