"""Diagnostic: how many paper-level themes does the data actually support?

Rebuilds the exact clustering inputs used by 6_topic_clusters.py (sbert units ->
mean-pool per paper -> L2-normalize -> Ward), then sweeps k and reports:
  * silhouette (cosine)      -- cohesion vs separation, higher = better
  * Calinski-Harabasz        -- variance ratio, higher = better
  * Davies-Bouldin           -- lower = better
  * Ward merge-gap ("elbow") -- largest jumps in linkage distance
  * max centroid cosine      -- theme redundancy (rises when over-split)
Writes internal/kdiag_<label>.csv (all five indices plus the smallest theme
size) + figures/fig_kdiag_<label>.png (silhouette, Calinski-Harabasz, and
Davies-Bouldin only; centroid redundancy and merge-gaps stay in the table).
No clustering outputs are overwritten -- this only informs the k choice.

Use --replot to redraw the figure from an existing table, which skips the
embedding step entirely.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, fcluster
from sklearn.metrics import (calinski_harabasz_score, davies_bouldin_score,
                             silhouette_score)
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize

from topic_cluster import embed_sbert
import paths

FIELDS = {"summary_bullets": ("long_summary_bullets.csv", "summary_bullets"),
          "highlights": ("long_highlights.csv", "highlights"),
          "future_directions": ("long_future_directions.csv", "future_directions")}
TILE = {"Tile-centered", "Tile-related"}


PANELS = [("silhouette", "Silhouette (cosine) ↑", "#1f77b4"),
          ("calinski_harabasz", "Calinski-Harabasz ↑", "#d95f02"),
          ("davies_bouldin", "Davies-Bouldin ↓", "#7570b3")]


def draw(diag: pd.DataFrame, label: str, gap_k) -> Path:
    """Render the three-panel diagnostic. Kept separate from the sweep so the
    figure can be redrawn from kdiag_<label>.csv without re-embedding."""
    fig, ax = plt.subplots(1, len(PANELS), figsize=(15, 4))
    for a, (col, title, color) in zip(ax, PANELS):
        a.plot(diag["k"], diag[col], "o-", color=color)
        a.set_title(title)
        a.set_xlabel("k (themes)")
        a.grid(alpha=0.3)
    suffix = f"   (Ward merge-gap k ≈ {gap_k})" if gap_k else ""
    fig.suptitle(f"Theme-count diagnostics: {label}{suffix}")
    fig.tight_layout()
    out = paths.FIGURES / f"fig_kdiag_{label}.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", required=True, nargs="+", choices=list(FIELDS))
    ap.add_argument("--name", default=None)
    ap.add_argument("--kmin", type=int, default=6)
    ap.add_argument("--kmax", type=int, default=32)
    ap.add_argument("--replot", action="store_true",
                    help="redraw the figure from an existing kdiag_<label>.csv "
                         "instead of re-running the embedding + sweep")
    ap.add_argument("--gap-k", default="",
                    help="merge-gap k list to annotate a --replot title, e.g. '2,4,5,6'")
    args = ap.parse_args()
    paths.ensure()
    label = args.name or "-".join(args.field)
    in_dir = paths.TABLES

    if args.replot:
        fp = paths.INTERNAL / f"kdiag_{label}.csv"
        if not fp.is_file():
            raise SystemExit(f"no diagnostic table to replot: {fp}")
        diag = pd.read_csv(fp)
        gap_k = ([int(x) for x in args.gap_k.split(",") if x.strip()]
                 if args.gap_k else None)
        print(f"replot {fp.name} -> {draw(diag, label, gap_k)}")
        return

    parts = []
    for fld in args.field:
        fn, col = FIELDS[fld]
        d = pd.read_csv(in_dir / fn).dropna(subset=[col]).rename(columns={col: "text"})
        parts.append(d[["paper_id", "doi", "text"]])
    units = pd.concat(parts, ignore_index=True)
    master = pd.read_csv(in_dir / "papers_master.csv")
    ids = set(master.loc[master["relevance_level"].isin(TILE), "paper_id"])
    units = units[units["paper_id"].isin(ids)].reset_index(drop=True)

    U, _ = embed_sbert(units["text"].astype(str).tolist())
    pid = units["paper_id"].to_numpy()
    uniq = pd.unique(pid)
    P = normalize(np.vstack([U[pid == p].mean(0) for p in uniq]))
    print(f"{label}: {len(units)} units -> {len(uniq)} paper vectors")

    Z = linkage(P, method="ward")
    rows = []
    for k in range(args.kmin, args.kmax + 1):
        lab = fcluster(Z, k, criterion="maxclust")
        cents = normalize(np.vstack([P[lab == c].mean(0) for c in np.unique(lab)]))
        S = cosine_similarity(cents)
        np.fill_diagonal(S, 0.0)
        rows.append({"k": k,
                     "silhouette": silhouette_score(P, lab, metric="cosine"),
                     "calinski_harabasz": calinski_harabasz_score(P, lab),
                     "davies_bouldin": davies_bouldin_score(P, lab),
                     "max_centroid_cos": float(S.max()),
                     "min_cluster_n": int(np.bincount(lab)[1:].min())})
    diag = pd.DataFrame(rows)
    diag.to_csv(paths.INTERNAL / f"kdiag_{label}.csv", index=False, encoding="utf-8-sig")

    # Ward merge-gaps: biggest jumps in the last merges -> natural cut points
    merged = Z[:, 2]
    gaps = np.diff(merged)
    top = np.argsort(gaps)[::-1][:8]
    gap_k = sorted(len(uniq) - (i + 1) for i in top)  # k implied by cutting at that gap

    draw(diag, label, gap_k)

    print("\nWard merge-gap suggested k (largest linkage jumps):", gap_k)
    print(diag.to_string(index=False))


if __name__ == "__main__":
    main()
