"""
Phase 4 step 3: visualize the paper-level theme clusters.

Produces for a clustered field label (e.g. summary_bullets-highlights):
  * fig_map_<name>.png          -- 2-D paper map (PCA/t-SNE), points = papers,
                                   colored by theme, centroids labelled.
  * fig_themesize_<name>.png    -- papers per theme (bar, LLM theme names).
  * fig_theme_x_period_<name>.png / .csv   -- theme by decade
  * fig_theme_x_region_<name>.png / .csv   -- theme by world region

Paper vectors are rebuilt from the same fields used to cluster (mean-pooled sbert
embeddings), so the projection matches the clustering geometry. Theme names come
from cluster_summary_<name>_labeled.csv when present, else keyword short-labels.

Usage:
    python 8_cluster_viz.py --name summary_bullets-highlights --field summary_bullets highlights --proj tsne
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
from sklearn.preprocessing import normalize

from geo_time import PERIOD_ORDER, REGION_ORDER, order_cols, to_period, to_region
from topic_cluster import embed_sbert, embed_tfidf
import paths

FIELDS = {
    "summary_bullets":   ("long_summary_bullets.csv", "summary_bullets"),
    "highlights":        ("long_highlights.csv", "highlights"),
    "future_directions": ("long_future_directions.csv", "future_directions"),
    "limitations":       ("long_limitations.csv", "limitations"),
    "research_gap":      ("long_research_gap.csv", "research_gap"),
}


def short_label(cluster, terms_str):
    terms = [t.strip() for t in str(terms_str).split(",")][:3]
    return f"{cluster}: " + ", ".join(terms)


def heatmap(mat, title, path, label_map=None):
    if mat.empty:
        return
    if label_map:
        mat = mat.rename(index=label_map)
    fig, ax = plt.subplots(figsize=(1.5 + 0.8 * mat.shape[1], 1.5 + 0.45 * mat.shape[0]))
    im = ax.imshow(mat.values, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(mat.shape[1]), mat.columns, rotation=30, ha="right", fontsize=8)
    ax.set_yticks(range(mat.shape[0]), mat.index, fontsize=7)
    mx = mat.values.max() or 1
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.values[i, j]
            if v:
                ax.text(j, i, int(v), ha="center", va="center", fontsize=7,
                        color="white" if v > mx * 0.6 else "black")
    ax.set_title(title, fontsize=10)
    fig.colorbar(im, ax=ax, shrink=0.7)
    fig.tight_layout(); fig.savefig(path, dpi=200, bbox_inches="tight"); plt.close(fig)


def paper_vectors(in_dir, fields, order_ids):
    """Rebuild mean-pooled sbert paper vectors in the order of order_ids."""
    parts = []
    for fld in fields:
        fname, col = FIELDS[fld]
        d = pd.read_csv(in_dir / fname).dropna(subset=[col]).rename(columns={col: "text"})
        parts.append(d[["paper_id", "text"]])
    units = pd.concat(parts, ignore_index=True)
    units = units[units["paper_id"].isin(set(order_ids))].reset_index(drop=True)
    U = embed_sbert(units["text"].astype(str).tolist())[0]
    pid = units["paper_id"].to_numpy()
    P = np.zeros((len(order_ids), U.shape[1]))
    for i, p in enumerate(order_ids):
        m = pid == p
        if m.any():
            P[i] = U[m].mean(axis=0)
    return normalize(P)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--field", nargs="+", default=["summary_bullets", "highlights"])
    ap.add_argument("--proj", default="pca", choices=["pca", "tsne"])
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--out-dir", default=str(paths.TABLES))
    args = ap.parse_args()

    paths.ensure()
    in_dir, out_dir = Path(args.in_dir), Path(args.out_dir)
    df = pd.read_csv(in_dir / f"clusters_{args.name}.csv")  # paper_id, doi, cluster

    labeled_fp = in_dir / f"cluster_summary_{args.name}_labeled.csv"
    if labeled_fp.exists():
        summ = pd.read_csv(labeled_fp)
        label_map = {int(r["cluster"]): f"{int(r['cluster'])}: {r['theme']}"
                     for _, r in summ.iterrows()}
        print(f"Using LLM theme labels from {labeled_fp.name}")
    else:
        summ = pd.read_csv(in_dir / f"cluster_summary_{args.name}.csv")
        label_map = {int(r["cluster"]): short_label(int(r["cluster"]), r["top_terms"])
                     for _, r in summ.iterrows()}

    order_ids = df["paper_id"].tolist()
    X = paper_vectors(in_dir, args.field, order_ids)

    # --- 1) 2-D paper map ---
    if args.proj == "tsne":
        coords = TSNE(n_components=2, random_state=0, init="pca",
                      perplexity=min(30, max(5, len(order_ids) // 20))).fit_transform(X)
    else:
        coords = PCA(n_components=2, random_state=0).fit_transform(X)
    df["x"], df["y"] = coords[:, 0], coords[:, 1]

    fig, ax = plt.subplots(figsize=(12, 8))
    clusters = sorted(df["cluster"].unique())
    cmap = plt.get_cmap("tab20").resampled(len(clusters))
    for i, c in enumerate(clusters):
        sub = df[df["cluster"] == c]
        ax.scatter(sub["x"], sub["y"], s=16, color=cmap(i), alpha=0.6,
                   label=label_map.get(c, str(c)))
        ax.text(sub["x"].mean(), sub["y"].mean(), str(c), fontsize=10, fontweight="bold",
                ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="0.6", alpha=0.8))
    ax.set_title(f"Paper theme map: {args.name} ({args.proj.upper()}, {len(order_ids)} papers)")
    ax.set_xticks([]); ax.set_yticks([])
    ax.legend(fontsize=6, loc="center left", bbox_to_anchor=(1.0, 0.5), title="theme")
    fig.tight_layout(); fig.savefig(paths.FIGURES / f"fig_map_{args.name}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)

    # --- 2) theme size ---
    sizes = (df.groupby("cluster")["paper_id"].nunique()
             .rename(index=label_map).sort_values())
    fig, ax = plt.subplots(figsize=(10, 0.4 * len(sizes) + 1))
    sizes.plot(kind="barh", ax=ax, color="#4477aa")
    for i, v in enumerate(sizes.values):
        ax.text(v + 0.5, i, str(int(v)), va="center", fontsize=8)
    ax.set_xlabel("Papers"); ax.set_title(f"Theme sizes: {args.name}")
    fig.tight_layout(); fig.savefig(paths.FIGURES / f"fig_themesize_{args.name}.png", dpi=200); plt.close(fig)

    # --- 3) theme x period and theme x region ---
    master = pd.read_csv(in_dir / "papers_master.csv")
    yr = master.set_index("paper_id")["year"]
    df["period"] = df["paper_id"].map(yr).apply(to_period)
    tp = order_cols(pd.crosstab(df["cluster"], df["period"]), PERIOD_ORDER)
    tp.to_csv(out_dir / f"stat_theme_x_period_{args.name}.csv", encoding="utf-8-sig")
    heatmap(tp, f"Theme x period: {args.name}", paths.FIGURES / f"fig_theme_x_period_{args.name}.png", label_map)

    cty = master.set_index("paper_id")["country"]
    reg_rows = []
    for pid in df["paper_id"]:
        for reg in to_region(cty.get(pid)):
            reg_rows.append((pid, reg))
    reg_df = pd.DataFrame(reg_rows, columns=["paper_id", "region"])
    dr = df.merge(reg_df, on="paper_id")
    tr = order_cols(pd.crosstab(dr["cluster"], dr["region"]), REGION_ORDER)
    tr.to_csv(out_dir / f"stat_theme_x_region_{args.name}.csv", encoding="utf-8-sig")
    heatmap(tr, f"Theme x region: {args.name}", paths.FIGURES / f"fig_theme_x_region_{args.name}.png", label_map)

    print(f"Wrote fig_map / fig_themesize / fig_theme_x_period / fig_theme_x_region for '{args.name}'")
    # (macro-tier geography: temporal / regional / US-state -> 11_macro_geography.py)


if __name__ == "__main__":
    main()
