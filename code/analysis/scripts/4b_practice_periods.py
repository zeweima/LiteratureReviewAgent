"""Adoption of drainage and mitigation practices over time.

The generic ``drainage_practices`` cross-tab counts rows rather than papers, so a
study that tags the same practice once per treatment is counted several times,
and it excludes cover crops, which are recorded under
``associated_management_practices``. This script counts distinct papers, pulls
both fields together, and reports the result three ways:

  * counts   -- mitigation practices only, on their own scale
  * share    -- the same practices as a percentage of each period's output, so
                emergence is separated from growth of the corpus itself
  * heatmap  -- every practice, in the style of the other period cross-tabs

Papers are binned by publication year using the shared 5-year periods. The final
bin is open at the time of corpus assembly (2025 and 2026 only) and is shaded in
the line and bar figures and asterisked everywhere.

Inputs (code/analysis/tables/):
    papers_master.csv, long_drainage_practices.csv, long_associated_practices.csv

Outputs:
    tables/stat_mitigation_practice_x_period.csv    -- counts and shares
    figures/fig_practice_x_period_counts.png        -- grouped bars
    figures/fig_practice_x_period_share.png         -- shares, with reference line
    figures/fig_practice_x_period_heat.png          -- annotated heatmap

Usage:
    python 4b_practice_periods.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

import paths
from geo_time import to_period

# (label, source table, column, value); heatmap rows follow this order
ALL_PRACTICES = [
    ("Conventional drainage", "long_drainage_practices.csv",
     "drainage_practices", "Conventional Drainage"),
    ("No drainage", "long_drainage_practices.csv",
     "drainage_practices", "No Drainage"),
    ("Controlled drainage", "long_drainage_practices.csv",
     "drainage_practices", "Controlled Drainage"),
    ("Subirrigation", "long_drainage_practices.csv",
     "drainage_practices", "Subirrigation"),
    ("Cover crop", "long_associated_practices.csv",
     "associated_management_practices", "Cover Crop"),
    ("Woodchip bioreactor", "long_drainage_practices.csv",
     "drainage_practices", "Bioreactor"),
    ("Saturated buffer", "long_drainage_practices.csv",
     "drainage_practices", "Saturated Buffer"),
]

# plotted in the counts and share figures, in order of first appearance
MITIGATION = ["Cover crop", "Controlled drainage", "Subirrigation",
              "Woodchip bioreactor", "Saturated buffer"]
REFERENCE = "Conventional drainage"

COLORS = {"Cover crop": "#66a61e", "Controlled drainage": "#4477aa",
          "Subirrigation": "#7570b3", "Woodchip bioreactor": "#d95f02",
          "Saturated buffer": "#c02020"}
REF_COLOR = "#999999"

PERIOD_ORDER = ["<2000", "2000-2004", "2005-2009", "2010-2014",
                "2015-2019", "2020-2024", "2025-2029"]
PARTIAL = "2025-2029"          # open interval at corpus assembly
PARTIAL_LABEL = "2025-26*"
IN_SCOPE = ("Tile-centered", "Tile-related")


def load(in_dir: Path) -> pd.DataFrame:
    m = pd.read_csv(in_dir / "papers_master.csv", low_memory=False)
    scope = m[m["relevance_level"].isin(IN_SCOPE)][["paper_id", "year"]].copy()
    scope["period"] = scope["year"].apply(to_period)
    return scope[scope["period"].notna()]


def series(in_dir: Path, scope: pd.DataFrame, spec) -> pd.Series:
    """Distinct in-scope papers per period reporting one practice.

    Deduplicates on paper_id: a study tagging the same practice once per
    treatment must count once, not once per treatment.
    """
    _, fn, col, value = spec
    d = pd.read_csv(in_dir / fn)
    ids = set(d.loc[d[col] == value, "paper_id"])
    hit = scope[scope["paper_id"].isin(ids)]
    return (hit.groupby("period")["paper_id"].nunique()
            .reindex(PERIOD_ORDER).fillna(0).astype(int))


def _axis(ax, ipart: int) -> None:
    ax.axvspan(ipart - 0.5, ipart + 0.5, color="#000000", alpha=0.06, zorder=0)
    ax.set_xticks(range(len(PERIOD_ORDER)))
    ax.set_xticklabels([PARTIAL_LABEL if p == PARTIAL else p for p in PERIOD_ORDER],
                       rotation=45, ha="right", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)


def fig_counts(counts, ipart, dpi) -> Path:
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    x = range(len(PERIOD_ORDER))
    width = 0.16
    for i, label in enumerate(MITIGATION):
        off = (i - (len(MITIGATION) - 1) / 2) * width
        ax.bar([v + off for v in x], counts[label].values, width,
               label=label, color=COLORS[label])
    ax.set_ylabel("Papers")
    ax.set_title("Mitigation practices, papers per period", fontsize=11)
    ax.legend(fontsize=8, frameon=False)
    _axis(ax, ipart)
    fig.tight_layout()
    fp = paths.FIGURES / "fig_practice_x_period_counts.png"
    fig.savefig(fp, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return fp


def fig_share(counts, totals, ipart, dpi) -> Path:
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    x = range(len(PERIOD_ORDER))
    for label in MITIGATION:
        ax.plot(x, (counts[label] / totals * 100).values, "o-",
                color=COLORS[label], label=label, markersize=4)
    ax.plot(x, (counts[REFERENCE] / totals * 100).values, "s--",
            color=REF_COLOR, markersize=4, label=REFERENCE)
    ax.set_ylabel("Share of papers in period (%)")
    ax.set_title("Practices as a share of output", fontsize=11)
    ax.legend(fontsize=8, frameon=False, ncol=2)
    _axis(ax, ipart)
    fig.tight_layout()
    fp = paths.FIGURES / "fig_practice_x_period_share.png"
    fig.savefig(fp, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return fp


def fig_heat(counts, dpi) -> Path:
    """Annotated heatmap matching the style of 5_spatiotemporal.heatmap."""
    mat = pd.DataFrame({spec[0]: counts[spec[0]] for spec in ALL_PRACTICES}).T
    mat.columns = [PARTIAL_LABEL if c == PARTIAL else c for c in mat.columns]

    fig, ax = plt.subplots(figsize=(1.5 + 0.8 * mat.shape[1],
                                    1.2 + 0.5 * mat.shape[0]))
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
    ax.set_title("Drainage and mitigation practice x period (distinct papers)",
                 fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.7)
    fig.tight_layout()
    fp = paths.FIGURES / "fig_practice_x_period_heat.png"
    fig.savefig(fp, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return fp


def build(in_dir: Path, dpi: int) -> dict:
    scope = load(in_dir)
    totals = (scope.groupby("period")["paper_id"].nunique()
              .reindex(PERIOD_ORDER).fillna(0).astype(int))
    counts = {spec[0]: series(in_dir, scope, spec) for spec in ALL_PRACTICES}

    tab = pd.DataFrame(counts)
    tab.insert(0, "papers_in_period", totals)
    share = (tab.drop(columns="papers_in_period").div(totals, axis=0) * 100).round(2)
    share.columns = [f"{c} (%)" for c in share.columns]
    out_tab = pd.concat([tab, share], axis=1)
    out_tab.index.name = "period"
    out_fp = paths.TABLES / "stat_mitigation_practice_x_period.csv"
    out_tab.to_csv(out_fp, encoding="utf-8-sig")

    ipart = PERIOD_ORDER.index(PARTIAL)
    figs = [fig_counts(counts, ipart, dpi),
            fig_share(counts, totals, ipart, dpi),
            fig_heat(counts, dpi)]

    return {"table": str(out_fp),
            "figures": [f.name for f in figs],
            "papers_per_period": totals.to_dict(),
            "totals": {k: int(v.sum()) for k, v in counts.items()}}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()
    paths.ensure()
    for k, v in build(Path(args.in_dir), args.dpi).items():
        print(f"{k:20s} {v}")


if __name__ == "__main__":
    main()
