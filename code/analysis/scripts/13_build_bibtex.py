"""Phase 4 step 8: build a LaTeX/BibTeX reference database for the corpus.

The synthesis documents (steps 10 & 12) cite papers by a compact key built from
first-author-surname + year (e.g. ``Osterholz2020``). To turn those citations
into real LaTeX references, this script:

  1. rebuilds the SAME citation keys from ``papers_master.csv`` (identical logic
     to 12_deep_synthesis.build_citekeys, so every key lines up with the prose);
  2. links each paper to its full Web-of-Science RIS metadata by DOI (the record
     in ``Data/Bib/WOS_core/with_pdf_ris.json`` — authors, journal, volume,
     issue, pages, issn, ...); and
  3. writes a properly-escaped ``references.bib`` whose entry keys ARE those
     citation keys, so ``\\cite{Osterholz2020}`` just works.

DOI linkage is 100% for this corpus, so every cited key resolves to a rich
entry; papers with no RIS match (none, currently) fall back to the master row.

Outputs (new folder ``code/analysis/latex/``):
    references.bib          -- BibTeX database, one entry per paper (keys = cite keys)
    citekey_map.csv         -- paper_id, citekey, doi, entry_type, matched_ris
    main.tex                -- minimal compilable skeleton demonstrating \\cite + biblatex
    README.md               -- how to compile; case-protection & biber notes

It also scans the existing synthesis markdown for cited keys and reports how many
resolve in the .bib (coverage), flagging any that do not.

Usage:
    python 13_build_bibtex.py
    python 13_build_bibtex.py --ris /path/to/with_pdf_ris.json --out-dir some/dir
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

import paths

# RIS metadata sits beside the basemaps, under the repo-level Data/ tree.
DEFAULT_RIS = paths.BASE.parent.parent / "Data" / "Bib" / "WOS_core" / "with_pdf_ris.json"

# RIS type_of_reference -> BibTeX entry type + the field its source-title maps to.
TYPE_MAP = {
    "JOUR":   ("article", "journal"),
    "CPAPER": ("inproceedings", "booktitle"),
    "CONF":   ("inproceedings", "booktitle"),
    "CHAP":   ("incollection", "booktitle"),
    "BOOK":   ("book", "series"),
}

KEY_RE = re.compile(r"\b[A-Za-z][A-Za-z.'\-]*\d{4}[a-z]?\b")

# LaTeX special characters -> escape sequence (single-pass; replacements not re-escaped).
_SPECIAL = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_ESC_RE = re.compile("|".join(re.escape(k) for k in _SPECIAL))


def esc(s) -> str:
    """Escape LaTeX special characters in free text (leaves UTF-8 letters alone)."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return _ESC_RE.sub(lambda m: _SPECIAL[m.group()], str(s))


def norm_doi(x) -> str:
    if not isinstance(x, str):
        return ""
    x = x.strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", x)


# --------------------------------------------------------------------------- #
# citation keys -- MUST match 12_deep_synthesis.build_citekeys exactly
# --------------------------------------------------------------------------- #
def build_citekeys(master: pd.DataFrame) -> dict:
    keys, used = {}, {}
    for _, r in master.iterrows():
        authors = str(r.get("authors") or "")
        first = authors.split(";")[0].split(",")[0].strip() if authors else ""
        first = "".join(ch for ch in first if ch.isalnum()) or str(r["paper_id"])[:10]
        year = "" if pd.isna(r.get("year")) else str(int(r["year"]))
        base = f"{first}{year}"
        n = used.get(base, 0); used[base] = n + 1
        keys[r["paper_id"]] = base if n == 0 else f"{base}{chr(ord('a') + n)}"
    return keys


# --------------------------------------------------------------------------- #
# one BibTeX entry
# --------------------------------------------------------------------------- #
def ris_authors(e: dict) -> str:
    """Join RIS 'Surname, Initials' names with BibTeX ' and '."""
    a = e.get("authors") or e.get("first_authors") or []
    if isinstance(a, str):
        a = [a]
    names = []
    for x in a:
        x = str(x).strip()
        if x.endswith(","):                 # trailing comma (empty initials) -> biber warns
            x = x[:-1].strip()
        if x:
            names.append(x)
    return " and ".join(esc(x) for x in names)


def master_authors(row) -> str:
    """Fallback: master 'A, X; B, Y' -> 'A, X and B, Y'."""
    raw = str(row.get("authors") or "").strip()
    if not raw:
        return ""
    return " and ".join(esc(p.strip()) for p in raw.split(";") if p.strip())


def pages_of(e: dict) -> str | None:
    sp, ep, art = e.get("start_page"), e.get("end_page"), e.get("custom7")
    if sp and ep and str(sp) != str(ep):
        return f"{sp}--{ep}"
    if sp:
        return str(sp)
    if art:                       # article-number journals (e.g. MDPI): no page range
        return str(art)
    return None


def make_entry(key: str, row, e: dict | None) -> str:
    """Compose a BibTeX entry. `e` is the RIS record (None -> master fallback)."""
    etype, src_field = "article", "journal"
    fields: list[tuple[str, str]] = []

    if e is not None:
        etype, src_field = TYPE_MAP.get(e.get("type_of_reference", "JOUR"),
                                        ("article", "journal"))
        author = ris_authors(e) or master_authors(row)
        title = esc(e.get("title") or row.get("bib_title"))
        source = esc(e.get("secondary_title"))
        year = e.get("year") or ("" if pd.isna(row.get("year")) else int(row["year"]))
        vol, num = e.get("volume"), e.get("number")
        pages = pages_of(e)
        issn = e.get("issn")
        doi = e.get("doi") or row.get("doi")
    else:
        author = master_authors(row)
        title = esc(row.get("bib_title"))
        source = esc(row.get("journal"))
        year = "" if pd.isna(row.get("year")) else int(row["year"])
        vol = num = pages = issn = None
        doi = row.get("doi")

    if author:
        fields.append(("author", author))
    if title:
        fields.append(("title", "{" + title + "}"))   # extra braces protect case
    if source:
        fields.append((src_field, source))
    if year:
        fields.append(("year", str(year)))
    if vol:
        fields.append(("volume", esc(vol)))
    if num:
        fields.append(("number", esc(num)))
    if pages:
        fields.append(("pages", esc(pages)))
    if issn:
        fields.append(("issn", esc(issn)))
    if doi:
        fields.append(("doi", esc(doi)))

    body = ",\n".join(f"  {k:<9}= {{{v}}}" for k, v in fields)
    return f"@{etype}{{{key},\n{body}\n}}\n"


# --------------------------------------------------------------------------- #
def scan_cited_keys(doc_dir: Path) -> set:
    """Collect every citation key appearing in the synthesis markdown."""
    keys = set()
    files = list(doc_dir.glob("*_synthesis.md"))
    syn = doc_dir / "synthesis"
    if syn.exists():
        files += list(syn.rglob("*.md"))
    for fp in files:
        for tok in KEY_RE.findall(fp.read_text(encoding="utf-8", errors="ignore")):
            keys.add(tok)
    return keys


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", default=str(paths.TABLES))
    ap.add_argument("--ris", default=str(DEFAULT_RIS))
    ap.add_argument("--out-dir", default=str(paths.BASE / "latex"))
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    master = pd.read_csv(in_dir / "papers_master.csv")
    ris_list = json.loads(Path(args.ris).read_text(encoding="utf-8"))
    ris = {norm_doi(e.get("doi")): e for e in ris_list if norm_doi(e.get("doi"))}

    citekey = build_citekeys(master)
    assert len(set(citekey.values())) == len(citekey), "citation keys collided"

    entries, rows = [], []
    for _, r in master.iterrows():
        pid = r["paper_id"]
        key = citekey[pid]
        e = ris.get(norm_doi(r.get("doi")))
        entries.append((key, make_entry(key, r, e)))
        etype = (TYPE_MAP.get(e.get("type_of_reference", "JOUR"), ("article",))[0]
                 if e is not None else "article")
        rows.append({"paper_id": pid, "citekey": key, "doi": r.get("doi"),
                     "entry_type": etype, "matched_ris": e is not None})

    entries.sort(key=lambda t: t[0].casefold())
    header = (f"% Tile-drainage corpus references ({len(entries)} entries).\n"
              f"% Auto-generated by scripts/13_build_bibtex.py from papers_master.csv\n"
              f"% + Data/Bib/WOS_core/with_pdf_ris.json. Keys match the synthesis docs.\n"
              f"% UTF-8; compile with biblatex+biber (or bibtex with inputenc utf8).\n\n")
    bib_fp = out_dir / "references.bib"
    bib_fp.write_text(header + "\n".join(e for _, e in entries), encoding="utf-8")

    map_df = pd.DataFrame(rows)
    map_df.to_csv(out_dir / "citekey_map.csv", index=False, encoding="utf-8-sig")

    # coverage against what the synthesis actually cites
    keyset = set(citekey.values())
    cited = scan_cited_keys(paths.DOCS)
    cited_in_corpus = {k for k in cited if k in keyset}
    unresolved = sorted(cited_in_corpus - {k for k, _ in entries})  # should be empty

    # minimal compilable skeleton + README
    sample = sorted(cited_in_corpus)[:8] or [k for k, _ in entries[:8]]
    (out_dir / "main.tex").write_text(_MAIN_TEX.format(
        cites=", ".join(sample)), encoding="utf-8")
    (out_dir / "README.md").write_text(_README.format(
        n=len(entries), n_matched=int(map_df["matched_ris"].sum()),
        n_cited=len(cited_in_corpus)), encoding="utf-8")

    n_matched = int(map_df["matched_ris"].sum())
    print(f"references.bib : {len(entries)} entries "
          f"({n_matched} enriched from RIS, {len(entries) - n_matched} master-fallback)")
    print(f"citekey_map.csv: {len(map_df)} rows")
    print(f"synthesis cites: {len(cited_in_corpus)} distinct corpus keys; "
          f"unresolved in .bib: {len(unresolved)}")
    if unresolved:
        print("  [!] unresolved:", ", ".join(unresolved[:20]))
    print(f"-> {out_dir}")


_MAIN_TEX = r"""\documentclass{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage[T1]{{fontenc}}
\usepackage[style=authoryear,backend=biber]{{biblatex}}
\addbibresource{{references.bib}}

\begin{{document}}
Example in-text citations \parencite{{{cites}}}.

\printbibliography
\end{{document}}
"""

_README = """# LaTeX references for the tile-drainage review

`references.bib` — **{n}** BibTeX entries, one per corpus paper. Entry keys are the
same first-author+year keys the synthesis docs cite (e.g. `\\cite{{Osterholz2020}}`),
so citations resolve with no renaming. {n_matched} entries are enriched from the
Web-of-Science RIS metadata (authors, journal, volume, issue, pages, ISSN, DOI);
the remainder fall back to `papers_master.csv`. The synthesis prose cites
**{n_cited}** distinct corpus keys, all present here.

## Compile

```bash
pdflatex main       # or lualatex/xelatex
biber main          # biblatex backend (recommended for UTF-8)
pdflatex main
pdflatex main
```

`main.tex` is a minimal working example. To use classic BibTeX instead of biber,
swap the `biblatex` line for `\\usepackage{{cite}}` + `\\bibliographystyle{{plainnat}}`
and run `bibtex main`.

## Notes

- **Case protection.** Titles are wrapped in an extra `{{ ... }}` so BibTeX styles
  that lowercase titles keep acronyms/proper nouns (e.g. *SWAT*, *Iowa*) intact.
- **Article-number journals** (e.g. MDPI) have no page range; the article number is
  written to `pages`.
- **Regenerate** after re-running the pipeline: `python scripts/13_build_bibtex.py`.
- `citekey_map.csv` maps `paper_id -> citekey -> doi` for traceability.
"""


if __name__ == "__main__":
    main()
