"""Corpus overview: bibliographic records, full-text acquisition, and publication years.

Reports the corpus funnel -- how many bibliographic records the Web of Science
search returned, how many of those have a full-text PDF, how many were extracted,
and how many survive relevance filtering -- and the distribution of publications
per year at each of those stages.

Inputs (read-only):
    Data/Bib/WOS_core/all_ris.json       -- every exported WoS record
    Data/Bib/WOS_core/with_pdf_ris.json  -- the subset for which a PDF was obtained
    code/analysis/tables/papers_master.csv

Outputs:
    tables/stat_corpus_overview.csv   -- one row per funnel stage
    tables/stat_papers_per_year.csv   -- one row per publication year
    figures/fig_papers_per_year.png   -- stacked bar, records per year by stage
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

import paths

BIB = paths.BASE.parent.parent / "Data" / "Bib" / "WOS_core"

IN_SCOPE = ("Tile-centered", "Tile-related")

# in-scope, extracted but filtered out, no full text
COLORS = ("#4477aa", "#a8c4de", "#cccccc")


def _norm_doi(v) -> str:
    return str(v).strip().lower() if pd.notna(v) else ""


def _load_ris(name: str) -> pd.DataFrame:
    records = json.loads((BIB / name).read_text(encoding="utf-8"))
    df = pd.DataFrame(
        {
            "doi": [_norm_doi(r.get("doi")) for r in records],
            "year": [r.get("year") for r in records],
        }
    )
    df["year"] = pd.to_numeric(df["year"].astype(str).str[:4], errors="coerce")
    return df


def build(root=None) -> dict:
    paths.ensure()

    all_ris = _load_ris("all_ris.json")
    pdf_ris = _load_ris("with_pdf_ris.json")
    master = pd.read_csv(paths.TABLES / "papers_master.csv", low_memory=False)
    master["doi"] = master["doi"].map(_norm_doi)

    pdf_dois = set(pdf_ris["doi"]) - {""}
    in_scope_dois = set(master.loc[master["relevance_level"].isin(IN_SCOPE), "doi"]) - {""}
    extracted_dois = set(master["doi"]) - {""}

    # every record is assigned exactly one stage, so the stacked bars sum to the total
    def stage(row) -> str:
        if row["doi"] and row["doi"] in in_scope_dois:
            return "in_scope"
        if row["doi"] and row["doi"] in pdf_dois:
            return "filtered_out"
        return "no_full_text"

    all_ris["stage"] = all_ris.apply(stage, axis=1)

    overview = pd.DataFrame(
        [
            ("Bibliographic records retrieved (Web of Science)", len(all_ris)),
            ("Records carrying a DOI", int((all_ris["doi"] != "").sum())),
            ("Records with a full-text PDF obtained", len(pdf_ris)),
            ("Unique DOIs with a full-text PDF", len(pdf_dois)),
            ("Papers converted and extracted", len(extracted_dois)),
            ("Papers retained after relevance filtering", len(in_scope_dois)),
        ],
        columns=["stage", "n"],
    )
    overview["share_of_records"] = (overview["n"] / len(all_ris)).round(4)

    per_year = (
        all_ris.dropna(subset=["year"])
        .astype({"year": int})
        .pivot_table(index="year", columns="stage", aggfunc="size", fill_value=0)
        .reindex(columns=["in_scope", "filtered_out", "no_full_text"], fill_value=0)
        .sort_index()
    )
    per_year = per_year.reindex(range(per_year.index.min(), per_year.index.max() + 1), fill_value=0)
    per_year.index.name = "year"
    per_year["total"] = per_year.sum(axis=1)

    overview.to_csv(paths.TABLES / "stat_corpus_overview.csv", index=False, encoding="utf-8-sig")
    per_year.to_csv(paths.TABLES / "stat_papers_per_year.csv", encoding="utf-8-sig")

    fig, ax = plt.subplots(figsize=(11, 4.2))
    bottom = pd.Series(0, index=per_year.index, dtype=float)
    labels = {
        "in_scope": "Retained after relevance filtering",
        "filtered_out": "Full text obtained, filtered out",
        "no_full_text": "Metadata only (no full text)",
    }
    for col, color in zip(["in_scope", "filtered_out", "no_full_text"], COLORS):
        ax.bar(per_year.index, per_year[col], bottom=bottom, color=color, width=0.85, label=labels[col])
        bottom += per_year[col]

    ax.set_xlabel("Publication year")
    ax.set_ylabel("Number of papers")
    ax.set_xlim(per_year.index.min() - 1, per_year.index.max() + 1)
    ax.legend(frameon=False, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e6e6e6", zorder=0)
    ax.set_axisbelow(True)

    fig.savefig(paths.FIGURES / "fig_papers_per_year.png", dpi=200, bbox_inches="tight")
    plt.close(fig)

    return {
        "records_total": len(all_ris),
        "records_with_doi": int((all_ris["doi"] != "").sum()),
        "records_with_pdf": len(pdf_ris),
        "unique_dois_with_pdf": len(pdf_dois),
        "papers_extracted": len(extracted_dois),
        "papers_in_scope": len(in_scope_dois),
        "year_min": int(per_year.index.min()),
        "year_max": int(per_year.index.max()),
        "peak_year": int(per_year["total"].idxmax()),
        "peak_year_n": int(per_year["total"].max()),
    }


if __name__ == "__main__":
    print(build())
