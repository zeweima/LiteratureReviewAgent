"""Step 0 -- materialize per-unit INPUT files (fresh, self-contained).

One entry point, three steps:
  fine    -- reads the cluster tables; one input file per fine theme (tagged findings).
  macro   -- reads fine/_fine_manifest.json + cluster_summary_macro_<name>.csv; groups
             fine sections by macro_cluster into one input per macro chapter.
  overall -- runs the ARCHITECT over macro/_macro_manifest.json to plan a paper outline
             (section kinds + per-section word ranges), materializing theme-section inputs.

Agents receive only the file PATH, never the content inline. The run steps
(`run_stage.py`, `run_overall.py`) read these.

    python prep_inputs.py --step fine    --track frontier
    python prep_inputs.py --step macro   --track frontier
    python prep_inputs.py --step overall --track frontier --title "..."

Output (per config.AGENTIC/<track>/inputs/<step>/): the per-unit .md files, a
`_units.json` index, and (overall) `_outline.json` + `_macro_digest.md`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path

import pandas as pd

import config
from latex_tmpl import slug, subsection_titles


# --------------------------------------------------------------------------- #
# data prep (re-implemented locally; NOT imported from the old pipeline)
# --------------------------------------------------------------------------- #
def build_citekeys(master: pd.DataFrame) -> dict:
    """{paper_id: 'FirstauthorlastnameYear'} with a/b/c suffixes on collision."""
    keys, used = {}, {}
    for _, r in master.iterrows():
        authors = str(r.get("authors") or "")
        first = authors.split(";")[0].split(",")[0].strip() if authors else ""
        first = "".join(ch for ch in first if ch.isalnum()) or str(r["paper_id"])[:10]
        year = "" if pd.isna(r.get("year")) else str(int(r["year"]))
        base = f"{first}{year}"
        n = used.get(base, 0)
        used[base] = n + 1
        keys[r["paper_id"]] = base if n == 0 else f"{base}{chr(ord('a') + n)}"
    return keys


def build_places(master: pd.DataFrame, in_dir: Path) -> dict:
    US = {"united states", "united states of america", "usa", "u.s.a.", "u.s.", "us", "america"}
    place, county_map, cstate = {}, {}, {}
    ca_fp = in_dir / "geo_county_assignments.csv"
    if ca_fp.exists():
        ca = pd.read_csv(ca_fp)
        for pid, g in ca.groupby("paper_id"):
            county_map[pid] = "; ".join(sorted(g["NAMELSAD"].dropna().unique()))
            sp = g["state_province"].dropna()
            if len(sp):
                cstate[pid] = str(sp.iloc[0])
    for _, r in master.iterrows():
        pid = r["paper_id"]
        country = r.get("country"); country = country if isinstance(country, str) else ""
        sp = r.get("state_province"); sp = sp if isinstance(sp, str) else ""
        if pid in county_map and county_map[pid]:
            st = cstate.get(pid) or sp
            place[pid] = f"{county_map[pid]}, {st}, USA".replace(", ,", ",")
        elif country.strip().casefold() in US and sp:
            place[pid] = f"{sp}, USA"
        elif country:
            place[pid] = country
        else:
            place[pid] = "location n/a"
    return place


def build_members(in_dir: Path, fields, clusters: pd.DataFrame) -> pd.DataFrame:
    p2c = dict(zip(clusters["paper_id"], clusters["cluster"]))
    parts = []
    for fld in fields:
        fname, col = config.FIELDS[fld]
        d = pd.read_csv(in_dir / fname).dropna(subset=[col]).rename(columns={col: "text"})
        parts.append(d[["paper_id", "text"]])
    m = pd.concat(parts, ignore_index=True)
    m = m[m["paper_id"].isin(p2c)]
    m["cluster"] = m["paper_id"].map(p2c)
    return m


def tag_findings(sub, citekey, place, scale) -> list:
    return [f"[{citekey.get(pid, pid)} | {place.get(pid, 'location n/a')} | "
            f"{scale.get(pid) if isinstance(scale.get(pid), str) else 'scale n/a'}] {txt}"
            for pid, txt in zip(sub["paper_id"], sub["text"])]


# --------------------------------------------------------------------------- #
# input-file rendering
# --------------------------------------------------------------------------- #
def render_input_md(theme, description, allowed, tagged) -> str:
    anchors = ", ".join(allowed)
    lines = [
        f"# Theme: {theme}",
        "",
        f"**Working description:** {description}".rstrip(),
        "",
        f"**Allowed citation anchors ({len(allowed)}):** {anchors}",
        "",
        f"## Findings ({len(tagged)} total)",
        "Each finding is tagged `[citation_key | location | scale]`; the "
        "citation_key is the paper to cite for THAT finding.",
        "",
    ]
    lines.extend(tagged)
    return "\n".join(lines) + "\n"


def render_synth_md(theme, description, allowed, blocks, cap_hi, focus, kind_note="",
                    guidance="", subsections=None) -> str:
    """Input file for a synth unit (macro chapter or overall section).

    `blocks` is a list of (label, body) pieces of SOURCE MATERIAL to integrate; the
    source is already-written LaTeX (with citations), NOT tagged findings. The labels
    are just content pointers -- they are material, not sub-sections of the output.
    `guidance` is the architect's per-section direction to the writer (overall step).
    `subsections` is the architect's planned \\subsection breakdown for the output --
    an ordered list of {title, guidance}; empty/None for flat-prose sections.
    """
    anchors = ", ".join(allowed)
    if cap_hi:
        hard = round(cap_hi * config.HARD_OVERFLOW)
        cap_line = (f"**Hard word limit:** the output MUST be under {hard} words -- stay "
                    "well within it; condense, do not pad.")
    else:
        cap_line = "**Word limit:** no hard limit -- prioritise coverage."
    sub_block = []
    if subsections:
        sub_block = ["**Planned subsections (organise your output with these "
                     "\\subsection titles, in order):**"]
        for i, ss in enumerate(subsections, 1):
            g = f" -- {ss['guidance']}" if ss.get("guidance") else ""
            sub_block.append(f"{i}. {ss['title']}{g}")
        sub_block.append("")
    lines = [
        f"# {theme}",
        "",
        f"**Working description:** {description}".rstrip(),
        "",
        f"**Review component focus:** {focus}".rstrip() if focus else "",
        "",
        f"**Section guidance (from the architect):** {guidance}".rstrip() if guidance else "",
        "",
        *sub_block,
        cap_line,
        "",
        f"**Allowed citation anchors ({len(allowed)}):** {anchors}",
        "",
    ]
    if kind_note:
        lines += [kind_note, ""]
    lines += [
        f"## Source material to integrate ({len(blocks)} items)",
        "This is your SOURCE MATERIAL (already-written LaTeX with citations) -- integrate "
        "and CONDENSE it into ONE coherent output. The headings below just label each piece "
        "of material; you may follow these groupings in your output structure or reorganise "
        "as the content warrants -- your judgment. Reuse ONLY the citation keys that already "
        "appear below; every claim you keep must remain attributed to the same key it "
        "carries here.",
        "",
    ]
    for label, body in blocks:
        lines += [f"### Material: {label}", "", body.strip(), ""]
    return "\n".join(ln for ln in lines) + "\n"


# --------------------------------------------------------------------------- #
# step: fine
# --------------------------------------------------------------------------- #
def prep_fine(args, in_dir: Path, out_dir: Path):
    name, fields = config.TRACKS[args.track]
    master = pd.read_csv(in_dir / "papers_master.csv")
    clusters = pd.read_csv(in_dir / f"clusters_{name}.csv")
    summ = pd.read_csv(in_dir / f"cluster_summary_{name}_labeled.csv")
    citekey = build_citekeys(master)
    keyset = sorted(set(citekey.values()))
    place = build_places(master, in_dir)
    scale = master.set_index("paper_id")["scale_category"].to_dict()
    members = build_members(in_dir, fields, clusters)

    units = []
    for _, r in summ.iterrows():
        cid = int(r["cluster"])
        theme = str(r["theme"])
        description = str(r.get("description", "") or "")
        sub = members[members["cluster"] == cid].dropna(subset=["text"])
        pids = list(sub["paper_id"])
        allowed = sorted({citekey.get(p, p) for p in pids})
        tagged = tag_findings(sub, citekey, place, scale)

        uid = f"{cid:02d}_{slug(theme)}"
        input_fp = out_dir / f"{uid}.md"
        input_fp.write_text(render_input_md(theme, description, allowed, tagged),
                            encoding="utf-8")

        macro = r.get("macro_cluster")
        units.append({
            "id": uid, "cluster": cid, "theme": theme, "description": description,
            "input_path": input_fp.resolve().as_posix(), "allowed_keys": allowed,
            "n_papers": int(r.get("n_papers", len(pids)) or len(pids)),
            "n_findings": len(tagged),
            "macro_cluster": None if pd.isna(macro) else int(macro),
            "mode": "fine",
        })
        print(f"  [{uid}] {len(tagged)} findings, {len(allowed)} anchors -> {input_fp.name}")

    index = {"track": args.track, "step": "fine", "name": name,
             "fields": fields, "keyset": keyset, "units": units}
    (out_dir / "_units.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"\nWrote {len(units)} fine input files + _units.json -> {out_dir}")


# --------------------------------------------------------------------------- #
# step: macro -- group fine sections by macro_cluster
# --------------------------------------------------------------------------- #
def prep_macro(args, in_dir: Path, out_dir: Path):
    name, _ = config.TRACKS[args.track]
    fine_man_fp = config.AGENTIC / args.track / "fine" / "_fine_manifest.json"
    if not fine_man_fp.exists():
        raise SystemExit(f"missing {fine_man_fp}\nRun the fine step first:  "
                         f"python run_stage.py --step fine --track {args.track}")
    fine_units = json.loads(fine_man_fp.read_text(encoding="utf-8"))["units"]  # {cluster: entry}
    macro_csv = pd.read_csv(in_dir / f"cluster_summary_macro_{name}.csv")
    focus = config.TRACK_FOCUS.get(args.track, "")
    cap = config.MACRO_SOFT_CAP

    # group fine entries by their macro_cluster
    by_macro = {}
    for entry in fine_units.values():
        mc = entry.get("macro_cluster")
        if mc is None:
            print(f"  (skip) fine {entry.get('id')} has no macro_cluster")
            continue
        by_macro.setdefault(int(mc), []).append(entry)

    units = []
    for _, mr in macro_csv.sort_values("macro_cluster").iterrows():
        mid = int(mr["macro_cluster"])
        kids = sorted(by_macro.get(mid, []), key=lambda e: -(e.get("n_papers") or 0))
        if not kids:
            print(f"  (skip) macro {mid}: no fine members in the manifest")
            continue
        theme = str(mr.get("macro_theme", f"Macro {mid}"))
        description = str(mr.get("macro_description", "") or "")
        allowed = sorted({k for e in kids for k in (e.get("cited") or [])})
        blocks = [(e["theme"], e.get("body") or "") for e in kids]

        uid = f"{mid:02d}_{slug(theme)}"
        input_fp = out_dir / f"{uid}.md"
        input_fp.write_text(
            render_synth_md(theme, description, allowed, blocks, cap, focus),
            encoding="utf-8")
        units.append({
            "id": uid, "cluster": mid, "theme": theme, "description": description,
            "input_path": input_fp.resolve().as_posix(), "allowed_keys": allowed,
            "n_papers": int(mr.get("n_papers", 0) or 0),
            "member_fine": [e["id"] for e in kids],
            "macro_cluster": None, "mode": "synth", "word_hi": cap,
        })
        print(f"  [{uid}] {len(kids)} sub-themes, {len(allowed)} anchors -> {input_fp.name}")

    index = {"track": args.track, "step": "macro", "name": name, "units": units}
    (out_dir / "_units.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"\nWrote {len(units)} macro input files + _units.json -> {out_dir}")


# --------------------------------------------------------------------------- #
# step: overall -- architect an outline over the macro chapters
# --------------------------------------------------------------------------- #
def prep_overall(args, in_dir: Path, out_dir: Path):
    from architect import architect_outline   # local import (SDK) keeps fine/macro light
    name, _ = config.TRACKS[args.track]
    macro_man_fp = config.AGENTIC / args.track / "macro" / "_macro_manifest.json"
    if not macro_man_fp.exists():
        raise SystemExit(f"missing {macro_man_fp}\nRun the macro step first:  "
                         f"python run_stage.py --step macro --track {args.track}")
    macro_units = json.loads(macro_man_fp.read_text(encoding="utf-8"))["units"]  # {cluster: entry}
    focus = config.TRACK_FOCUS.get(args.track, "")
    title = args.title or (f"Agricultural Subsurface (Tile) Drainage: "
                           f"{'Research Frontier' if args.track == 'frontier' else 'State of Knowledge'}")

    # digest the macro themes for the architect to read. Each macro body is
    # internally organised into \subsection{...} blocks; list those headings up
    # front so the architect can plan the paper at SUBSECTION granularity (route a
    # macro's subsections to different paper sections to avoid repetition).
    digest_fp = out_dir / "_macro_digest.md"
    dlines = [f"# Macro themes for: {title}", "",
              "Each macro theme below is INPUT MATERIAL, internally organised into "
              "subsections. Use the subsection map to route material precisely.", ""]
    for mid, e in sorted(macro_units.items(), key=lambda kv: int(kv[0])):
        subs = subsection_titles(e.get("body") or "")
        toc = "\n".join(f"  - {t}" for t in subs) or "  (no subsections)"
        dlines += [f"## Macro {mid}: {e['theme']}",
                   f"_{e.get('description','')}_", "",
                   f"Subsections of macro {mid}:", toc, "",
                   (e.get("body") or "").strip(), ""]
    digest_fp.write_text("\n".join(dlines) + "\n", encoding="utf-8")

    outline = asyncio.run(architect_outline(
        track=args.track, title=title, max_words=config.OVERALL_BUDGET,
        macro_units=macro_units, digest_path=digest_fp.resolve().as_posix(),
        focus=focus, model=args.architect_model))
    (out_dir / "_outline.json").write_text(json.dumps(outline, indent=1), encoding="utf-8")

    # materialize theme-section inputs now (their source = macro bodies, already available);
    # dependent sections (intro/cross/gaps/conclusions/abstract) are built JIT by run_overall.
    units = []
    for s in outline["sections"]:
        rec = {"id": s["id"], "kind": s["kind"], "theme": s["title"],
               "description": s.get("summary", ""), "guidance": s.get("guidance", ""),
               "subsections": s.get("subsections", []),
               "deps": s.get("deps", []),
               "source_ids": s.get("source_ids", []),
               "word_lo": s.get("word_lo"), "word_hi": s.get("word_hi"),
               "mode": "synth",
               "input_path": (out_dir / f"{s['id']}.md").resolve().as_posix()}
        if s["kind"] == "theme":
            src = [macro_units[str(k)] for k in s.get("source_ids", []) if str(k) in macro_units]
            if not src:
                src = list(macro_units.values())
            allowed = sorted({k for e in src for k in (e.get("cited") or [])})
            blocks = [(e["theme"], e.get("body") or "") for e in src]
            note = ("This is a THEME section of the overall review paper. It synthesises the "
                    "macro theme(s) above -- which may be one macro theme or several drawn "
                    "together into a single coherent section.")
            (out_dir / f"{s['id']}.md").write_text(
                render_synth_md(s["title"], s.get("summary", ""), allowed, blocks,
                                s.get("word_hi"), focus, kind_note=note,
                                guidance=s.get("guidance", ""),
                                subsections=s.get("subsections", [])),
                encoding="utf-8")
            rec["allowed_keys"] = allowed
        else:
            rec["allowed_keys"] = []   # filled by run_overall from generated section bodies
        units.append(rec)
        print(f"  [{s['id']}] kind={s['kind']} words={s.get('word_lo')}-{s.get('word_hi')}")

    index = {"track": args.track, "step": "overall", "name": name, "title": title,
             "budget": config.OVERALL_BUDGET, "units": units}
    (out_dir / "_units.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"\nWrote outline ({len(units)} sections) + _units.json -> {out_dir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--track", required=True, choices=sorted(config.TRACKS))
    ap.add_argument("--step", default="fine", choices=sorted(config.STEPS))
    ap.add_argument("--fresh", action="store_true", help="rebuild the inputs dir")
    ap.add_argument("--in-dir", default=str(config.TABLES))
    ap.add_argument("--title", default="", help="overall (c) paper title")
    ap.add_argument("--architect-model", default=config.ARCHITECT_MODEL)
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = config.AGENTIC / args.track / "inputs" / args.step
    if args.fresh and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.step == "fine":
        prep_fine(args, in_dir, out_dir)
    elif args.step == "macro":
        prep_macro(args, in_dir, out_dir)
    elif args.step == "overall":
        prep_overall(args, in_dir, out_dir)
    else:
        raise SystemExit(f"unknown step {args.step!r}")


if __name__ == "__main__":
    main()
