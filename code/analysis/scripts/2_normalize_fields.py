"""
Apply scalable normalizers to the flattened long tables.

Reads the raw ``long_*`` / ``papers_master`` CSVs and writes *normalized*
companions, leaving the raw tables untouched. Also emits a frequency-sorted
``review_*`` queue for any term the family registry could not place -- the only
artifact a human needs to look at when the corpus grows.

Outputs (in --out-dir):
  * long_physical_models_normalized.csv  (raw, canonical, family, match_method)
  * review_physical_models.csv           (unmatched terms, by frequency)
  * papers_master_normalized.csv         (master + parsed numeric / temporal cols)

Usage:
    python 5_normalize_fields.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

import paths
from normalizers import (
    area_to_km2,
    normalize_model_series,
    parse_lengths_m,
    parse_year_span,
    review_queue,
    temporal_bucket,
)


def normalize_models(in_dir: Path, out_dir: Path) -> pd.DataFrame:
    src = in_dir / "long_physical_models.csv"
    df = pd.read_csv(src)
    mapping = normalize_model_series(df["physical_models"])
    out = pd.concat([df[["paper_id", "doi"]].reset_index(drop=True), mapping], axis=1)
    out = out.rename(columns={"raw": "physical_model_raw"})
    out.to_csv(out_dir / "long_physical_models_normalized.csv", index=False, encoding="utf-8-sig")

    rq = review_queue(mapping)
    rq.to_csv(paths.INTERNAL / "review_physical_models.csv", index=False, encoding="utf-8-sig")

    n = len(out)
    matched = int(out["family"].notna().sum())
    n_model = int((out["kind"] == "model").sum())
    n_eq = int((out["kind"] == "equation").sum())
    print(f"physical_models: {n} rows, {matched} mapped ({matched / n:.0%}) "
          f"-> {n_model} model, {n_eq} equation; {n - matched} unmatched -> review queue")
    print("\nTop model families (kind='model', the focus):")
    models = out[out["kind"] == "model"]
    print(models.groupby("family")["paper_id"]
          .nunique().sort_values(ascending=False).head(15).to_string())
    if len(rq):
        print(f"\nReview queue (top unmatched): {rq['raw'].head(8).tolist()}")
    return out


def normalize_master(in_dir: Path, out_dir: Path) -> pd.DataFrame:
    m = pd.read_csv(in_dir / "papers_master.csv")

    # Tile depth & spacing: keep the structure (no averaging). Per-paper summary
    # columns (min/max/n/type/values) on the master, plus one tidy row per
    # individual value in long_tile_dimensions_normalized.csv.
    # plausible metre bounds per field; out-of-range values (parsed years, plot
    # sizes, mis-scaled mixed units) are FLAGGED, not dropped -- kept in the tidy
    # table for audit, excluded from the per-paper summary + analysis.
    BOUNDS = {"depth": (0.05, 5.0), "spacing": (0.5, 300.0)}
    dim_rows = []
    for field, prefix in (("tile_depth", "depth"), ("tile_spacing", "spacing")):
        parsed = m[field].apply(parse_lengths_m)
        lo, hi = BOUNDS[prefix]
        vtypes, vstr, vmin, vmax, vn = [], [], [], [], []
        for pid, doi, raw, d in zip(m["paper_id"], m["doi"], m[field], parsed):
            vt = d["value_type"]
            plaus = []
            for i, v in enumerate(d["values"]):
                ok = lo <= v <= hi
                dim_rows.append({"paper_id": pid, "doi": doi, "field": prefix,
                                 "raw_value": raw, "value_m": v,
                                 "value_type": vt, "value_index": i, "plausible": ok})
                if ok:
                    plaus.append(v)
            vtypes.append(vt)
            vstr.append("; ".join(f"{v:g}" for v in plaus))
            vmin.append(min(plaus) if plaus else None)
            vmax.append(max(plaus) if plaus else None)
            vn.append(len(plaus))
        m[f"{prefix}_value_type"] = vtypes
        m[f"{prefix}_values_m"] = vstr
        m[f"{prefix}_min_m"] = vmin
        m[f"{prefix}_max_m"] = vmax
        m[f"{prefix}_n_values"] = vn

    span = m["study_duration"].apply(parse_year_span).apply(pd.Series)
    m["area_km2"] = [area_to_km2(v, u) for v, u in zip(m["total_area_value"], m["total_area_unit"])]
    m["temporal_bucket"] = m["temporal_resolution"].apply(temporal_bucket)

    out = pd.concat([m, span], axis=1)
    out.to_csv(out_dir / "papers_master_normalized.csv", index=False, encoding="utf-8-sig")

    dim = pd.DataFrame(dim_rows, columns=["paper_id", "doi", "field", "raw_value",
                                          "value_m", "value_type", "value_index",
                                          "plausible"])
    dim.to_csv(out_dir / "long_tile_dimensions_normalized.csv",
               index=False, encoding="utf-8-sig")

    print("\npapers_master: depth/spacing (min/max/n/type/values, NO mean), "
          "area_km2, start/end/span_years, temporal_bucket")
    print(f"  tile depth parsed:   {int((m['depth_n_values'] > 0).sum())} papers")
    print(f"  tile spacing parsed: {int((m['spacing_n_values'] > 0).sum())} papers")
    print(f"  area harmonized:     {int(out['area_km2'].notna().sum())} papers")
    print(f"  duration parsed:     {int(out['span_years'].notna().sum())} papers")
    vt = dim["value_type"].value_counts()
    n_flag = int((~dim["plausible"]).sum())
    print(f"  long_tile_dimensions_normalized.csv: {len(dim)} individual values "
          f"(rows by type: {vt.to_dict()}); {n_flag} flagged out-of-range")
    print("  temporal buckets:")
    print(out["temporal_bucket"].value_counts(dropna=False).to_string())
    return out


def write_audit(models: pd.DataFrame, master: pd.DataFrame) -> None:
    """Per-paper, per-category checking table: doi, original_value, normalized_value.

    One tidy long file (internal/audit_normalization.csv) so every raw->normalized
    mapping is auditable. Models contribute one row per raw model string; the
    numeric/temporal fields contribute one row per paper that stated a value.
    """
    rows = []

    # --- physical models (one row per raw model string) ---
    for _, r in models.iterrows():
        norm = r["family"] if pd.notna(r["family"]) else None
        rows.append({
            "paper_id": r["paper_id"], "doi": r["doi"],
            "category": "physical_model",
            "original_value": r["physical_model_raw"],
            "normalized_value": norm,
            "detail": f"canonical={r['canonical']}; kind={r['kind']}",
            "method": r["match_method"],
        })

    # --- per-paper scalar fields (original free text -> parsed value) ---
    def add_scalar(category, orig_col, norm_col, orig_fmt=None):
        for _, r in master.iterrows():
            orig = r.get(orig_col)
            if orig is None or (isinstance(orig, float) and pd.isna(orig)):
                continue
            rows.append({
                "paper_id": r["paper_id"], "doi": r["doi"],
                "category": category,
                "original_value": orig_fmt(r) if orig_fmt else orig,
                "normalized_value": r.get(norm_col),
                "detail": "", "method": "parse",
            })

    # length fields: normalized_value is the value list (no mean); detail = type/min/max
    for field, prefix, cat in (("tile_depth", "depth", "tile_depth_m"),
                               ("tile_spacing", "spacing", "tile_spacing_m")):
        for _, r in master.iterrows():
            orig = r.get(field)
            if not isinstance(orig, str) or not orig.strip():
                continue
            rows.append({
                "paper_id": r["paper_id"], "doi": r["doi"], "category": cat,
                "original_value": orig,
                "normalized_value": r.get(f"{prefix}_values_m") or None,
                "detail": f"type={r.get(prefix + '_value_type')}; "
                          f"min={r.get(prefix + '_min_m')}; max={r.get(prefix + '_max_m')}",
                "method": "parse",
            })

    add_scalar("study_duration_years", "study_duration", "span_years")
    add_scalar("temporal_bucket", "temporal_resolution", "temporal_bucket")
    add_scalar("area_km2", "total_area_value", "area_km2",
               orig_fmt=lambda r: f"{r['total_area_value']} {r.get('total_area_unit')}")

    audit = pd.DataFrame(rows, columns=["paper_id", "doi", "category",
                                        "original_value", "normalized_value",
                                        "detail", "method"])
    audit.to_csv(paths.INTERNAL / "audit_normalization.csv",
                 index=False, encoding="utf-8-sig")
    n_mapped = int(audit["normalized_value"].notna().sum())
    print(f"\naudit_normalization.csv: {len(audit)} (paper,category) rows, "
          f"{n_mapped} with a normalized value  [internal/]")
    print(audit["category"].value_counts().to_string())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-dir", default=str(paths.TABLES))
    parser.add_argument("--out-dir", default=str(paths.TABLES))
    args = parser.parse_args()

    paths.ensure()
    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    models = normalize_models(in_dir, out_dir)
    master = normalize_master(in_dir, out_dir)
    write_audit(models, master)
    print("\nDone. Normalized tables ->", out_dir, "| audit ->", paths.INTERNAL)


if __name__ == "__main__":
    main()
