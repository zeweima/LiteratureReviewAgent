"""
Flatten per-paper LLM extractions into tidy tables for exploratory analysis.

Reads every ``merged_extraction.json`` under ``RESULTS_DIR`` (one folder per DOI)
and writes:

1. ``papers_master.csv`` -- ONE ROW PER PAPER. All scalar fields, plus list
   fields collapsed into ``"; "``-joined strings and ``n_*`` count columns.
   This is the substrate for quick filtering / group-bys / pivot tables.

2. ``long_*.csv`` -- ONE ROW PER LIST ITEM (tidy/long format), keyed by
   ``paper_id`` + ``doi``. Use these for value-counts and co-occurrence analysis
   (e.g. drainage_practices frequency, keyword clustering, method landscape).

Notes
-----
* This script only *reshapes* the data. It performs NO analysis and NO unit
  normalization. ``long_quantitative_stats.csv`` is emitted as a raw table for
  later work -- nothing is aggregated here (per current-stage decision).
* All 100 papers are kept regardless of relevance, with ``relevance_level`` as a
  column so downstream code can filter (e.g. Tile-centered + Tile-related).

Usage
-----
    python 3_flatten_extractions.py
    python 3_flatten_extractions.py --results-dir sample/results --out-dir sample/analysis
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import paths


# --------------------------------------------------------------------------- #
# Config: how each field maps into the flattened outputs.
# --------------------------------------------------------------------------- #

LIST_JOIN = "; "  # separator used when collapsing a list into one master cell


def _get(d, *path, default=None):
    """Safely walk nested dicts, treating None sections as empty."""
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _join(values):
    """Collapse a list into a single ``"; "``-joined string (None -> '')."""
    if not values:
        return ""
    return LIST_JOIN.join(str(v) for v in values if v is not None)


def _n(values):
    """Length of a list field (0 if None/missing)."""
    return len(values) if isinstance(values, list) else 0


# Long tables built from a list-of-strings at a given path.
# (table_name, (path...))
SCALAR_LIST_TABLES = [
    ("long_study_type",        ("metadata", "study_type")),
    ("long_keywords",          ("key_words",)),
    ("long_research_gap",      ("context", "research_gap")),
    ("long_hypotheses",        ("context", "hypotheses")),
    ("long_approach_types",    ("methodology", "approach_types")),
    ("long_physical_models",   ("methodology", "physical_models")),
    ("long_ml_models",         ("methodology", "ml_models")),
    ("long_analytical_methods",("methodology", "analytical_methods")),
    ("long_key_assumptions",   ("methodology", "key_assumptions")),
    ("long_drainage_practices",("tile_design", "drainage_practices")),
    ("long_associated_practices", ("tile_design", "associated_management_practices")),
    ("long_other_practices",   ("tile_design", "other_practices")),
    ("long_summary_bullets",   ("results", "summary_bullets")),
    ("long_highlights",        ("internal_evaluation", "highlights")),
    ("long_limitations",       ("internal_evaluation", "limitations")),
    ("long_future_directions", ("internal_evaluation", "future_directions")),
]


def build_master_row(paper_id: str, rec: dict) -> dict:
    """Flatten one extraction record into a single wide row."""
    bib = rec.get("bib_metadata") or {}
    return {
        # --- identity ---
        "paper_id": paper_id,
        "doi": bib.get("doi") or _get(rec, "metadata", "title"),
        "year": bib.get("year"),
        "journal": bib.get("secondary_title"),
        "authors": _join(bib.get("authors")),
        "bib_title": bib.get("title"),
        "extracted_title": _get(rec, "metadata", "title"),

        # --- context (scalar) ---
        "scientific_question": _get(rec, "context", "scientific_question"),

        # --- relevance ---
        "relevance_level": _get(rec, "relevance", "level"),
        "is_primary": _get(rec, "relevance", "is_primary"),
        "relevance_justification": _get(rec, "relevance", "justification"),

        # --- location ---
        "country": _get(rec, "metadata", "location", "country"),
        "state_province": _get(rec, "metadata", "location", "state_province"),
        "county": _get(rec, "metadata", "location", "county"),
        "watershed": _get(rec, "metadata", "location", "watershed"),
        "coordinates_text": _get(rec, "metadata", "location", "coordinates_text"),
        "spatial_extent": _get(rec, "metadata", "location", "spatial_extent"),

        # --- study type (multilabel) ---
        "study_type": _join(_get(rec, "metadata", "study_type")),

        # --- scale ---
        "scale_category": _get(rec, "scale", "category"),
        "total_area_value": _get(rec, "scale", "total_area_value"),
        "total_area_unit": _get(rec, "scale", "total_area_unit"),
        "area_description": _get(rec, "scale", "area_description"),
        "site_count": _get(rec, "scale", "site_count"),
        "temporal_resolution": _get(rec, "scale", "temporal_resolution"),

        # --- methodology ---
        "study_duration": _get(rec, "methodology", "study_duration"),
        "approach_types": _join(_get(rec, "methodology", "approach_types")),
        "physical_models": _join(_get(rec, "methodology", "physical_models")),
        "ml_models": _join(_get(rec, "methodology", "ml_models")),
        "analytical_methods": _join(_get(rec, "methodology", "analytical_methods")),

        # --- tile design (scalars) ---
        "tile_depth": _get(rec, "tile_design", "depth"),
        "tile_spacing": _get(rec, "tile_design", "spacing"),
        "pipe_diameter": _get(rec, "tile_design", "pipe_diameter"),
        "pipe_material": _get(rec, "tile_design", "pipe_material"),
        "drainage_coefficient": _get(rec, "tile_design", "drainage_coefficient"),
        "soil_texture": _get(rec, "tile_design", "soil_texture"),
        "drainage_practices": _join(_get(rec, "tile_design", "drainage_practices")),
        "associated_management_practices": _join(
            _get(rec, "tile_design", "associated_management_practices")
        ),
        "other_practices": _join(_get(rec, "tile_design", "other_practices")),

        # --- synthesis ---
        "main_contribution": _get(rec, "overall_synthesis", "main_contribution"),
        "executive_summary": _get(rec, "overall_synthesis", "executive_summary"),

        # --- keywords (collapsed) ---
        "key_words": _join(rec.get("key_words")),

        # --- counts (handy for completeness / exploration) ---
        "n_keywords": _n(rec.get("key_words")),
        "n_research_gap": _n(_get(rec, "context", "research_gap")),
        "has_scientific_question": _get(rec, "context", "scientific_question") is not None,
        "n_objectives": _n(_get(rec, "context", "objectives")),
        "n_summary_bullets": _n(_get(rec, "results", "summary_bullets")),
        "n_quantitative_stats": _n(_get(rec, "results", "quantitative_stats")),
        "n_limitations": _n(_get(rec, "internal_evaluation", "limitations")),
        "n_future_directions": _n(_get(rec, "internal_evaluation", "future_directions")),
    }


def build_long_rows(paper_id: str, doi: str, rec: dict, path) -> list[dict]:
    """One row per item for a list-of-strings field."""
    values = _get(rec, *path)
    if not isinstance(values, list):
        return []
    field = path[-1]
    return [
        {"paper_id": paper_id, "doi": doi, field: v}
        for v in values
        if v is not None
    ]


def build_quant_stats_rows(paper_id: str, doi: str, rec: dict) -> list[dict]:
    """One row per quantitative_stats entry. Raw table only -- no analysis."""
    stats = _get(rec, "results", "quantitative_stats")
    if not isinstance(stats, list):
        return []
    rows = []
    for s in stats:
        s = s or {}
        rows.append({
            "paper_id": paper_id,
            "doi": doi,
            "variable": s.get("variable"),
            "value": s.get("value"),
            "unit": s.get("unit"),
            "context": s.get("context"),
        })
    return rows


def build_research_aims_rows(paper_id: str, doi: str, rec: dict) -> list[dict]:
    """Combine scientific_question + objectives into one tidy 'research aims' table.

    ``scientific_question`` is a single string (the broad 'why' framing); ``objectives``
    is a list (the concrete 'we will measure/test' aims). They are stacked here with an
    ``aim_type`` discriminator so a study contributing only one of the two is preserved.
    """
    rows = []
    sq = _get(rec, "context", "scientific_question")
    if sq is not None:
        rows.append({
            "paper_id": paper_id, "doi": doi,
            "aim_type": "scientific_question", "text": sq,
        })
    for obj in _get(rec, "context", "objectives") or []:
        if obj is not None:
            rows.append({
                "paper_id": paper_id, "doi": doi,
                "aim_type": "objective", "text": obj,
            })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=str(paths.RESULTS),
                        help="Folder with one subfolder per DOI containing merged_extraction.json")
    parser.add_argument("--out-dir", default=str(paths.TABLES),
                        help="Output folder for the flattened CSVs (tabular data)")
    args = parser.parse_args()

    paths.ensure()
    results_dir = Path(args.results_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paper_dirs = sorted(p for p in results_dir.iterdir() if p.is_dir())
    print(f"Found {len(paper_dirs)} paper folders under {results_dir}")

    master_rows = []
    long_rows = {name: [] for name, _ in SCALAR_LIST_TABLES}
    quant_rows = []
    aim_rows = []
    skipped = []

    for pdir in paper_dirs:
        fp = pdir / "merged_extraction.json"
        if not fp.exists():
            skipped.append(pdir.name)
            continue
        with open(fp, encoding="utf-8") as f:
            rec = json.load(f)

        paper_id = pdir.name
        doi = (rec.get("bib_metadata") or {}).get("doi")

        master_rows.append(build_master_row(paper_id, rec))
        for name, path in SCALAR_LIST_TABLES:
            long_rows[name].extend(build_long_rows(paper_id, doi, rec, path))
        quant_rows.extend(build_quant_stats_rows(paper_id, doi, rec))
        aim_rows.extend(build_research_aims_rows(paper_id, doi, rec))

    if skipped:
        print(f"WARNING: {len(skipped)} folders had no merged_extraction.json: {skipped}")

    # --- write master ---
    master_df = pd.DataFrame(master_rows)
    master_path = out_dir / "papers_master.csv"
    master_df.to_csv(master_path, index=False, encoding="utf-8-sig")
    print(f"  wrote {master_path}  ({master_df.shape[0]} rows x {master_df.shape[1]} cols)")

    # --- write long tables ---
    for name, _ in SCALAR_LIST_TABLES:
        df = pd.DataFrame(long_rows[name])
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  wrote {path}  ({len(df)} rows)")

    # --- write combined research aims (scientific_question + objectives) ---
    aims_df = pd.DataFrame(aim_rows)
    aims_path = out_dir / "long_research_aims.csv"
    aims_df.to_csv(aims_path, index=False, encoding="utf-8-sig")
    n_sq = int((aims_df["aim_type"] == "scientific_question").sum()) if len(aims_df) else 0
    n_obj = int((aims_df["aim_type"] == "objective").sum()) if len(aims_df) else 0
    print(f"  wrote {aims_path}  ({len(aims_df)} rows: {n_sq} scientific_question, {n_obj} objective)")

    # --- write quantitative stats (raw, no analysis) ---
    quant_df = pd.DataFrame(quant_rows)
    quant_path = out_dir / "long_quantitative_stats.csv"
    quant_df.to_csv(quant_path, index=False, encoding="utf-8-sig")
    print(f"  wrote {quant_path}  ({len(quant_df)} rows)  [raw -- no analysis]")

    print("\nDone. Master table + long tables ready for exploration in sample/analysis/.")


if __name__ == "__main__":
    main()
