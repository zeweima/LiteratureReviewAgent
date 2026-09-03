# synth_agentic — agentic LaTeX synthesis (fine → macro → overall)

A fresh, self-contained rebuild of the review synthesis. It shares **no code** with
`synth_latex_common.py` / `14*.py`. An **LLM orchestrator (one per unit)** drives three
tools; every artifact is saved **incrementally** so a hang loses nothing, and the cache
is simply "does the unit's `final.pdf` exist?".

## Two tracks (two independent review components)

The review is split into **two components**, each run as a separate `--track` with its own
inputs, manifests, output tree (`latex/agentic/<track>/`), and assembled paper. Same code,
run twice; they never share content and can be built independently.

| track | `config.TRACKS` name | source field(s) | lens (`TRACK_FOCUS`) | scale |
|---|---|---|---|---|
| **frontier** | `future_directions` | `future_directions` | research **frontier** — future directions, open questions, gaps | 14 fine → 5 macro |
| **knowledge** | `summary_bullets-highlights` | `summary_bullets` + `highlights` | **state of knowledge** — established findings, mechanisms, quantified outcomes | 20 fine → 6 macro |

`--track` selects the field(s) that feed the fine step, the output namespace, and the lens
that `TRACK_FOCUS` injects into every agent. Everything below applies to **each** track;
run the whole chain once per track to get two papers.

## Tiers

The engine is **step-agnostic** and runs three tiers within a track:

| step | unit | source | length | runner |
|---|---|---|---|---|
| **fine** (a) | one fine theme | tagged findings from the CSVs | max coverage, **no cap** | `run_stage.py --step fine` |
| **macro** (b) | one macro-cluster chapter | its member **fine** section bodies | integrate + condense, **soft cap ~8k** | `run_stage.py --step macro` |
| **overall** (c) | each section of the review **paper** | the **macro** chapters (theme kind) / earlier sections | architect-planned **per-section range** (~20k total) | `run_overall.py` |

Fine → macro nesting is already in the data (`macro_cluster` flows fine→manifest;
`cluster_summary_macro_<name>.csv` gives macro names/descriptions). Each tier only cites
keys already used in the tier below it.

## Architecture

```
prep_inputs.py --step <s>   ── materialize reusable inputs/<s>/<id>.md + _units.json
run_stage.py   --step <s>   ── one orchestrator per unit (fine, macro; flat + parallel)
run_overall.py              ── architect outline, staged generate, assemble main.pdf (overall)
        │
        ▼   (per unit)
   Orchestrator (SDK agent)  tools:  generate(instruction, attempt)
                                     review(attempt)
                                     report(status, accepted_attempt)
```

- **generate** — maintains a SINGLE evolving `section.tex` (Write once, then Edit in place),
  and **owns compilation** (bounded edit→compile→fix loop; empty-bib / undefined-cite counts
  as failure). Three prompt variants: **fine** = "retain maximal coverage"; **macro** =
  pure **synthesis** ("integrate and CONDENSE the fine sections into a coherent section");
  **overall** = writing one **section of a paper** (role-aware: an abstract/introduction
  reads as such, not as a thematic summary). Macro/overall condense under a soft word cap; the
  soft cap also
  implies a **hard cap** (`soft × HARD_OVERFLOW`, e.g. 5000→5500): if a compiled draft is over
  it, the same loop runs deterministic **condense** passes until it's under (or `GEN_MAX_CONDENSE`
  is exhausted). Returns a short status; never the LaTeX body.
- **review** — a Read sub-agent checking **two things only**: (1) faithfulness of every
  concrete claim to the source, and (2) citation correctness. Length is ignored — the soft
  cap is advisory to the generator only. For **fine** the source is tagged findings and
  (2) is reference matching (each key is the paper the finding came from). For **macro/
  overall** the source is the material being integrated — which already carries citations —
  so (2) is **citation provenance**: every `\parencite` in the generated section must trace
  to the *same key on supporting content* in the source (not merely a plausible paper).
- **report** — promotes the accepted attempt to `final.tex` / `final.pdf` / `body.tex`.

**Track focus.** The review has two components with distinct lenses; `config.TRACK_FOCUS`
tells the generator/orchestrator/architect which one they write under — **frontier** = future
directions / open questions / gaps; **knowledge** = established findings / mechanisms /
quantified outcomes. Threaded via `--track` into every step so the lens stays consistent as
macro condenses fine and the architect structures the paper.

**Overall (c) specifics.** `architect.py` reads all macro chapters under the track focus and
returns an outline (section kinds + a word **range** per section, summing to ~`OVERALL_BUDGET`);
`default_outline` is a deterministic fallback. Sections generate in **dependency order** —
theme (grounded in macros) → intro/cross/gaps/conclusions (grounded in the theme sections) →
abstract (grounded in intro + conclusions) — then are assembled into `overall/paper/main.tex`
(`\input`-ing each section) and compiled to one `main.pdf`.

Each section is also told its **role** in the paper (abstract / introduction / thematic /
cross-cutting / gaps / conclusions — see `run_overall._role`), so framing sections read as an
abstract or introduction rather than as a standalone thematic summary. The **overall (c)**
generator (`GEN_SYSTEM_PAPER`) distinguishes *framing* sections (abstract, intro, conclusions —
cohesive prose that frames the paper) from *thematic* ones (condense the sources). This
paper-writing framing is **c-only**; fine (a) and macro (b) stay a pure synthesis
(`GEN_SYSTEM`/`GEN_SYSTEM_SYNTH`, no role). Section conventions (by kind):
**abstract** and **conclusions** are **citation-free**; **introduction**, **conclusions**, and
**abstract** are **flat prose** (no `\subsection`). Theme / cross-cutting / gaps sections keep
sub-sections and citations. (Set in `run_overall.py`'s `_role` / `NO_CITE` / `NO_SUBSEC`;
enforced via the generator's role/structure/citation directives.)

Nested SDK sub-agents are safe: the SDK dispatches inbound tool calls on a separate asyncio
task, so a tool handler can `await query(...)`. Every `query()` is wrapped in `asyncio.wait_for`
and `aclose()`d to reap its `claude.exe`.

## Files

| file | role |
|---|---|
| `config.py` | paths, `TRACKS`, `TRACK_FOCUS`, `STEPS`, caps, models, timeouts |
| `latex_tmpl.py` | preamble + `wrap_section_doc`, cite handling, body cleanup |
| `texcompile.py` | deterministic quiet `latexmk` compile + Windows `nul` cleanup |
| `sdk_agents.py` | nesting-safe sub-agent wrapper (`call_agent`) |
| `prep_inputs.py` | **step 0**: `--step {fine,macro,overall}` → `inputs/<step>/*.md` + `_units.json` |
| `architect.py` | overall (c) outline planner (`architect_outline` + `default_outline`) |
| `tools.py` | generate/review/report + `finalize` + `build_server` (fine + synth modes) |
| `orchestrator.py` | `run_unit`: one orchestrator per unit, streams `orchestrator.jsonl` |
| `run_stage.py` | flat runner for fine + macro (writes `_<step>_manifest.json`) |
| `run_overall.py` | staged runner + paper assembly for overall |
| `run_fine.py` | deprecated shim → `run_stage.py --step fine` |
| `run_all.ps1` / `run_all.sh` | one-shot wrapper: chains fine→macro→overall via the CLIs |

## Layout (per `code/analysis/latex/agentic/<track>/`)

```
inputs/<step>/<id>.md            # reusable inputs (agent gets only the path)
inputs/<step>/_units.json        # unit index (overall also: _outline.json, _macro_digest.md)
<step>/<id>/                     # per-unit run outputs:
    section.tex  final.tex  final.pdf  body.tex  references.bib
    review_attempt_NN.json  orchestrator.jsonl  status.json  tokens.jsonl  _build/
<step>/_<step>_manifest.json     # consumed by the next step
overall/paper/{main.tex, main.pdf, sections/*.tex, references.bib}   # the assembled review
```
`<track>` ∈ {knowledge, frontier}; `<step>` ∈ {fine, macro, overall}.

## Run (dependency chain — each step needs the previous step's manifest)

Run the same chain for **each track**. Set `TRACK` (and its paper `TITLE`) once:

```bash
conda activate litkg   # SDK env
cd code/analysis/scripts/synth_agentic   # so config paths resolve (parents[2] == code/analysis)

# --- frontier ---
TRACK=frontier ; TITLE="Tile Drainage: Research Frontier"
# --- knowledge (run the same block with these two lines instead) ---
# TRACK=knowledge ; TITLE="Tile Drainage: State of Knowledge"

# fine (a)
python prep_inputs.py --step fine    --track $TRACK
python run_stage.py   --step fine    --track $TRACK

# macro (b) — needs fine/_fine_manifest.json
python prep_inputs.py --step macro   --track $TRACK
python run_stage.py   --step macro   --track $TRACK

# overall (c) — needs macro/_macro_manifest.json; the architect plans the outline
python prep_inputs.py --step overall --track $TRACK --title "$TITLE"
python run_overall.py --track $TRACK
```

Each track lands its own paper at `latex/agentic/<track>/overall/paper/main.pdf`.
Re-running without `--fresh` skips any unit whose `final.pdf` already exists (so a re-run
resumes a partial batch).

**One-shot wrapper** — `run_all.ps1` / `run_all.sh` just chain the six CLI calls above in
dependency order and stop at the first failure (extra flags after the track go to the
`run_*` steps only):

```powershell
.\run_all.ps1 -Track frontier                     # a → b → c for one track
.\run_all.ps1 -Track both                          # frontier then knowledge
.\run_all.ps1 -Track frontier --fresh --review-model opus
```
```bash
./run_all.sh frontier            # or: ./run_all.sh both
PY="conda run -n litkg python" ./run_all.sh knowledge --concurrency 2
```

**Single unit** (fine/macro): `run_stage.py --only <sel>`. A numeric selector matches
the cluster / zero-padded id prefix exactly (`--only 0` → only `00_…`, never `01_…`);
a non-numeric selector is a name substring (`--only drainage-modeling`). Add `--fresh`
to force a rebuild of just that unit. `--limit N` caps the count instead. `run_overall.py`
always builds the whole paper (no `--only`).

## Options

**CLI flags** (defaults in parentheses):

| flag | `prep_inputs.py` | `run_stage.py` | `run_overall.py` | meaning |
|---|:--:|:--:|:--:|---|
| `--track {frontier,knowledge}` | ✓ req | ✓ req | ✓ req | which review component |
| `--step {fine,macro,overall}` | ✓ (`fine`) | ✓ (`fine`, flat only) | — | which tier (`run_overall` is overall-only) |
| `--fresh` | ✓ | ✓ | ✓ | ignore cache / rebuild (`prep` clears the inputs dir) |
| `--title "..."` | ✓ (overall) | — | — | overall paper title (defaults per track) |
| `--in-dir PATH` | ✓ (`config.TABLES`) | — | — | source CSV dir |
| `--architect-model M` | ✓ (`sonnet`) | — | — | overall outline planner |
| `--only SEL` | — | ✓ test-only | — | one unit by cluster id / name substring |
| `--limit N` | — | ✓ test-only | — | cap number of units |
| `--concurrency N` | — | ✓ (`3`) | ✓ (`3`) | orchestrators (units/sections) in flight |
| `--orch-model M` | — | ✓ (`sonnet`) | ✓ (`sonnet`) | orchestrator model |
| `--gen-model M` | — | ✓ (`opus`) | ✓ (`opus`) | generator model |
| `--review-model M` | — | ✓ (`opus`) | ✓ (`opus`) | reviewer model (`sonnet` is cheaper/faster) |

Model names accept any CLI alias (`opus`, `sonnet`, `haiku`) or a full model id.
`run_fine.py` is a shim: `run_fine.py --track T` == `run_stage.py --step fine --track T`.

**`config.py` knobs** (no CLI flag — edit the file):

| constant | default | role |
|---|---|---|
| `TRACKS` / `FIELDS` | — | track → cluster name + source field(s) |
| `TRACK_FOCUS` | — | per-track lens injected into every agent |
| `STEPS` | — | step → mode (`fine`/`synth`), source tier, flat?, `cap_hi` |
| `MACRO_SOFT_CAP` | `8000` | macro chapter soft word cap (prompt-only) |
| `OVERALL_BUDGET` | `20000` | overall paper total words; architect splits into per-section ranges |
| `HARD_OVERFLOW` | `1.1` | hard word cap = soft cap × this; generator is forced under it (condense loop) |
| `CONCURRENCY` / `SUBAGENT_CONCURRENCY` | `3` / `6` | unit vs sub-agent parallelism |
| `MAX_ATTEMPTS` / `GEN_MAX_FIX` / `GEN_MAX_CONDENSE` | `4` / `10` / `3` | generate↔review rounds / compile-fix passes / condense-to-hard-cap passes |
| `ORCH_RETRIES` / `ORCH_RETRY_BACKOFF` | `3` / `5` | transient-error retries and backoff seconds×attempt |
| `GEN_TIMEOUT` / `REVIEW_TIMEOUT` | `1800` / `3600` | per generator / reviewer sub-agent (seconds) |
| `ORCH_TIMEOUT` / `COMPILE_TIMEOUT` / `ARCHITECT_TIMEOUT` | `14400` / `600` / `900` | orchestrator / latexmk / architect (seconds) |
| `GEN_MAX_TURNS` / `REVIEW_MAX_TURNS` / `ORCH_MAX_TURNS` | `30` / `30` / `40` | agent turn budgets |
| `MIKTEX_BIN` | — | latexmk fallback path if not on `PATH` |

## Verify

- After prep: `inputs/<step>/_units.json` + one `.md` per unit (overall also `_outline.json`).
- After a run: each `<step>/<id>/` has `final.pdf`, the review trail, `orchestrator.jsonl`,
  `status.json`. Cited keys ⊆ the unit's allowed anchors. For macro/overall, word counts sit
  near the soft cap (advisory). For overall, `overall/paper/main.pdf` compiles with a resolved
  bibliography and numbered sections.
- Tail `orchestrator.jsonl` mid-run — the audit trail exists **before** completion.

## Token accounting

Every sub-agent's token usage is captured from its final `ResultMessage`:

- **Live**: each call prints `    [tokens] <label> (<model>): in=… out=… cache_read=… cost=$…`
  to the run log (labels: `gen[aN]`, `gen-fix[aN.M]`, `review`, `architect`). The orchestrator's
  own usage is recorded in `orchestrator.jsonl` (`usage` on the result record).
- **Per unit**: `<step>/<id>/tokens.jsonl` — one JSON record per sub-agent call (label, model,
  in/out/cache_read/cache_creation/cost_usd). Rolled up into the manifest entry's `tokens`
  (`calls`, totals, and a `by_label` in/out split) and the `[done]` line.
- **Per run**: the driver prints `[tokens] total: in=… out=… cost=$…` in the summary.

This makes the generate-vs-review cost split explicit — review's `in` is typically far larger
(it reads the section **plus** the full source), which is why it can dominate wall-clock.

## Failure handling (why one crash can't sink the run)

Each generator/reviewer/architect/orchestrator is its own `claude.exe`; a subprocess
that exits non-zero surfaces as an anyio `BaseExceptionGroup` (which is **not** an
`Exception`, so a plain `except Exception` misses it). Resilience is layered so that a
single failure degrades to a recorded status + best-effort output instead of aborting
the batch:

1. **Sub-agent (`call_agent`)** — the query is wrapped in `asyncio.wait_for` and catches
   `BaseException` (re-raising only `CancelledError`), folding a timeout or crash into a
   return string (`[sub-agent timed out …]` / `[sub-agent error: …]`). It never propagates,
   so a nested crash can't poison its parent. `aclose()` reaps the subprocess, and
   `install_quiet_exception_handler()` drops the benign "Task exception was never retrieved"
   cleanup noise.
2. **Review tool** — on a folded error/timeout it waits 5s and retries once; if still bad it
   returns **INCONCLUSIVE** (`review_passed = null`) rather than looping — the draft already
   compiled, so the orchestrator may report it as pass.
3. **Unit (`run_unit`)** — the orchestrator query is likewise `BaseException`-guarded; process/
   exit-code errors count as transient and are retried up to `ORCH_RETRIES` with backoff.
   Whatever happens, a **fallback finalize** runs: if any attempt compiled, the best is promoted
   to `final.pdf` (status `fallback`); otherwise status is `failed`/`no_attempts`. A unit is
   **never** left in `running`, and `run_unit` never raises. Artifacts are written incrementally,
   so a mid-run kill loses nothing.
4. **Batch (`run_stage` / `run_overall`)** — `asyncio.gather(..., return_exceptions=True)` so one
   unit can't cancel its siblings; any stray exception becomes a fail entry. The
   `_<step>_manifest.json` is **always** written with every unit's status, and the overall paper
   still assembles from whatever sections compiled.
5. **Cache / resume** — the durable success marker is `final.pdf`; re-running without `--fresh`
   skips finished units, so resuming a partial or partially-failed batch is just re-running it.
