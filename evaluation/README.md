# Evaluation

Validation of the pipeline's own output against a human reviewer, in two parts.

## `tier1/` -- extraction accuracy

One CSV per extracted field (29 files). Each row is one paper's value for that
field, judged `correct` / `minor_error` / `critical_error` / `complete_wrong`
against the source paper by a human reviewer, following an LLM-generated first
pass that the human then verified.

Columns: `doi`, `extracted_content` (what the pipeline extracted), `assessment`
(the human's final verdict), `llm_evidence` (the short source-paper quote the
LLM cited to support its judgment), `human_note`.

## `tier2_fine.csv`, `tier3_macro.csv`, `tier4_overall.csv` -- citation fidelity

One row per citation in the generated reviews, at the fine-theme, macro-theme,
and overall-paper synthesis levels respectively: which paper is cited, the
surrounding claim (`claim_paragraph`, the agent's own generated prose), and a
human verdict on whether the citation is actually supported.
