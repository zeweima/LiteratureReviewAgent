"""Theme hierarchy tables: papers per macro (large) and fine (small) theme.

Joins the labeled fine-theme summaries to their macro parents for both content
tracks and emits one tidy table plus a ready-to-paste markdown rendering.

Inputs (from 7_label_clusters.py):
    tables/cluster_summary_<name>_labeled.csv   -- fine themes, n_papers, macro_cluster
    tables/cluster_summary_macro_<name>.csv     -- macro themes, n_papers, n_fine
    tables/clusters_<name>.csv                  -- paper -> cluster assignment

Outputs:
    tables/stat_theme_hierarchy.csv   -- one row per fine theme, both tracks
    docs/theme_tables.md              -- markdown tables for the manuscript

Usage:
    python 10_theme_tables.py
"""

from __future__ import annotations

import pandas as pd

import paths

TRACKS = {
    "knowledge": "summary_bullets-highlights",
    "frontier": "future_directions",
}


def _load(track: str, name: str) -> pd.DataFrame:
    fine = pd.read_csv(paths.TABLES / f"cluster_summary_{name}_labeled.csv")
    macro = pd.read_csv(paths.TABLES / f"cluster_summary_macro_{name}.csv")
    assign = pd.read_csv(paths.TABLES / f"clusters_{name}.csv")

    total = assign["paper_id"].nunique()
    macro_n = macro.set_index("macro_cluster")["n_papers"].to_dict()

    df = fine[["macro_cluster", "macro_theme", "cluster", "theme", "n_papers", "n_units"]].copy()
    df["track"] = track
    df["macro_n_papers"] = df["macro_cluster"].map(macro_n)
    df["share_of_track"] = (df["n_papers"] / total).round(4)

    # largest macro group first, then largest fine theme within it
    df = df.sort_values(
        ["macro_n_papers", "macro_cluster", "n_papers"], ascending=[False, True, False]
    )
    return df[
        [
            "track", "macro_cluster", "macro_theme", "macro_n_papers",
            "cluster", "theme", "n_papers", "n_units", "share_of_track",
        ]
    ]


def _markdown(df: pd.DataFrame, track: str, total: int) -> list[str]:
    lines = [
        f"### {track.capitalize()} track "
        f"({df['macro_cluster'].nunique()} macro themes, {len(df)} fine themes, "
        f"{total} papers)",
        "",
        "| Macro theme (papers) | Fine theme | Papers | Share |",
        "|---|---|---:|---:|",
    ]
    for macro, grp in df.groupby("macro_cluster", sort=False):
        head = f"**{grp['macro_theme'].iloc[0]}** ({int(grp['macro_n_papers'].iloc[0])})"
        for i, (_, r) in enumerate(grp.iterrows()):
            lines.append(
                f"| {head if i == 0 else ''} | {r['theme']} | {int(r['n_papers'])} "
                f"| {r['share_of_track']:.1%} |"
            )
    lines.append("")
    return lines


def build() -> dict:
    paths.ensure()

    frames, md, stats = [], ["# Theme hierarchy — papers per macro and fine theme", ""], {}
    for track, name in TRACKS.items():
        df = _load(track, name)
        total = pd.read_csv(paths.TABLES / f"clusters_{name}.csv")["paper_id"].nunique()
        frames.append(df)
        md += _markdown(df, track, total)
        stats[track] = {
            "papers": total,
            "macro_themes": int(df["macro_cluster"].nunique()),
            "fine_themes": len(df),
            "fine_sum": int(df["n_papers"].sum()),
            "largest_fine": f"{df.loc[df['n_papers'].idxmax(), 'theme']} "
                            f"({int(df['n_papers'].max())})",
        }

    out = pd.concat(frames, ignore_index=True)
    out.to_csv(paths.TABLES / "stat_theme_hierarchy.csv", index=False, encoding="utf-8-sig")
    (paths.DOCS / "theme_tables.md").write_text("\n".join(md), encoding="utf-8")
    return stats


if __name__ == "__main__":
    for k, v in build().items():
        print(k, v)
