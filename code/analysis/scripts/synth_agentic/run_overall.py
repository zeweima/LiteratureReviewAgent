"""Overall (step c) -- staged, dependency-ordered runner + paper assembly.

Reads the architect outline (`inputs/overall/_units.json`) and generates sections in
three dependency stages, then assembles them into one compilable paper:

  stage 1  theme sections           (grounded in their macro themes; inputs already
                                      materialized by prep_inputs)
  stage 2  intro/conclusions + any  (grounded in ALL theme-section bodies; inputs built
           architect synthesis secs  just-in-time here from stage-1 output)
  stage 3  abstract                 (grounded in intro + conclusions bodies)

Sections are grouped by dependency stage via architect.section_stage(kind), so the
architect may add synthesis sections of any kind without changing this runner.

Each section is one orchestrator (`run_unit`, step="overall", mode="synth") with the
architect's per-section word range. Then `overall/paper/{main.tex,main.pdf,sections/*}`
is written and compiled.  Cache = per-section final.pdf (skip unless --fresh).

    python prep_inputs.py --step overall --track frontier --title "..."
    python run_overall.py --track frontier
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

import config
from orchestrator import run_unit
from prep_inputs import render_synth_md
from architect import section_stage, section_allow_cite, section_use_subsections
from latex_tmpl import preamble, esc
from texcompile import compile_doc, remove_stray_nul
from sdk_agents import install_quiet_exception_handler


def _fail_entry(u, e):
    return {"cluster": None, "id": u["id"], "theme": u.get("theme", u["id"]),
            "kind": u.get("kind"), "description": u.get("description", ""),
            "status": f"error: {type(e).__name__}", "compiled": None,
            "review_passed": None, "body": "", "cited": []}


async def _run_stage(section_units, sem_units, sem_sub, args):
    """Run a set of sections concurrently; a section that raises becomes a fail
    entry so the stage (and the paper) still completes."""
    results = await asyncio.gather(
        *[_run_section(u, sem_units, sem_sub, args) for u in section_units],
        return_exceptions=True)
    out = []
    for u, r in zip(section_units, results):
        if isinstance(r, BaseException):
            print(f"  [error] {u['id']}: {type(r).__name__}: {r}")
            out.append(_fail_entry(u, r))
        else:
            out.append(r)
    return out


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s); st.flush()

    def flush(self):
        for st in self.streams:
            st.flush()


def _setup_logging(log_fp: Path):
    log_fp.parent.mkdir(parents=True, exist_ok=True)
    f = log_fp.open("w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, f)
    sys.stderr = _Tee(sys.__stderr__, f)


def _role(kind, title, theme):
    """A description of the section's role in the paper, given to the generator."""
    P = f'the review paper "{title}"'
    if kind == "abstract":
        return (f"the ABSTRACT of {P}. In a single flowing paragraph, state the scope of "
                "the review, the main themes and findings it covers, and the key takeaways. "
                "It must be self-contained and high-level -- no citations, no headings, no "
                "lists, and do not refer to \"this section\".")
    if kind == "intro":
        return (f"the INTRODUCTION of {P}. Motivate the topic and why it matters, give the "
                "needed background, state the scope and aims of the review, and briefly "
                "preview how the paper is organised. It should read as the OPENING of a "
                "paper -- flowing prose, not a point-by-point summary of the source sections.")
    if kind == "conclusions":
        return (f"the CONCLUSIONS of {P}. Draw together the main messages across the whole "
                "paper, state the overall takeaways and their implications, and close the "
                "paper. Do NOT introduce new evidence or citations; write flowing prose.")
    if section_stage(kind) == 1:                    # a theme / body section
        return (f'the thematic section "{theme}" of {P}, synthesising its source material '
                "into a coherent part of the paper.")
    # any other stage-2 synthesis section the architect chose (e.g. cross-cutting)
    return (f'the "{title}" synthesis section of {P} -- draw across the paper\'s theme '
            "sections to develop the connections, tensions, or patterns it names, without "
            "repeating what the individual theme sections already cover.")


def _run_section(unit, sem_units, sem_sub, args):
    kind = unit.get("kind", "")
    return run_unit(unit, sem_units, sem_sub, track=args.track, step="overall",
                    orch_model=args.orch_model, gen_model=args.gen_model,
                    review_model=args.review_model, fresh=args.fresh, mode="synth",
                    word_lo=unit.get("word_lo"), word_hi=unit.get("word_hi"),
                    allow_cite=section_allow_cite(kind),
                    use_subsections=section_use_subsections(kind),
                    role=unit.get("role", ""), paper=True)


def _build_dep_input(unit, source_entries, focus, note):
    """Materialize a dependent section's input .md from already-generated bodies."""
    allowed = sorted({k for e in source_entries for k in (e.get("cited") or [])})
    blocks = [(e["theme"], e.get("body") or "") for e in source_entries]
    Path(unit["input_path"]).write_text(
        render_synth_md(unit["theme"], unit.get("description", ""), allowed, blocks,
                        unit.get("word_hi"), focus, kind_note=note,
                        guidance=unit.get("guidance", ""),
                        subsections=unit.get("subsections", [])),
        encoding="utf-8")
    unit["allowed_keys"] = allowed
    return allowed


def _assemble(track, title, ordered, out_dir):
    """Write sections/*.tex + main.tex and compile the whole-paper PDF."""
    paper = out_dir / "paper"
    sec_dir = paper / "sections"
    build = paper / "_build"
    sec_dir.mkdir(parents=True, exist_ok=True)
    build.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(config.BIB, paper / "references.bib")
    except Exception:
        pass

    inputs = []
    for e in ordered:
        body = (e.get("body") or "").strip()
        if not body:
            continue
        sid = e["id"]
        (sec_dir / f"{sid}.tex").write_text(
            f"\\section{{{esc(e['theme'])}}}\n\n{body}\n", encoding="utf-8")
        inputs.append(f"\\input{{sections/{sid}.tex}}")

    main_tex = paper / "main.tex"
    main_tex.write_text(
        preamble(config.BIB.resolve().as_posix())
        + f"\\title{{{esc(title)}}}\n\\author{{}}\n\\date{{}}\n"
        + "\\begin{document}\n\\maketitle\n\n"
        + "\n".join(inputs)
        + "\n\n\\printbibliography\n\\end{document}\n",
        encoding="utf-8")

    ok, errors, logtext = compile_doc(main_tex, build)
    (paper / "main_compile.log").write_text(logtext or errors or "", encoding="utf-8")
    pdf = build / "main.pdf"
    if pdf.exists():
        shutil.copyfile(pdf, paper / "main.pdf")
    remove_stray_nul(paper)
    print(f"[assemble] {len(inputs)} sections -> paper/main.pdf "
          f"{'OK' if ok else 'FAILED: ' + (errors or '')[:200]}")
    return bool(ok)


async def _amain(args):
    focus = config.TRACK_FOCUS.get(args.track, "")
    units_fp = config.AGENTIC / args.track / "inputs" / "overall" / "_units.json"
    if not units_fp.exists():
        sys.exit(f"missing {units_fp}\nRun:  python prep_inputs.py --step overall "
                 f"--track {args.track}")
    idx = json.loads(units_fp.read_text(encoding="utf-8"))
    title = idx.get("title", "Review")
    for u in idx["units"]:                      # tell each section its role in the paper
        u["role"] = _role(u.get("kind", ""), title, u.get("theme", ""))
    units = {u["id"]: u for u in idx["units"]}
    order = idx["units"]                       # reading order for assembly

    install_quiet_exception_handler()
    sem_units = asyncio.Semaphore(args.concurrency)
    sem_sub = asyncio.Semaphore(config.SUBAGENT_CONCURRENCY)
    done = {}                                  # id -> entry

    print(f"[run_overall] track={args.track} title={title!r} sections={len(order)} "
          f"fresh={args.fresh}")

    # -- stage 1: theme sections (read raw macros; inputs already materialized) - #
    themes = [u for u in order if section_stage(u["kind"]) == 1]
    print(f"[stage 1] {len(themes)} theme sections")
    for u, e in zip(themes, await _run_stage(themes, sem_units, sem_sub, args)):
        done[u["id"]] = e
    theme_entries = [done[u["id"]] for u in themes]

    # -- stage 2: intro/conclusions + any synthesis sections (read theme bodies)  #
    s2 = [u for u in order if section_stage(u["kind"]) == 2]
    print(f"[stage 2] {len(s2)} synthesis sections")
    note2 = ("This section of the overall paper is grounded in the theme sections above; "
             "synthesise across them and cite ONLY the keys they use.")
    for u in s2:
        _build_dep_input(u, theme_entries, focus, note2)
    for u, e in zip(s2, await _run_stage(s2, sem_units, sem_sub, args)):
        done[u["id"]] = e

    # -- stage 3: abstract (grounded in intro + conclusions) ------------------- #
    s3 = [u for u in order if section_stage(u["kind"]) == 3]
    if s3:
        print(f"[stage 3] {len(s3)} abstract")
        src = [done[u["id"]] for u in order
               if u["kind"] in ("intro", "conclusions") and u["id"] in done] or theme_entries
        note3 = ("Write the paper's abstract, grounded in the introduction and conclusions "
                 "above; cite sparingly and only keys they use.")
        for u in s3:
            _build_dep_input(u, src, focus, note3)
        for u, e in zip(s3, await _run_stage(s3, sem_units, sem_sub, args)):
            done[u["id"]] = e

    # -- assemble the paper ---------------------------------------------------- #
    out_dir = config.AGENTIC / args.track / "overall"
    ordered = [done[u["id"]] for u in order if u["id"] in done]
    paper_ok = _assemble(args.track, title, ordered, out_dir)

    man = {"track": args.track, "step": "overall", "title": title,
           "paper_pdf": "paper/main.pdf", "paper_compiled": paper_ok,
           "sections": {u["id"]: done.get(u["id"]) for u in order}}
    (out_dir / "_overall_manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")

    n = len(ordered)
    compiled = sum(1 for e in ordered if e.get("compiled"))
    passed = sum(1 for e in ordered if e.get("review_passed"))
    t_in = sum((done.get(u["id"], {}).get("tokens") or {}).get("in", 0) for u in order)
    t_out = sum((done.get(u["id"], {}).get("tokens") or {}).get("out", 0) for u in order)
    t_cost = sum((done.get(u["id"], {}).get("tokens") or {}).get("cost_usd", 0.0) for u in order)
    print(f"\n[summary] {n} sections | compiled {compiled}/{n} | review-passed {passed}/{n} "
          f"| paper {'OK' if paper_ok else 'FAILED'}")
    print(f"[tokens] total: in={t_in} out={t_out} cost=${t_cost:.4f}")
    for u in order:
        e = done.get(u["id"], {})
        tk = e.get("tokens") or {}
        print(f"  {u['id']} [{u['kind']}]: status={e.get('status')} "
              f"compiled={e.get('compiled')} review={e.get('review_passed')} "
              f"words={len((e.get('body') or '').split())} "
              f"tokens(in/out)={tk.get('in', 0)}/{tk.get('out', 0)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--track", required=True, choices=sorted(config.TRACKS))
    ap.add_argument("--fresh", action="store_true", help="ignore cache; rebuild every section")
    ap.add_argument("--concurrency", type=int, default=config.CONCURRENCY)
    ap.add_argument("--orch-model", default=config.ORCH_MODEL)
    ap.add_argument("--gen-model", default=config.GEN_MODEL)
    ap.add_argument("--review-model", default=config.REVIEW_MODEL)
    args = ap.parse_args()

    _setup_logging(config.AGENTIC / args.track / "overall" / "_run.log")
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
