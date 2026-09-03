"""Shared helpers for the reliability-assessment sampling tools.

Resolves a citation key back to its original paper (doi -> results folder ->
merged_extraction.json / mineru markdown), and extracts (claim, citation-key)
pairs from generated LaTeX, stratified by \\subsection, for the sampling
scripts in this folder. Pure stdlib -- no external dependencies.
"""
from __future__ import annotations

import re
from pathlib import Path

CITE_RE = re.compile(
    r"\\(?:parencite|textcite|autocite|citep|citet|cite)\*?(?:\[[^\]]*\])*\{([^}]*)\}"
)
SECTION_RE = re.compile(r"\\section\*?\{([^}]*)\}")
SUBSEC_RE = re.compile(r"\\subsection\*?\{([^}]*)\}")
BIB_ENTRY_RE = re.compile(r"@\w+\{([^,\s]+)\s*,(.*?)\n\}", re.S)
BIB_DOI_RE = re.compile(r"doi\s*=\s*\{([^}]*)\}")

MAX_WINDOW = 500  # chars to search either side of a citation for a sentence boundary


def find_root(start) -> Path:
    """Walk up from `start` until a directory containing code/results is found."""
    p = Path(start).resolve()
    for parent in [p] + list(p.parents):
        if (parent / "code" / "results").is_dir():
            return parent
    raise RuntimeError(f"could not locate repo root (code/results not found) from {start}")


def load_bib_doi_map(bib_path) -> dict:
    """citekey -> doi, parsed directly from the master references.bib."""
    text = Path(bib_path).read_text(encoding="utf-8")
    out = {}
    for m in BIB_ENTRY_RE.finditer(text):
        key = m.group(1).strip()
        dm = BIB_DOI_RE.search(m.group(2))
        if dm:
            # A few bib entries carry a stray bibtex-escape backslash before an
            # underscore (e.g. "10.1007/978-3-030-01036-2\_7") that isn't part of the
            # real DOI -- strip it, or the derived paper_id below won't match any
            # results/mineru folder.
            out[key] = dm.group(1).strip().replace("\\_", "_").replace("\\", "")
    return out


def doi_to_paper_id(doi: str) -> str:
    """Matches how code/results and Data/mineru folders are actually named: both "/" and
    ":" become "_" (old ASCE/ASTM/Springer DOIs like ".../119:1(201)" or "10.1023/A:..."
    use ":" as a delimiter, and ":" is illegal in a Windows path anyway, so it was
    replaced at ingestion time same as "/").
    """
    return re.sub(r"[/:]", "_", doi)


def resolve_key(key: str, doi_map: dict, results_dir: Path, mineru_dir: Path) -> dict:
    """citekey -> {doi, paper_id, extraction_json, markdown, pdf} (paths None if missing)."""
    doi = doi_map.get(key)
    if not doi:
        return {"doi": None, "paper_id": None, "extraction_json": None, "markdown": None, "pdf": None}
    paper_id = doi_to_paper_id(doi)
    ejson = Path(results_dir) / paper_id / "merged_extraction.json"
    vlm = Path(mineru_dir) / paper_id / "vlm"
    md = vlm / f"{paper_id}.md"
    pdf = vlm / f"{paper_id}_origin.pdf"
    return {
        "doi": doi,
        "paper_id": paper_id,
        "extraction_json": str(ejson) if ejson.exists() else None,
        "markdown": str(md) if md.exists() else None,
        "pdf": str(pdf) if pdf.exists() else None,
    }


def extract_section_title(text: str) -> str:
    """The unit's own \\section*{...} title (e.g. "Bioreactor Design and Research Gaps") --
    a human-readable label distinct from both the folder-slug unit_id and the \\subsection
    titles nested inside it."""
    m = SECTION_RE.search(text)
    return m.group(1).strip() if m else ""


def split_subsections(text: str):
    """[(title, body_text), ...] in order; one ('(no subsections)', text) bucket if none found."""
    matches = list(SUBSEC_RE.finditer(text))
    if not matches:
        return [("(no subsections)", text)]
    segs = []
    pre = text[: matches[0].start()].strip()
    if pre:
        segs.append(("(preamble)", pre))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segs.append((title, text[start:end]))
    return segs


def _claim_window(text: str, start: int, end: int) -> str:
    back = text.rfind(". ", max(0, start - MAX_WINDOW), start)
    seg_start = back + 2 if back != -1 else max(0, start - MAX_WINDOW)
    fwd = text.find(". ", end, min(len(text), end + MAX_WINDOW))
    seg_end = fwd + 1 if fwd != -1 else min(len(text), end + MAX_WINDOW)
    claim = text[seg_start:seg_end]
    return re.sub(r"\s+", " ", claim).strip()


def extract_pairs(subsection_text: str):
    """[(claim_sentence, citation_key, match_start, match_end), ...] -- one row per key even
    for multi-key citations. `match_start`/`match_end` (offsets of the citation command
    itself within `subsection_text`) aren't part of the sampled CSVs, but let a later pass
    relocate the exact citation occurrence to build a highlighted paragraph -- see
    `split_paragraphs`/`highlight_span`.
    """
    pairs = []
    for m in CITE_RE.finditer(subsection_text):
        claim = _claim_window(subsection_text, m.start(), m.end())
        for key in (k.strip() for k in m.group(1).split(",")):
            if key:
                pairs.append((claim, key, m.start(), m.end()))
    return pairs


def dedup_pairs(pairs):
    """Collapse exact repeats of the same (normalized claim, key) 4-tuple."""
    seen = set()
    out = []
    for item in pairs:
        claim, key = item[0], item[1]
        norm = re.sub(r"\s+", " ", claim).strip().lower()
        dk = (norm, key)
        if dk in seen:
            continue
        seen.add(dk)
        out.append(item)
    return out


def split_paragraphs(text: str):
    """[(start, end, paragraph_text), ...] -- blank-line-separated paragraphs, with offsets."""
    bounds = [0] + [m.end() for m in re.finditer(r"\n\s*\n", text)] + [len(text)]
    paras = []
    for i in range(len(bounds) - 1):
        s, e = bounds[i], bounds[i + 1]
        if text[s:e].strip():
            paras.append((s, e, text[s:e]))
    return paras


def highlight_span(text: str, start: int, end: int, key: str = "") -> str:
    """The paragraph containing [start, end), with that citation replaced by a bold marker
    naming `key` -- clearer for a reviewer than the raw LaTeX command, and disambiguates
    which key in a multi-key \\parencite{a,b,c} this particular row is about.
    """
    marker = f"**[CITATION CHECKED: {key}]**" if key else "**[CITATION CHECKED]**"
    for p_start, p_end, p_text in split_paragraphs(text):
        if p_start <= start < p_end:
            rel_s, rel_e = start - p_start, end - p_start
            marked = p_text[:rel_s] + marker + p_text[rel_e:]
            return re.sub(r"\s+", " ", marked).strip()
    return marker + " " + _claim_window(text, start, end)  # fallback: no paragraph found


def stratified_sample(groups: dict, n: int, rng):
    """Proportional allocation (largest-remainder rounding) then SRS within each group.

    `groups`: {group_key: [item, ...]}. Returns [(group_key, item), ...], length min(n, total).
    """
    total = sum(len(v) for v in groups.values())
    if total <= n:
        return [(gk, it) for gk, items in groups.items() for it in items]

    raw = {gk: len(items) * n / total for gk, items in groups.items()}
    alloc = {gk: int(v) for gk, v in raw.items()}
    remainder = n - sum(alloc.values())
    order = sorted(raw.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True)
    guard = 0
    i = 0
    while remainder > 0 and guard < 10 * max(len(order), 1):
        gk = order[i % len(order)][0]
        if alloc[gk] < len(groups[gk]):
            alloc[gk] += 1
            remainder -= 1
        i += 1
        guard += 1

    out = []
    for gk, items in groups.items():
        k = min(alloc.get(gk, 0), len(items))
        out.extend((gk, it) for it in rng.sample(items, k))
    return out
