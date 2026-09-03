"""Central path layout for the tile-drainage analysis pipeline.

Directory structure (all under code/analysis/):
    scripts/    -- this code (numbered pipeline + shared modules)
    tables/     -- analysis-ready CSV tabular data
    internal/   -- internal checking & state: review queues, assignment audits,
                   and the deterministic SDK caches (labeling / synthesis)
    figures/    -- PNG figures
    docs/       -- markdown syntheses + README

Data inputs live outside the analysis tree:
    ../results              -- one folder per DOI with merged_extraction.json
    ../../Data/Maps/BaseMap -- basemaps (world geojson, TIGER state/county zips)

Every script imports these constants instead of hard-coding paths, so the whole
pipeline can be relocated by moving this file. Call ``ensure()`` once at startup.
"""

from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
BASE = SCRIPTS.parent                       # code/analysis
TABLES = BASE / "tables"
INTERNAL = BASE / "internal"
FIGURES = BASE / "figures"
DOCS = BASE / "docs"

RESULTS = BASE.parent / "results"           # code/results
MAPS = BASE.parent.parent / "Data" / "Maps" / "BaseMap"

LABEL_CACHE = INTERNAL / "_label_cache"
SYNTH_CACHE = INTERNAL / "_synth_cache"


def ensure() -> None:
    """Create the output folders if missing (inputs are never created)."""
    for d in (TABLES, INTERNAL, FIGURES, DOCS):
        d.mkdir(parents=True, exist_ok=True)
