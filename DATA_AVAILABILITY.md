# Data availability

## What is here

Aggregate statistics, coded per-paper fields, cluster assignments, geographic
summaries, figures, and the agent-generated reviews.

`tables/papers_master.csv` is the corpus index: bibliographic metadata (DOI,
year, journal, authors, title) plus the coded fields the pipeline extracted --
country, study type, spatial scale, drainage practices, tile geometry, and the
per-paper counts. These are factual codings.

## What is not here, and why

Most of the reviewed corpus is not open access, so no text drawn from the source
papers is redistributed. The following are withheld:

* per-paper extracted statements -- summary bullets, highlights, future
  directions, limitations, research gaps, aims, hypotheses, assumptions,
  quantitative results, keywords, and analytical-method descriptions
* the free-text columns of the corpus index -- executive summary, main
  contribution, relevance justification, scientific question, area description
* the full text of the cited papers
* raw extraction payloads and run logs
* `latex/agentic/*/inputs/fine/` -- the same statements, bundled by theme as the
  input to the first writing stage. Every later stage's input is the previous
  stage's output and is included, so the cascade is readable from the fine
  drafts onward; only its first input is withheld.

The corpus is identified by DOI in `tables/papers_master.csv`, so the extraction
stage can be re-run by anyone with their own access to the papers, using the
scripts in this release.

## Evaluation is the deliberate exception

`evaluation/tier1/` includes an `llm_evidence` column: a short quote from the
source paper that the reviewing LLM cited to justify its judgment. This is the
one place the redistribution rule above is relaxed on purpose -- these quotes
are the evidentiary record behind the extraction-accuracy verdicts, and the
validation is not independently auditable without them. Everything else in
`evaluation/` is either the pipeline's own output (`extracted_content`) or
generated review prose (`claim_paragraph`), not source text.

## Provenance

Every table under `code/analysis/tables/` is machine-checked against the
withheld-column list at build time; the build fails if source-derived prose
appears there. `evaluation/` is supplied and maintained separately and is not
covered by that check.
