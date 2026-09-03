"""
Phase 4 step 1: cluster papers into candidate themes (deterministic, offline).

Scales the sample-stage bullet clustering to the full corpus by clustering at the
PAPER level instead of the individual-sentence level:

  1. embed every text unit (summary_bullets + highlights) with sbert  (~16k units)
  2. mean-pool each paper's unit embeddings -> ONE vector per paper (~1.4k)
  3. Ward-cluster the paper vectors into k themes

Clustering ~1.4k paper vectors is tractable (Ward is O(n^2)); clustering ~16k
sentences would not be. Paper-level themes are also the right unit for the
downstream codebook / synthesis (one dominant theme per paper).

Writes:
  * clusters_<label>.csv         -- one row per paper: paper_id, doi, cluster
  * cluster_summary_<label>.csv  -- one row per cluster: n_papers, top terms,
                                    representative sentences, example DOIs
                                    (input for SDK theme-labeling + synthesis)

Usage:
    python 6_topic_clusters.py --field summary_bullets highlights --k 20
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize

from topic_cluster import cluster_texts, embed_sbert, embed_tfidf, top_terms
import paths

FIELDS = {
    "summary_bullets":   ("long_summary_bullets.csv", "summary_bullets"),
    "highlights":        ("long_highlights.csv", "highlights"),
    "future_directions": ("long_future_directions.csv", "future_directions"),
    "limitations":       ("long_limitations.csv", "limitations"),
    "research_gap":      ("long_research_gap.csv", "research_gap"),
}
TILE_RELEVANCE = {"Tile-centered", "Tile-related"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--field", required=True, nargs="+", choices=list(FIELDS),
                    help="one or more fields pooled per paper")
    ap.add_argument("--k", type=int, default=20, help="fine themes")
    ap.add_argument("--macro-k", type=int, default=6,
                    help="macro super-themes (fine themes grouped by centroid); 0 disables")
    ap.add_argument("--backend", default="sbert", choices=["tfidf", "sbert"])
    ap.add_argument("--name", default=None)
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--out-dir", default=str(paths.TABLES))
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    paths.ensure()
    in_dir, out_dir = Path(args.in_dir), Path(args.out_dir)
    label = args.name or "-".join(args.field)

    # ---- pool the requested fields into one long table of text units ----------
    parts = []
    for fld in args.field:
        fname, col = FIELDS[fld]
        d = pd.read_csv(in_dir / fname).dropna(subset=[col])
        d = d.rename(columns={col: "text"})[["paper_id", "doi", "text"]]
        d["source_field"] = fld
        parts.append(d)
    units = pd.concat(parts, ignore_index=True)

    if not args.all:
        master = pd.read_csv(in_dir / "papers_master.csv")
        ids = set(master.loc[master["relevance_level"].isin(TILE_RELEVANCE), "paper_id"])
        units = units[units["paper_id"].isin(ids)]
    units = units.reset_index(drop=True)
    texts = units["text"].astype(str).tolist()

    print(f"Fields {args.field} -> '{label}': {len(texts)} text units, "
          f"{units['paper_id'].nunique()} papers, k={args.k}, backend={args.backend}")
    print("  per-field:", units["source_field"].value_counts().to_dict())

    # ---- embed units, then mean-pool to one vector per paper ------------------
    if args.backend == "sbert":
        U, _ = embed_sbert(texts)
    else:
        U, _ = embed_tfidf(texts)

    paper_ids = units["paper_id"].to_numpy()
    uniq = pd.Index(pd.unique(paper_ids))
    dim = U.shape[1]
    P = np.zeros((len(uniq), dim), dtype=float)
    for i, pid in enumerate(uniq):
        P[i] = U[paper_ids == pid].mean(axis=0)
    P = normalize(P)  # re-normalize the mean-pooled vectors

    # ---- cluster paper vectors ------------------------------------------------
    plabels = cluster_texts(P, args.k)
    paper_cluster = dict(zip(uniq, plabels))
    units["cluster"] = units["paper_id"].map(paper_cluster)

    # ---- macro tier: group the FINE-theme centroids into a few super-themes ----
    # Nested by construction (macro-themes are unions of fine themes), so the fine
    # partition -- and the downstream SDK label/synthesis caches -- are untouched.
    # This is the two-level Ward scheme motivated by the k-diagnostic: the merge
    # gaps favour ~6 macro-themes, the fine sweep ~20 detailed themes.
    macro_of_fine = {}
    if args.macro_k and args.macro_k >= 2:
        fine_ids = sorted(set(plabels))
        cents = normalize(np.vstack([P[plabels == c].mean(axis=0) for c in fine_ids]))
        macro_lab = cluster_texts(cents, args.macro_k)
        macro_of_fine = dict(zip(fine_ids, macro_lab))
        units["macro_cluster"] = units["cluster"].map(macro_of_fine)

    doi_map = units.drop_duplicates("paper_id").set_index("paper_id")["doi"]
    clusters_df = pd.DataFrame({"paper_id": uniq,
                                "doi": [doi_map.get(p) for p in uniq],
                                "cluster": plabels})
    if macro_of_fine:
        clusters_df["macro_cluster"] = clusters_df["cluster"].map(macro_of_fine)
    clusters_df.to_csv(out_dir / f"clusters_{label}.csv", index=False, encoding="utf-8-sig")

    # ---- TF-IDF labels from per-paper concatenated documents ------------------
    paper_docs = (units.groupby("paper_id")["text"]
                  .apply(lambda s: " ".join(s.astype(str))).reindex(uniq).tolist())
    Xt, vec = embed_tfidf(paper_docs)
    terms = top_terms(Xt, plabels, vec)

    # ---- representative sentences: units closest to each cluster's centroid ---
    rows = []
    for c in sorted(set(plabels)):
        mask_u = units["cluster"].to_numpy() == c
        idx = np.where(mask_u)[0]
        centroid = U[idx].mean(axis=0, keepdims=True)
        sims = cosine_similarity(U[idx], centroid).ravel()
        best = idx[np.argsort(sims)[::-1]]
        reps, seen = [], set()
        for j in best:                      # de-dup near-identical reps by paper
            pid = units.iloc[j]["paper_id"]
            if pid in seen:
                continue
            seen.add(pid)
            reps.append(units.iloc[j]["text"])
            if len(reps) == 3:
                break
        sub_papers = clusters_df[clusters_df["cluster"] == c]
        rows.append({
            "cluster": c,
            "macro_cluster": macro_of_fine.get(c) if macro_of_fine else None,
            "n_papers": len(sub_papers),
            "n_units": int(mask_u.sum()),
            "top_terms": ", ".join(terms.get(c, [])),
            "example_dois": "; ".join(sub_papers["doi"].dropna().unique()[:6]),
            "representative_1": reps[0] if len(reps) > 0 else "",
            "representative_2": reps[1] if len(reps) > 1 else "",
            "representative_3": reps[2] if len(reps) > 2 else "",
        })
    summary = pd.DataFrame(rows).sort_values("n_papers", ascending=False)
    summary.to_csv(out_dir / f"cluster_summary_{label}.csv", index=False, encoding="utf-8-sig")

    print(f"\nWrote clusters_{label}.csv and cluster_summary_{label}.csv")
    if macro_of_fine:
        n_macro = len(set(macro_of_fine.values()))
        print(f"{len(set(plabels))} fine themes grouped into {n_macro} macro-themes. Preview by macro:")
        for mc in sorted(set(macro_of_fine.values())):
            fine_here = summary[summary["macro_cluster"] == mc]
            print(f"  MACRO {mc}  ({fine_here['n_papers'].sum()} papers, {len(fine_here)} fine):")
            for _, r in fine_here.iterrows():
                print(f"     [{r['cluster']:>2}] n={r['n_papers']:>3}  {r['top_terms']}")
    else:
        print(f"{len(set(plabels))} clusters (paper-level). Preview:")
        for _, r in summary.iterrows():
            print(f"  [{r['cluster']:>2}] papers={r['n_papers']:>3}  {r['top_terms']}")


if __name__ == "__main__":
    main()
