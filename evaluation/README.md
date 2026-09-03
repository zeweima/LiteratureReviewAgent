# Evaluation

Validation of the pipeline's own output against a human reviewer, in two parts.

## `tier1/` -- extraction accuracy

One CSV per extracted field (28 files, 1,784 rows total). Each row is one
paper's value for that field, judged `correct` / `wrong` against the source
paper by a human reviewer, following an LLM-generated first pass that the human
then verified. 1,752 rows are `correct`, 32 are `wrong`.

Columns: `doi`, `extracted_content` (what the pipeline extracted), `assessment`
(the human's final verdict), `llm_evidence` (the short source-paper quote the
LLM cited to support its judgment), `human_note`.

`tier1_incorrect_fields.csv` pulls just those 32 `wrong` rows for `tier1/` into
the older, per-error column layout -- `field` (which of the 28 fields), `doi`,
`extracted_content`, `assessment`, `llm_evidence`, `human_note` -- one row per
error rather than one file per field. Kept alongside `tier1/` because it's the
form used to walk through the errors directly, rather than to derive the
accuracy rate.

## `tier2_fine.csv`, `tier3_macro.csv`, `tier4_overall.csv` -- citation fidelity

One row per sampled citation in the generated reviews (100 each), at the
fine-theme, macro-theme, and overall-paper synthesis levels respectively: which
paper is cited, the surrounding claim (`claim_paragraph`, the agent's own
generated prose), and a human verdict, `supports` / `does_not_support`, on
whether the cited paper actually backs the claim. `does_not_support`: 1/100
(fine), 2/100 (macro), 5/100 (overall).

`citation_not_supported.csv` pulls just those 8 `does_not_support` rows across
all three tiers into one file, with `resolved_doi`, `paragraph` (the flagged
claim), and `note` (the reviewer's explanation of what's actually supported
instead) in place of `doi`, `claim_paragraph`, and `human_note`.
