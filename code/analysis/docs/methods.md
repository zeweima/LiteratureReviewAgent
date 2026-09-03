# Methods and edge cases — machine-assisted tile-drainage systematic map

Detailed documentation of the analysis pipeline (`code/analysis/`) that turns
1,946 LLM-extracted papers into the systematic map. Written for the paper's
Methods section + Supplementary Information: it states *what* each step does,
*why* it is built that way, and — critically — *how each known edge case is
handled*, since the corpus is heterogeneous OCR-derived text where naive parsing
silently produces wrong numbers.

Two principles run through everything:
- **Nothing silently dropped.** Every unmapped, unparsed, or implausible value is
  written to an `internal/` audit table with its raw input, so failures are
  visible and countable rather than hidden.
- **Effort scales with unique strings, not papers.** Curated assets (model
  registry, region map) grow with *distinct* vocabulary (Heaps' law), and all
  LLM calls are hash-cached, so re-runs are deterministic and ~free.

---

## 1. Provenance and corpus definition

Input is one `merged_extraction.json` per paper (`code/results/*/`), each an
LLM extraction against a fixed Pydantic schema. `1_flatten_extractions.py` walks
all 1,946 and emits a wide `papers_master.csv` (one row/paper) plus ~18 tidy
`long_*.csv` (one row per multi-valued item: study types, practices, models,
findings, etc.). This step is **raw** — no analysis, no normalization — so the
flattened tables are a faithful, auditable image of the extractions.

**Scope.** Each paper carries a `relevance_level`. In-scope = **Tile-centered
(930) + Tile-related (514) = 1,444**; excluded = Mentioned (193), Little relevance
(83), Not relevant (226). These five levels are the inclusion half of the PRISMA
flow (identification/search counts are external to the extractions).

**Edge cases.**
- CSV round-tripping turns missing text into float `NaN`; every downstream loader
  coerces geo/text columns back to `None` before string ops (otherwise
  `.strip()`/regex raise `AttributeError` on floats).
- Windows console is cp1252; all scripts force `PYTHONUTF8=1` and write CSVs
  `utf-8-sig` so degree signs, primes and non-ASCII place names survive.

---

## 2. Field normalization (`2_normalize_fields.py`, `normalizers.py`)

Normalizes the messy free-text fields into analysis-ready values, writing
`papers_master_normalized.csv`, normalized long tables, and audit tables. The
raw tables are never mutated.

### 2.1 Process-model family registry
Model names are wildly inconsistent (`DRAINMOD`, `DRAINMOD-NII`, `Drainmod 6.1`,
`DRAINMOD-N II`). We canonicalize then match against a curated regex registry
(`FAMILY_RULES`), falling back to a frequency-sorted review queue.

- `canonicalize()` lowercases, strips version numbers, parentheticals and
  suffixes; `_loose()` keeps parenthetical acronyms so `Root Zone Water Quality
  Model (RZWQM)` still matches. `assign_family()` tests each rule against **both**
  the canonical and loose forms.
- Every unmatched string goes to `internal/review_physical_models.csv` sorted by
  frequency — the *only* human-curation surface, and it grows with unique names,
  not papers. Current coverage ≈ 57%.

**Edge cases.**
- *Acronym-in-parenthetical dropped by canonicalize* (`…(RZWQM)`): fixed by also
  matching the loose form. Raised mapping 45%→57%.
- *Version digits fused to the name* (`SWAT2005`, `SWAT2009`): a `\bswat\b|swat\d`
  rule catches boundary-less digits; a plain `\bswat\b` missed them.
- *Double-counting variants*: a paper listing `SWAT` + `SWAT2005` mapped to two
  rows of the same family. Row-counted crosstabs now dedupe on
  `(paper_id, family)`; category counts use `nunique(paper_id)`. (101 such
  paper–family pairs existed; SWAT was 59 rows vs 41 papers.)
- *Models vs analytical equations*: kept in one registry but tagged `kind`
  (model/equation) so the model landscape isn't polluted by Glover-Dumm,
  Van Beers, Hooghoudt, etc.

### 2.2 Tile depth and spacing — per-number unit parsing (no averaging)
Design fields are free text like `"drains at 1.1 m depth, 12–18 m apart"` or
`"0.9, 1.2 and 1.5 m"`. We **keep every value** (ranges → both endpoints; lists →
all), never a mean, because a study can report several designs.

`parse_lengths_m(text)` returns `{value_type: single|range|list, values, min_m,
max_m, n_values}`. Each number takes **the unit written next to it**, and a
number lacking a unit inherits from its nearest neighbour (units are usually
stated once at the end of a list). Output is one tidy row per value in
`long_tile_dimensions_normalized.csv` plus per-paper summary columns.

**Edge cases.**
- *Mixed units mis-scaled*: `"1 m deep … 40 cm"` under a single-unit assumption
  became `[0.01, 0.4]`. Per-number detection with neighbour inheritance yields the
  correct `[1.0, 0.4]`.
- *Range vs list*: `"0.6–1.2 m"` → range (min/max); `"5, 10, 20 m"` → list of
  three. Distinguished by a range-separator regex (`-`, `–`, `~`, `to`) with no
  intervening comma.
- *Implausible values kept but flagged*: parsed years (`2015`), plot dimensions
  (`400 m`), record IDs (`ID-076`→76) and model-version digits fall outside
  physical bounds (depth 0.05–5 m, spacing 0.5–300 m). They are written with
  `plausible=False` — **not deleted** — and excluded only from the per-paper
  summary and figures. 136 values flagged; clean medians: depth ≈ 1.0 m, spacing
  ≈ 16 m.
- *Imperial units*: mm/cm/m/km are converted; `ft`/`inch` are **not** and get
  `plausible=False` (a documented gap, visible in the audit).

### 2.3 Other scalars
`area_to_km2` harmonizes total-area value+unit; `parse_year_span` derives
start/end/`span_years` from study-duration text; `temporal_bucket` normalizes
temporal resolution. Each writes raw→parsed rows to
`internal/audit_normalization.csv` (columns: `doi, category, original_value,
normalized_value, detail, method`) so every mapping is auditable.

---

## 3. Hierarchical geography (`3_geography.py`)

Geography is resolved at nested scales because a global choropleth, a US county
map, and a watershed all answer different questions: **world → country → region**,
**US → state → county**, and **watershed** as a cross-cutting scale (watersheds
straddle county/state lines).

### 3.1 Country and region
Free-text country fields are split on delimiters/`and`, mapped to ISO via
`country_mapping.py` (exact → alias → fuzzy ≥0.88), and joined to
`World_Countries.geojson`. Region roll-up uses a curated `COUNTRY_REGION` table
(`geo_time.py`). Multi-country papers are exploded to one (paper, region) row per
region, so a pan-regional study counts in each.

**Edge cases.** Aliases: `USA/U.S./America`→United States;
`UK/England/Scotland/Wales/Northern Ireland`→United Kingdom; `the Netherlands`,
`Russia`→Russian Federation, etc. Unmapped tokens are historical/ambiguous
(`Yugoslavia`, `German Democratic Republic`, `Latvian S.S.R.`, bare `Europe`) and
are reported, not force-fit.

### 3.2 Coordinate parsing — best-effort, multi-site (`geo_coords.py`)
`coordinates_text` is the single most corrupted field (DMS, decimal, swapped
order, bounding boxes, multi-site lists, UTM, national grids, OCR noise, LaTeX).
`parse_coords_multi(text)` returns **every** site as `(lat, lon, method)` with
`method ∈ {dms, decimal, bare, labeled}`; `parse_coords` is the single-point
view. A normalization pass `_prep()` repairs common corruptions before matching.
Corpus parse rate ≈ 88%.

**Edge cases (this field drove most of the debugging).**
- *Dropped minute marker* — **the worst, because it was silently wrong.**
  `30°21N` re-anchored the token on the minutes and returned `21.0` (marked
  parsed=True). `_prep` re-inserts the missing prime (`30°21N`→`30°21'N`) → 30.35.
- *Dropped seconds marker*: `03°03′37.3E`→`03°03′37.3''E` (else it read as a
  second longitude token → landed in Eritrea).
- *Mojibake markers*: runs of control bytes that replaced the degree/prime glyph
  (`33␂␘N`) → normalized to `°`.
- *LaTeX / caret degree*: `41^{\circ}52'`, `29^0 10'` → `°`.
- *Prose-word false hemisphere*: lowercase `[nsew]` matched the `w` in "was"
  (`Trial 1 was…` → `1°W` → lon −1.0). Fixed with a trailing letter-boundary
  lookahead so ordinary words can't be hemispheres.
- *Spelled-out hemispheres*: `27°23′0″ South`, `east longitude` are matched
  explicitly (North/South/East/West) and reduced to their first letter.
- *Decimal degrees, no hemisphere*: `(53.936°, 11.877°)` — the `°` broke the bare
  fallback; the bare regex now tolerates a trailing degree sign.
- *Invalid minutes/seconds ≥60* (`42°74'77″`, `59°66'N`): rejected → stays
  unparsed (safer than a wrong point) and visible in the audit.
- *Multi-site studies*: `(30°21N,112°31E) … (30°33N,114°22E)` and
  `ENREC: 41.17,-96.47; SCAL: 40.57,-98.15` yield two points each; hemisphere
  tokens are paired lat+lon in text order, flushing a new site when an axis
  repeats. 71 papers have ≥2 sites; **no averaging**.
- *Still unsupported (≈12%, flagged `parsed=False`)*: UTM / national grid refs,
  space-separated markerless DMS (`31°52 56″`), concatenated MMSS (`31°5256″`),
  leading-hemisphere-only decimal-minute formats. Ranges (`39°–46°N`) take the
  endpoint carrying the hemisphere (the border-landing result is then caught by
  the plausibility check below).

### 3.3 Coordinate plausibility — the silent-error safety net
Parsing yields a syntactically valid pair even for a lat/lon swap, a sign drop, or
a hemisphere typo. `geolocate()` cross-checks: every parsed point is spatially
joined to the world polygons, and the country it **falls in** is compared to the
country the paper **states**. `in_country` = True (match), False (elsewhere =
SUSPECT), or None (can't judge — unparsed or unmappable country). Suspect points
are flagged in `internal/audit_coordinates.csv` and **excluded from the map** but
never deleted; `geo_points.csv` holds only map-ready plausible sites. ~20 suspect
sites caught (swaps like `36°39'E, 53°04'N`→Russia; `-88.21E`→China instead of
Illinois).

**Edge cases.**
- *Ocean / offshore points*: a point in water matches no polygon. A **bounding box
  is deliberately NOT used** for the water case — the US bbox spans almost all
  longitudes because the Aleutians cross the antimeridian, so a mid-Pacific point
  would pass. Instead we test **distance to the stated country's coastline**:
  plausible if within ~1.5° (~165 km), which keeps genuine coastal field stations
  (Jiangsu coast, Copenhagen Airport, Whangarei, PEI) while rejecting an
  open-ocean typo (a US site parsed to 39.6 N, 42.2 W in the mid-Atlantic).

### 3.4 US state, county, watershed
- **State**: US-only papers; `state_province` split into tokens, mapped
  (exact/abbrev/fuzzy) to TIGER states; drops non-state tokens (`Midwest`,
  `Corn Belt`). Multi-state papers explode per state.
- **County**: two independent signals combined — the explicit `county` field
  (resolved within its state) **and** coordinate reverse-geocoding (each plausible
  US point spatial-joined to `tl_2019_us_county`). A multi-site study places into
  **every distinct county it touches** (deduped on `(paper, GEOID)`) — e.g. one
  study spanning 4 sites across IA/OH/IL counts in 4 counties. 252 papers placed
  to 292 paper–county pairs (32 multi-county).
- **Watershed**: kept as a separate scale (light whitespace normalization only),
  since watershed names are idiosyncratic and cross admin boundaries.

---

## 4. Category statistics and spatiotemporal variation (`4_`, `5_`)

`4_category_stats.py`: study-type × scale, practice × study-type, practice
co-occurrence (drainage + management on one axis), publications/year × study-type,
the normalized model landscape, design-parameter distributions (from the
`plausible` depth/spacing values), and field completeness. `5_spatiotemporal.py`:
categorical fields (practice, study type, model family) × **period** and ×
**region**, with variant-deduped model rows.

**Edge cases.**
- *Period granularity*: publication-year bins are **5-year** (`<2000, 2000-2004,
  …, 2025-2029`) — decades were too coarse to show compositional shifts. Pre-2000
  is a sparse catch-all.
- *Model variant double-counting* (§2.1) is deduped before row-counted crosstabs.

---

## 5. Theme clustering (`6_topic_clusters.py`, `topic_cluster.py`)

Themes are discovered by embedding finding statements and clustering, then named
by the SDK. Two design choices matter.

### 5.1 Paper-level clustering
We embed each text unit (summary bullets + highlights, ~16k units) with
sentence-transformers `all-MiniLM-L6-v2`, then **mean-pool to one vector per
paper** (~1.4k) and cluster those. Rationale: Ward is O(n²) in memory; clustering
16k sentences is intractable, and the paper is the right unit for a downstream
codebook (one dominant theme per paper). Vectors are L2-normalized so Euclidean
Ward is monotonic with cosine (avoids the one-mega-cluster failure of
average/cosine linkage on dense embeddings).

### 5.2 Two-level (macro → fine) scheme
Fine clustering (`k=20` knowledge, `k=14` frontier). The **macro tier groups the
fine-theme centroids** into ~6/5 super-themes with a second Ward pass. This is
nested by construction (macro-themes are unions of fine themes), so the fine
partition — and every downstream SDK cache — is untouched.

### 5.3 How k is chosen — and the honest answer (`kdiag.py`)
A diagnostic sweeps k (6–32) computing silhouette (cosine), Calinski–Harabasz,
Davies–Bouldin, the Ward merge-gap elbow, and centroid-redundancy. **Finding:
there is no separable ground-truth k** — silhouette ≈ 0.02–0.04 across all k on
both tracks, CH declines monotonically, merge-gaps favour only a few macro-themes.
The embedding space is a thematic *continuum*, as expected for overlapping
scientific abstracts. So k is treated as a **resolution parameter**, chosen for
interpretability and non-redundancy, and reported as such — with the two-level
scheme presenting themes at two defensible zoom levels rather than claiming one
"correct" number.

**Edge cases.** Ward `n_clusters` is clamped to `[2, n-1]`. Representative
statements per cluster are de-duplicated by paper so one prolific paper doesn't
supply all three exemplars.

---

## 6. SDK labeling and grounded synthesis (`7_`, `10_`, `cluster_label.py`)

All LLM steps drive the authenticated `claude` CLI via `claude_agent_sdk` (no API
key), single-turn, no tools, `bypassPermissions`, and are **disk-cached by a hash
of (model, system prompt, user prompt)** — so re-runs hit cache and cost nothing,
and results are deterministic and auditable.

### 6.1 Theme naming (`7_label_clusters.py`)
Per fine cluster, the model is given the cluster's characteristic terms +
representative statements and returns strict JSON `{theme, description}`, grounded
*only* in the supplied statements. Macro themes are then named from their member
fine-theme names (`label_macros`), writing `cluster_summary_macro_<name>.csv`.
#LLM calls = #clusters (tens), not #papers.

### 6.2 Synthesis — fan-out per cluster, then merge (`10_synthesize_themes.py`)
Finding text is rebuilt from the long tables and tagged with **citation keys**
(`FirstAuthorYear`, collision-suffixed `a/b/c`). Then:
- **Stage 1 (fan-out).** One agent per **fine** cluster synthesizes that cluster's
  findings into a cited paragraph. Calls run in parallel (`asyncio.gather`,
  concurrency 4).
- **Stage 2 (merge).** One agent per **macro** theme merges its member sub-theme
  syntheses into an integrated paragraph, **preserving citation keys** and adding
  no new facts.
The output doc is hierarchical: `## macro synthesis → ### fine sub-theme syntheses
→ References (keys→DOIs)`.

**Grounding discipline.** The model uses ONLY supplied findings, must cite the
provided keys inline, and is told its output is data, not a chat reply — so every
sentence traces to a DOI. To bound prompt size, findings are capped per theme (45)
via round-robin across papers (diversity over volume).

**Edge cases.**
- *Cache compatibility*: Stage-1 keys are computed identically to the previous
  single-pass version, so refactoring to fan-out/merge produced **0 new fine
  calls** — only the ~8 multi-child macro merges were new.
- *Single-child macros* (e.g. Remote Sensing) have nothing to merge → the fine
  synthesis passes through, no wasted call.

---

## 7. Macro geography and visualization (`8_`, `11_`)

`8_cluster_viz.py` renders the fine-theme 2-D map (PCA/t-SNE on the same vectors),
theme sizes, and theme × period/region. `11_macro_geography.py` (no embeddings, so
fast) renders macro-theme variation: × period (counts) **and a 100%-stacked
compositional-share plot** (which reveals shifts the raw counts hide — e.g.
Contaminant-Loss share 48%→37% while Management rose 16%→24% and reactive-media
tripled), × world region, and **within the US** as a macro × state heatmap plus a
per-theme CONUS choropleth small-multiple.

---

## 8. Reproducibility and environment

- **Path routing** via `paths.py` (single source of truth: `TABLES/INTERNAL/
  FIGURES/DOCS/RESULTS/MAPS`); no per-run flags.
- **Determinism**: clustering and TF-IDF are seeded/deterministic; all LLM calls
  cached by content hash; `Date.now`/RNG avoided.
- **Environment edge cases**: `GDAL_DATA` pointed at pyogrio's bundle to silence
  projection warnings; a Windows SSL patch (certifi CA bundle) before importing
  sentence-transformers; the harmless duplicate-OpenMP warning is expected.

---

## 9. Consolidated limitations (for the paper's Limitations)

- Model registry ≈57% mapped (remainder in the review queue, long tail).
- Coordinate parser ≈88%; UTM/national-grid/markerless-DMS unsupported; ranges
  reduce to an endpoint. Imperial tile depths not converted.
- Country cross-check uses a ~1.5° coastal tolerance — a coarse geo sanity gate,
  not survey-grade validation.
- Themes are a continuum with no natural k; the number is a reported resolution
  choice, not a discovered truth.
- The 31,516 `quantitative_stats` rows remain unanalyzed by design (a quantitative
  meta-analysis is deferred future work).
- Extraction accuracy itself needs a human-checked validation subsample (planned)
  before quantitative claims are pushed hard.
