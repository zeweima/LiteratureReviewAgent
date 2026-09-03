# Agentic literature review of subsurface (tile) drainage research

Code and derived results for a multi-agent framework that screens a literature
corpus, extracts structured evidence from each paper, discovers a nested theme
hierarchy from the extracted statements, and drafts a cited review from it.

The framework runs two independent content tracks over the same corpus:

* **knowledge** -- what the literature reports (`summary_bullets`, `highlights`)
* **frontier** -- what it says remains open (`future_directions`)

Each track is clustered and written separately, producing two reviews.

## Layout

```
code/analysis/
  scripts/          pipeline, clustering, figures, and the synthesis agents
  tables/           aggregate statistics and coded per-paper fields (CSV)
  figures/          all generated figures (PNG)
  docs/             methods notes and the agents' intermediate syntheses
  internal/         cluster-number diagnostics
  latex/agentic/    the generated reviews (LaTeX + PDF)
evaluation/         extraction-accuracy and citation-fidelity validation
```

`scripts/paths.py` resolves every location relative to itself, so the tree runs
where it stands.

## Running it

```
pip install -r requirements.txt
cd code/analysis/scripts
python 3_geography.py            # and the other numbered stages
```

Stages differ in what they need, because the raw extractions are not
redistributable (see `DATA_AVAILABILITY.md`):

| Stage | Scripts | Runs from this release? |
|---|---|---|
| Extraction and flattening | `0_`-`2_` | No -- needs the per-paper extraction payloads |
| Geography, categories, cross-tabs | `3_`-`5_`, `3b_`, `4b_` | Yes |
| Clustering | `6_`, `7_` | No -- needs the extracted statement text to embed |
| Theme figures and tables | `8_`-`11_`, `kdiag` | Yes, from the shipped cluster assignments |
| Synthesis agents | `synth_agentic/` | Needs an API key and the statement text |

The clustering stage is the one break in the chain: its inputs are withheld, so
its *outputs* ship instead (`clusters_*.csv`, `stat_theme_hierarchy.csv`) and
everything downstream of it reproduces from those.

The geography scripts additionally expect basemap shapefiles under
`Data/Maps/BaseMap`, which are redistributed from their original public sources
rather than bundled here.

## Generated reviews

`code/analysis/latex/agentic/` holds the reviews as written by the agents, with
no human editing: `final_review_knowledge`, `final_review_frontier`, and the two
concatenated. `code/analysis/docs/final_review_toc.md` is their full section and
subsection structure.

## The synthesis run

Report generation is a three-stage cascade, and each stage's inputs and outputs
are here under `latex/agentic/<track>/`:

```
inputs/fine/       findings bundled per fine theme      -> written by prep_inputs
fine/              one drafted section per fine theme
inputs/macro/      the fine sections, regrouped
macro/             one drafted section per macro theme
inputs/overall/    the macro sections, plus the outline
overall/paper/     the assembled review
```

So the input to each stage is the previous stage's output, and the chain can be
read end to end: what the agent was given, what it was allowed to cite, and what
it wrote.

Alongside the drafts:

* `inputs/*/_units.json` -- each unit and the citation keys its writer was
  permitted to use. Citations are bounded structurally: a key outside this list
  is deleted before compile, so an unsupported citation cannot survive.
* `inputs/overall/_outline.json`, `_architect_raw.txt` -- the architect agent's
  plan, at subsection granularity, before any prose was written.
* `inputs/overall/_macro_digest.md` -- the condensed macro input to the architect.
* `tokens.jsonl` -- per-call token accounting.

`references.bib` sits once at `latex/agentic/`; every unit was compiled against
this same file, so the per-unit copies are not repeated. Per-unit PDFs and TeX
logs are build products of the shipped `.tex` and are omitted.

`inputs/fine/` is the one stage whose input is drawn from the source papers
rather than from a previous agent; see `DATA_AVAILABILITY.md`.

## Evaluation

`evaluation/` validates the pipeline's own output against a human reviewer:
per-field extraction accuracy (`tier1/`) and citation fidelity in the generated
reviews at the fine-theme, macro-theme, and overall-paper levels (`tier2_fine`,
`tier3_macro`, `tier4_overall`). See `evaluation/README.md` and
`DATA_AVAILABILITY.md`.

## Contents of this build

* 41 script files
* 55 result tables plus the 1,946-paper corpus index
* 49 figures
* 51 documentation and synthesis notes
