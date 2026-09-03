"""
Category statistics and figures for the tile-drainage literature sample.

Builds cross-tab matrices, temporal trends, the (normalized) model landscape,
design-parameter distributions, and an extraction-completeness summary. Every
panel is also written as a CSV so the numbers are reusable outside the figure.

Inputs (from --in-dir, produced by scripts 3 & 5):
    papers_master.csv, papers_master_normalized.csv,
    long_study_type.csv, long_drainage_practices.csv,
    long_associated_practices.csv, long_physical_models_normalized.csv

Outputs (to --out-dir):
    stat_*.csv  +  fig_*.png

Usage:
    python 6_category_stats.py                 # in-scope papers (Tile-centered/related)
    python 6_category_stats.py --all           # all 100 papers
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import paths

TILE_RELEVANCE = {"Tile-centered", "Tile-related"}


def _save_heatmap(mat: pd.DataFrame, title: str, path: Path, cmap="YlOrRd"):
    fig, ax = plt.subplots(figsize=(1.2 + 0.7 * mat.shape[1], 1.2 + 0.5 * mat.shape[0]))
    im = ax.imshow(mat.values, cmap=cmap, aspect="auto")
    ax.set_xticks(range(mat.shape[1]), mat.columns, rotation=40, ha="right", fontsize=8)
    ax.set_yticks(range(mat.shape[0]), mat.index, fontsize=8)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.values[i, j]
            if v:
                ax.text(j, i, int(v), ha="center", va="center", fontsize=8,
                        color="white" if v > mat.values.max() * 0.6 else "black")
    ax.set_title(title, fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.7)
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def crosstab_long_x_scalar(long_df, label_col, master, scalar_col, ids):
    """Cross-tab a multilabel long field against a per-paper scalar field."""
    d = long_df[long_df["paper_id"].isin(ids)].merge(
        master[["paper_id", scalar_col]], on="paper_id", how="left")
    return pd.crosstab(d[label_col], d[scalar_col])


def cooccurrence(long_df, label_col, ids):
    """Symmetric co-occurrence matrix of a multilabel field within papers."""
    d = long_df[long_df["paper_id"].isin(ids)]
    labels = sorted(d[label_col].dropna().unique())
    idx = {l: i for i, l in enumerate(labels)}
    mat = np.zeros((len(labels), len(labels)), int)
    for _, grp in d.groupby("paper_id"):
        items = [idx[x] for x in grp[label_col].dropna().unique()]
        for a in items:
            for b in items:
                mat[a, b] += 1
    return pd.DataFrame(mat, index=labels, columns=labels)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--out-dir", default=str(paths.TABLES))
    ap.add_argument("--all", action="store_true", help="Use all papers (default: in-scope only)")
    args = ap.parse_args()

    paths.ensure()
    in_dir, out_dir = Path(args.in_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    master = pd.read_csv(in_dir / "papers_master.csv")
    mnorm = pd.read_csv(in_dir / "papers_master_normalized.csv")
    st = pd.read_csv(in_dir / "long_study_type.csv")
    dp = pd.read_csv(in_dir / "long_drainage_practices.csv")
    amp = pd.read_csv(in_dir / "long_associated_practices.csv")
    pm = pd.read_csv(in_dir / "long_physical_models_normalized.csv")

    if args.all:
        ids = set(master["paper_id"])
        scope = "all"
    else:
        ids = set(master.loc[master["relevance_level"].isin(TILE_RELEVANCE), "paper_id"])
        scope = "in-scope"
    print(f"Scope: {scope}  ({len(ids)} papers)")

    # 1) study_type x scale_category -------------------------------------------------
    m1 = crosstab_long_x_scalar(st, "study_type", master, "scale_category", ids)
    m1.to_csv(out_dir / "stat_studytype_x_scale.csv", encoding="utf-8-sig")
    _save_heatmap(m1, "Study type x Spatial scale", paths.FIGURES / "fig_studytype_x_scale.png")

    # 2) drainage_practices x study_type ---------------------------------------------
    dps = dp[dp["paper_id"].isin(ids)].merge(st, on=["paper_id", "doi"], how="inner")
    m2 = pd.crosstab(dps["drainage_practices"], dps["study_type"])
    m2.to_csv(out_dir / "stat_practice_x_studytype.csv", encoding="utf-8-sig")
    _save_heatmap(m2, "Drainage practice x Study type", paths.FIGURES / "fig_practice_x_studytype.png")

    # 3) ALL practices co-occurrence (drainage + management on BOTH axes) -------------
    # Pool every practice into one label space so within-group interactions are
    # visible too (e.g. Conventional Drainage <-> Subirrigation), not just
    # drainage-vs-management. Diagonal = papers using that practice.
    dp2 = (dp.rename(columns={"drainage_practices": "practice"})
             .assign(category="drainage")[["paper_id", "doi", "practice", "category"]])
    amp2 = (amp.rename(columns={"associated_management_practices": "practice"})
              .assign(category="management")[["paper_id", "doi", "practice", "category"]])
    practices = pd.concat([dp2, amp2], ignore_index=True)

    m3 = cooccurrence(practices, "practice", ids)
    # Order labels grouped by category (drainage block, then management block).
    order = (practices[practices["paper_id"].isin(ids)]
             .drop_duplicates("practice")
             .sort_values(["category", "practice"])["practice"].tolist())
    order = [p for p in order if p in m3.index]
    m3 = m3.reindex(index=order, columns=order)
    m3.to_csv(out_dir / "stat_practice_cooccurrence.csv", encoding="utf-8-sig")
    if m3.size:
        _save_heatmap(m3, "Practice co-occurrence (drainage + management; diagonal = #papers)",
                      paths.FIGURES / "fig_practice_cooccurrence.png")

    # 4) publications per year, stacked by study_type --------------------------------
    sty = st[st["paper_id"].isin(ids)].merge(master[["paper_id", "year"]], on="paper_id")
    yt = pd.crosstab(sty["year"], sty["study_type"])
    yt.to_csv(out_dir / "stat_year_x_studytype.csv", encoding="utf-8-sig")
    fig, ax = plt.subplots(figsize=(11, 5))
    yt.plot(kind="bar", stacked=True, ax=ax, colormap="tab10", width=0.9)
    ax.set_xlabel("Year"); ax.set_ylabel("Study-type mentions")
    ax.set_title(f"Publications over time by study type ({scope})")
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout(); fig.savefig(paths.FIGURES / "fig_year_x_studytype.png", dpi=200); plt.close(fig)

    # 5) model family landscape (normalized) -- FOCUS on kind="model" ----------------
    pmi = pm[pm["paper_id"].isin(ids) & (pm["kind"] == "model")]
    fam = pmi.groupby("family")["paper_id"].nunique().sort_values(ascending=False)
    fam.to_csv(out_dir / "stat_model_families.csv", encoding="utf-8-sig")
    fig, ax = plt.subplots(figsize=(9, 5))
    fam.head(15).iloc[::-1].plot(kind="barh", ax=ax, color="#4477aa")
    ax.set_xlabel("Papers"); ax.set_title(f"Process-model families ({scope}, normalized)")
    fig.tight_layout(); fig.savefig(paths.FIGURES / "fig_model_families.png", dpi=200); plt.close(fig)

    # Equations tracked separately (not the focus) -- saved as a side table.
    eqi = pm[pm["paper_id"].isin(ids) & (pm["kind"] == "equation")]
    (eqi.groupby("family")["paper_id"].nunique().sort_values(ascending=False)
        .to_csv(out_dir / "stat_analytical_equations.csv", encoding="utf-8-sig"))

    # 6) design-parameter distributions ----------------------------------------------
    # Depth/spacing use every individual value (ranges contribute both endpoints,
    # lists contribute all) from long_tile_dimensions_normalized.csv -- no averaging.
    mn = mnorm[mnorm["paper_id"].isin(ids)]
    dim = pd.read_csv(in_dir / "long_tile_dimensions_normalized.csv")
    dim = dim[dim["paper_id"].isin(ids) & dim["plausible"]]
    depth_vals = dim.loc[dim["field"] == "depth", "value_m"].dropna()
    spacing_vals = dim.loc[dim["field"] == "spacing", "value_m"].dropna()
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    for ax, vals, lab, ylab in [
        (axes[0], depth_vals, "Tile depth (m)", "values"),
        (axes[1], spacing_vals, "Tile spacing (m)", "values"),
        (axes[2], mn["span_years"].dropna(), "Study duration (years)", "papers"),
    ]:
        ax.hist(vals, bins=12, color="#66a61e", edgecolor="white")
        ax.set_xlabel(lab); ax.set_ylabel(ylab.title())
        ax.set_title(f"{lab}  (n={len(vals)} {ylab})", fontsize=10)
    fig.suptitle(f"Design-parameter distributions ({scope})")
    fig.tight_layout(); fig.savefig(paths.FIGURES / "fig_design_distributions.png", dpi=200); plt.close(fig)

    # 7) extraction completeness -----------------------------------------------------
    sub = master[master["paper_id"].isin(ids)]
    comp = pd.Series({
        "tile_design (depth)": (mn["depth_n_values"] > 0).mean(),
        "tile_design (spacing)": (mn["spacing_n_values"] > 0).mean(),
        "scale area": mn["area_km2"].notna().mean(),
        "study duration": mn["span_years"].notna().mean(),
        "quant stats": (sub["n_quantitative_stats"] > 0).mean(),
        "research gap": (sub["n_research_gap"] > 0).mean(),
        "scientific question": sub["has_scientific_question"].mean(),
        "future directions": (sub["n_future_directions"] > 0).mean(),
    }).sort_values()
    comp.to_csv(out_dir / "stat_completeness.csv", encoding="utf-8-sig")
    fig, ax = plt.subplots(figsize=(8, 5))
    (comp * 100).plot(kind="barh", ax=ax, color="#cc6677")
    ax.set_xlabel("% of papers"); ax.set_xlim(0, 100)
    ax.set_title(f"Field completeness ({scope}, n={len(ids)})")
    fig.tight_layout(); fig.savefig(paths.FIGURES / "fig_completeness.png", dpi=200); plt.close(fig)

    print("Wrote stat_*.csv and fig_*.png to", out_dir)
    print("\nstudy_type x scale:\n", m1.to_string())
    print("\nmodel families (top):\n", fam.head(10).to_string())


if __name__ == "__main__":
    main()
