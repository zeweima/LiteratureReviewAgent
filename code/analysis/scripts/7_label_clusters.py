"""
Phase 4 step 2: turn numeric clusters into named themes via the Claude Code SDK.

Reads cluster_summary_<name>.csv, asks Claude for a theme name + short description
per cluster (grounded in the cluster's characteristic terms + representative
statements), and writes cluster_summary_<name>_labeled.csv with `theme` and
`description` columns. The viz + synthesis steps prefer `theme` over keywords.

Payloads are built from the representative sentences already stored in the
summary (paper-level clustering keeps the cluster file to paper_id/doi/cluster,
so representatives live in the summary, not the member table).

Usage:
    python 7_label_clusters.py --name summary_bullets-highlights
    python 7_label_clusters.py --name future_directions --model sonnet
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from cluster_label import run, run_macro
import paths


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--out-dir", default=str(paths.TABLES))
    args = ap.parse_args()

    paths.ensure()
    in_dir, out_dir = Path(args.in_dir), Path(args.out_dir)
    summ = pd.read_csv(in_dir / f"cluster_summary_{args.name}.csv")

    clusters = []
    for _, r in summ.iterrows():
        reps = [r.get(f"representative_{i}") for i in (1, 2, 3)]
        bullets = [str(x) for x in reps if isinstance(x, str) and x.strip()]
        clusters.append({"cluster": int(r["cluster"]),
                         "top_terms": str(r["top_terms"]),
                         "bullets": bullets})

    print(f"Labeling {len(clusters)} clusters for '{args.name}' with model={args.model} ...")
    labeled = run(clusters, args.model, cache_dir=paths.LABEL_CACHE)

    theme_map = {d["cluster"]: {"theme": d["theme"], "description": d["description"]}
                 for d in labeled}
    summ["theme"] = summ["cluster"].map(lambda c: theme_map.get(int(c), {}).get("theme", ""))
    summ["description"] = summ["cluster"].map(lambda c: theme_map.get(int(c), {}).get("description", ""))

    front = ["cluster", "theme", "n_papers", "description", "top_terms"]
    cols = [c for c in front if c in summ.columns]
    cols += [c for c in summ.columns if c not in cols]
    summ = summ[cols]
    out_fp = out_dir / f"cluster_summary_{args.name}_labeled.csv"
    summ.to_csv(out_fp, index=False, encoding="utf-8-sig")
    print(f"\nWrote {out_fp}")
    print(summ[["cluster", "n_papers", "theme"]].to_string(index=False))

    # ---- macro tier: name the umbrella themes from their member fine themes ----
    if "macro_cluster" in summ.columns and summ["macro_cluster"].notna().any():
        macros = []
        for mc, g in summ.groupby("macro_cluster"):
            g = g.sort_values("n_papers", ascending=False)
            subthemes = [f"{r['theme']} -- {r['top_terms']} ({int(r['n_papers'])} papers)"
                         for _, r in g.iterrows()]
            macros.append({"macro_cluster": int(mc), "subthemes": subthemes,
                           "n_papers": int(g["n_papers"].sum()),
                           "n_fine": int(len(g)),
                           "member_themes": "; ".join(g["theme"])})
        print(f"\nLabeling {len(macros)} macro-themes ...")
        labeled_macro = run_macro(macros, args.model, cache_dir=paths.LABEL_CACHE)
        mdf = pd.DataFrame([{"macro_cluster": d["macro_cluster"],
                             "macro_theme": d["theme"],
                             "macro_description": d["description"],
                             "n_papers": d["n_papers"], "n_fine": d["n_fine"],
                             "member_themes": d["member_themes"]}
                            for d in labeled_macro]).sort_values("n_papers", ascending=False)
        macro_fp = out_dir / f"cluster_summary_macro_{args.name}.csv"
        mdf.to_csv(macro_fp, index=False, encoding="utf-8-sig")
        # also write the macro name back onto the fine labeled table
        name_map = dict(zip(mdf["macro_cluster"], mdf["macro_theme"]))
        summ["macro_theme"] = summ["macro_cluster"].map(name_map)
        summ.to_csv(out_fp, index=False, encoding="utf-8-sig")
        print(f"Wrote {macro_fp}")
        print(mdf[["macro_cluster", "n_papers", "n_fine", "macro_theme"]].to_string(index=False))


if __name__ == "__main__":
    main()
