"""The orchestrator's tools: generate / review / report (fresh, self-contained).

- generate  -- spawns a Write/Edit sub-agent that maintains ONE evolving output
               file (section.tex): it is WRITTEN once, then EDITED in place on
               every later pass (never a new file). generate OWNS compilation (a
               bounded edit->compile->fix loop, pure-Python latexmk) and returns a
               short status; it never returns the LaTeX body.
- review    -- spawns a Read sub-agent that judges TWO things only: faithfulness
               to the sources, and correct reference matching.
- report    -- promotes the current section.tex to final.tex/final.pdf.

Per-attempt compile logs are NOT persisted (kept in memory for the fix loop).
The unit dir holds only meaningful artifacts. A fresh server is built per unit.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass

from claude_agent_sdk import tool, create_sdk_mcp_server

import config
from latex_tmpl import (
    wrap_section_doc, clean_body, sanitize_cites, cited_keys, CITE_RULES,
)
from texcompile import compile_doc, dollar_balance_issues
from sdk_agents import call_agent


# --------------------------------------------------------------------------- #
# system prompts
# --------------------------------------------------------------------------- #
GEN_SYSTEM = (
    "You write ONE in-depth thematic section BODY of an academic review on "
    "agricultural subsurface (tile) drainage, in LaTeX. You are given the path to a "
    "findings file (theme, allowed citation anchors, and finding statements each "
    "tagged '[citation_key | location | scale]') and the absolute path of the output "
    "file. Read the findings file, then produce a thorough, well-structured LaTeX "
    "section BODY synthesising the theme using ONLY those findings. RETAIN AND CONVEY "
    "AS MUCH OF THE SUPPLIED INFORMATION AS POSSIBLE -- maximal coverage and high "
    "information density: incorporate quantitative results, magnitudes, locations, "
    "scales, methods, and conditions; group related findings rather than dropping "
    "them. BEGIN the body with \\subsection{Overview} framing the theme (scope, main "
    "lines of evidence, headline findings), then organise details with further "
    "\\subsection{...} (and \\subsubsection{...} where helpful). " + CITE_RULES + " "
    "Every citation MUST be one of the allowed anchors. Output ONLY the inner LaTeX "
    "body -- no \\documentclass, preamble, \\begin{document}, top-level \\section "
    "title, or \\printbibliography, and no commentary. Write valid, COMPILABLE LaTeX "
    "(balanced $, braces, and environments). Maintain the SINGLE output file: create "
    "it once with Write, and on every later pass UPDATE it in place with Edit -- do "
    "NOT create a new file and do NOT print the body in chat."
)

REVIEW_SYSTEM = (
    "You are a STRICT reviewer of one LaTeX section of an academic review on "
    "agricultural subsurface (tile) drainage. You will READ two files: the generated "
    "section and the SOURCE findings (each tagged '[citation_key | location | "
    "scale]'). Check EXACTLY TWO things.\n"
    "(1) FAITHFULNESS (anti-hallucination): every CONCRETE claim -- a number, "
    "statistic, location, study result, mechanism, or finding -- must be supported by "
    "the SOURCE findings. Flag unsupported, overstated, or invented claims, quoting "
    "the offending text. Do NOT flag general domain background, definitions, or "
    "connective/framing sentences, and do NOT flag mere summarising/condensing/"
    "omission -- a review selects.\n"
    "(2) REFERENCE MATCHING: every \\parencite/\\textcite must attribute the claim to "
    "the CORRECT paper -- the citation_key of the finding(s) that claim comes from. "
    "Flag mismatched or swapped keys, quoting the text and naming the expected key.\n"
    "Ignore style, structure, length, and compilation. Respond with STRICT JSON only "
    "and nothing else: "
    '{"passed": true|false, "unfaithful": ["..."], "miscited": ["..."]}. '
    "Set passed=false if EITHER list is non-empty; use [] when clean. "
    "Each item must quote the offending text."
)

REVIEW_TASK = (
    "Check (1) faithfulness of every concrete claim to the findings, and (2) that "
    "each citation key is the correct paper for its claim."
)

# --- synth mode (macro step b / overall step c): integrate + CONDENSE -------- #
# The source is no longer tagged findings but already-written LaTeX SECTIONS (the
# tier below), which already carry \parencite/\textcite keys. The generator must
# integrate and condense them under a SOFT word cap; the reviewer's two checks are
# unchanged, only the "source" wording differs.
# macro (b): a pure SYNTHESIS of the fine sections -- not paper writing.
GEN_SYSTEM_SYNTH = (
    "You write ONE integrated, higher-level section BODY of an academic review on "
    "agricultural subsurface (tile) drainage, in LaTeX. You are given the path to a "
    "SOURCE file containing already-written source material (LaTeX text that already uses "
    "\\parencite/\\textcite citations) and the absolute path of the output "
    "file. Read the source, then SYNTHESISE and CONDENSE it into a single coherent "
    "section: integrate across the material, group and abstract shared points, keep "
    "the most important quantitative results, mechanisms, locations, and conditions, and "
    "cut repetition -- this is a distillation, not a concatenation. Preserve the key "
    "evidence and every citation that supports a retained claim. BEGIN the body with "
    "\\subsection{Overview} framing the theme (scope, main lines of evidence, headline "
    "points), then organise with further \\subsection{...} (and \\subsubsection{...} "
    "where helpful). " + CITE_RULES + " Every citation MUST be one of the allowed anchors "
    "(the keys already present in the source). Output ONLY the inner LaTeX body -- no "
    "\\documentclass, preamble, \\begin{document}, top-level \\section title, or "
    "\\printbibliography, and no commentary. Write valid, COMPILABLE LaTeX (balanced $, "
    "braces, and environments). Maintain the SINGLE output file: create it once with "
    "Write, and on every later pass UPDATE it in place with Edit -- do NOT create a new "
    "file and do NOT print the body in chat."
)

# overall (c): writing ONE section of an actual review PAPER (role-aware).
GEN_SYSTEM_PAPER = (
    "You write ONE specific SECTION of an academic review PAPER on agricultural subsurface "
    "(tile) drainage, in LaTeX. The task instruction names WHICH section this is (e.g. "
    "abstract, introduction, a thematic section, cross-cutting synthesis, research gaps, "
    "conclusions) and its role -- write it to READ AS THAT SECTION and to fit a single "
    "coherent paper, not as a standalone document. You are given the path to a SOURCE file "
    "(already-written source material that carries \\parencite/\\textcite citations) and the "
    "output-file path. Read the source, then produce the requested section grounded in it: "
    "for a THEMATIC section, synthesise and CONDENSE the material (integrate, abstract "
    "shared points, keep the key quantitative results, mechanisms, locations, and "
    "conditions, cut repetition -- a distillation, not a concatenation); for a FRAMING "
    "section (abstract, introduction, conclusions) write cohesive prose that frames the "
    "paper as a whole rather than re-summarising each source in turn. Preserve the key "
    "evidence and every citation that supports a retained claim. Follow the SECTION ROLE, "
    "STRUCTURE, and CITATION directives in the task instruction; by default begin the body "
    "with \\subsection{Overview} framing the section (scope, main lines of evidence, "
    "headline points), then organise with further \\subsection{...} (and \\subsubsection{...} "
    "where helpful). " + CITE_RULES + " When you cite, every citation MUST be one of the "
    "allowed anchors (the keys already present in the source). Output ONLY the inner LaTeX "
    "body -- no \\documentclass, preamble, \\begin{document}, top-level \\section title, or "
    "\\printbibliography, and no commentary. Write valid, COMPILABLE LaTeX (balanced $, "
    "braces, and environments). Maintain the SINGLE output file: create it once with "
    "Write, and on every later pass UPDATE it in place with Edit -- do NOT create a new "
    "file and do NOT print the body in chat."
)

REVIEW_SYSTEM_SYNTH = (
    "You are a STRICT reviewer of one integrated LaTeX section of an academic review on "
    "agricultural subsurface (tile) drainage. You will READ two files: the generated "
    "section and its SOURCE -- the already-written material it integrates. BOTH carry "
    "\\parencite/\\textcite citations; the generated section is a CONDENSATION of the "
    "source, so both its content and its citations must come from the source. Check "
    "EXACTLY TWO things.\n"
    "(1) FAITHFULNESS (anti-hallucination): every CONCRETE claim -- a number, statistic, "
    "location, study result, mechanism, or finding -- must be supported by the SOURCE. "
    "Flag unsupported, overstated, or invented claims, quoting the offending text. Do NOT "
    "flag general domain background, definitions, or connective/framing sentences, and do "
    "NOT flag mere summarising/condensing/omission -- a synthesis selects and abstracts.\n"
    "(2) CITATION PROVENANCE: every \\parencite/\\textcite in the GENERATED section must "
    "trace back to the SOURCE. For each cited claim in the generated section, find where "
    "that citation key appears in the SOURCE and confirm the source cites the SAME key for "
    "content that SUPPORTS the generated claim. Flag a citation when (a) the key does not "
    "appear in the source at all, (b) the source uses that key for different content that "
    "does not support the generated claim, or (c) the generated claim is attributed to a "
    "key the source does not attach to it (a swapped or borrowed key). In short: every "
    "cited claim in the generated content must come from the source and be supported there "
    "under the SAME key. Quote the generated text and name the offending key.\n"
    "Ignore style, structure, length, and compilation. Respond with STRICT JSON only and "
    "nothing else: "
    '{"passed": true|false, "unfaithful": ["..."], "miscited": ["..."]}. '
    'Put faithfulness problems in "unfaithful" and citation-provenance problems in '
    '"miscited". Set passed=false if EITHER list is non-empty; use [] when clean. '
    "Each item must quote the offending text."
)

REVIEW_TASK_SYNTH = (
    "Check (1) faithfulness of every concrete claim to the SOURCE material, and "
    "(2) citation provenance -- every citation in the generated section must trace to the "
    "SOURCE, with the SAME key supporting the SAME claim there (not merely a plausible "
    "paper)."
)

OUTPUT_NAME = "section.tex"   # the single evolving output file


# --------------------------------------------------------------------------- #
# per-unit context + state
# --------------------------------------------------------------------------- #
@dataclass
class UnitCtx:
    uid: str
    theme: str
    input_path: str          # absolute posix path to inputs/<step>/<id>.md
    out_dir: object          # Path
    build_dir: object        # Path
    allowed: set             # allowed citation anchors
    bib_path: str            # absolute posix path to references.bib
    sem: object              # shared sub-agent asyncio.Semaphore
    gen_model: str = config.GEN_MODEL
    review_model: str = config.REVIEW_MODEL
    mode: str = "fine"       # "fine" (max coverage) | "synth" (integrate + condense)
    word_lo: int = None      # soft-cap lower bound (words); None = no lower bound
    word_hi: int = None      # soft-cap upper bound (words); None = no cap
    word_hard: int = None    # HARD cap (words); generator is forced under it. None = off
    track_focus: str = ""    # the review COMPONENT's lens (knowledge vs frontier)
    allow_cite: bool = True   # False -> citation-free section (abstract, conclusions)
    use_subsections: bool = True   # False -> flat prose (intro, conclusions, abstract)
    role: str = ""            # what section this is + how it should read (overall c)
    paper: bool = False       # True only for overall (c): write as a section of a paper


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def _extract_json(text: str):
    """Best-effort pull of a JSON object from an agent reply."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        starts = [i for i, c in enumerate(text) if c == "{"]
        for s in starts:
            for e in range(len(text) - 1, s, -1):
                if text[e] == "}":
                    try:
                        return json.loads(text[s:e + 1])
                    except Exception:
                        continue
    return None


def _section_body(ctx: UnitCtx) -> str:
    fp = ctx.out_dir / OUTPUT_NAME
    if not fp.exists():
        return ""
    body = clean_body(fp.read_text(encoding="utf-8", errors="replace"))
    body, _ = sanitize_cites(body, ctx.allowed)
    return body


# --------------------------------------------------------------------------- #
# finalize (shared by the report tool and the orchestrator's fallback)
# --------------------------------------------------------------------------- #
def finalize(ctx: UnitCtx, state: dict, accepted_attempt, status: str):
    body = _section_body(ctx)                    # the single evolving file is the truth
    (ctx.out_dir / OUTPUT_NAME).write_text(body, encoding="utf-8")   # normalized

    # co-locate the bib so final.tex is a portable, compilable deliverable
    try:
        shutil.copyfile(config.BIB, ctx.out_dir / "references.bib")
    except Exception:
        pass

    final_tex = ctx.out_dir / "final.tex"
    final_tex.write_text(wrap_section_doc(ctx.theme, body, ctx.bib_path), encoding="utf-8")
    (ctx.out_dir / "body.tex").write_text(body, encoding="utf-8")    # raw body for b/c reuse
    ok, errors, logtext = compile_doc(final_tex, ctx.build_dir)
    (ctx.out_dir / "final_compile.log").write_text(logtext or errors or "", encoding="utf-8")
    pdf = ctx.build_dir / "final.pdf"
    if pdf.exists():
        shutil.copyfile(pdf, ctx.out_dir / "final.pdf")

    valid, _ = cited_keys(body, ctx.allowed)
    result = {
        "id": ctx.uid, "theme": ctx.theme, "state": "done", "status": status,
        "accepted_attempt": accepted_attempt, "compiled": bool(ok),
        "words": len(body.split()), "cited": valid,
        "review_passed": state["attempts"].get(accepted_attempt, {}).get("review_passed"),
    }
    state["final"] = result
    (ctx.out_dir / "status.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result, bool(ok)


# --------------------------------------------------------------------------- #
# server builder
# --------------------------------------------------------------------------- #
def build_server(ctx: UnitCtx):
    state = {"attempts": {}, "best": None, "final": None}
    target = ctx.out_dir / OUTPUT_NAME
    target_abs = target.resolve().as_posix()

    # prompt selection: fine = max coverage; macro = synthesis; overall = paper section
    synth = (ctx.mode != "fine")
    gen_system = GEN_SYSTEM if not synth else (GEN_SYSTEM_PAPER if ctx.paper else GEN_SYSTEM_SYNTH)
    review_system = REVIEW_SYSTEM_SYNTH if synth else REVIEW_SYSTEM
    review_task = REVIEW_TASK_SYNTH if synth else REVIEW_TASK
    src_noun = "source material file" if synth else "findings file"
    focus = (ctx.track_focus.strip() + "\n\n") if ctx.track_focus and ctx.track_focus.strip() else ""
    role_line = (f"SECTION ROLE: You are writing {ctx.role.strip()}\n\n"
                 if ctx.role and ctx.role.strip() else "")

    # per-section structure + citation directives (used by overall's intro/conclusions/abstract)
    if ctx.use_subsections:
        struct = ("If the input file lists 'Planned subsections', follow that plan: use "
                  "each planned subsection as a \\subsection{...} heading, IN THAT ORDER "
                  "(you may add \\subsubsection{...} within them where helpful). If no plan "
                  "is given, begin with \\subsection{Overview} and organise with further "
                  "\\subsection{...} (and \\subsubsection{...} where helpful).")
    else:
        struct = ("Write it as flowing prose (a short paragraph or few) -- do NOT use "
                  "\\subsection or \\subsubsection, and do NOT begin with "
                  "\\subsection{Overview}.")
    if ctx.allow_cite:
        cite = "Use ONLY the allowed citation anchors listed in that file."
    else:
        cite = ("This section is CITATION-FREE: do NOT use \\parencite/\\textcite or any "
                "citation -- it summarises sections that already carry the references.")

    def _cap_line():
        # Communicate ONLY the hard limit -- no soft range. A soft "aim for ~lo-hi"
        # line makes the generator anchor on the range and overshoot; a single firm
        # ceiling is what it must respect.
        hard = ctx.word_hard
        if not hard:
            return ""
        return (f"\n\nHARD WORD LIMIT: the section MUST be under {hard} words. Stay well "
                "within it; do NOT pad, and never sacrifice faithfulness to the source to "
                "hit a number. Content beyond the limit will be sent back to you to cut.")

    def _condense_instruction(words):
        # Condense the ALREADY-GENERATED draft -- trim its own text. Do NOT re-read the
        # source material or rewrite the section from scratch (that would defeat the cut
        # and risk drifting from what was already reviewed).
        return (f"The draft section you must shorten is at:\n  {target_abs}\n\n"
                f"Read that draft and CONDENSE ITS OWN TEXT from {words} words down to "
                f"UNDER {ctx.word_hard} words. Work ONLY from the draft -- do NOT go back "
                "to the source material and do NOT rewrite the section from scratch. Cut "
                "the least-important detail, merge overlapping sentences, and tighten "
                "wording while keeping the draft's existing structure and meaning. "
                "PRESERVE every citation that supports a retained claim, and keep it "
                "compilable (balanced $, braces, environments). Edit the file IN PLACE; "
                "do NOT create a new file.")

    def _write_instruction(guidance):
        g = f"\nGuidance: {guidance}" if guidance.strip() else ""
        return (f"{focus}{role_line}Read the {src_noun} at:\n  {ctx.input_path}\n\n"
                f"Write the LaTeX SECTION BODY to (absolute path):\n  {target_abs}\n\n"
                f"{cite} {struct} Output the body ONLY (no preamble, no \\section "
                f"title, no \\printbibliography).{_cap_line()}{g}\n\n"
                "Create the file with the Write tool at the exact path above.")

    def _edit_instruction(guidance):
        return (f"{focus}{role_line}Update the EXISTING section file IN PLACE:\n  {target_abs}\n\n"
                f"Revise it with the Edit tool to address:\n{guidance}\n\n"
                f"Keep it grounded ONLY in the source at:\n  {ctx.input_path}\n"
                f"Body only. {struct} {cite} "
                f"Do NOT create a new file -- edit the existing one.{_cap_line()}")

    def _fix_instruction(errors, dbi):
        dbi_txt = ("\nSuspected unbalanced math:\n" + "\n".join(dbi)) if dbi else ""
        return (f"The section file does NOT compile:\n  {target_abs}\n\n"
                f"LaTeX/bibliography errors:\n{errors or '(no excerpt)'}{dbi_txt}\n\n"
                "Read the file, then Edit it IN PLACE to fix ONLY these errors (balanced "
                "$, braces, environments; valid commands; every cite key must be an "
                "allowed anchor). Do NOT change the substantive content. Keep it a body "
                "only and do NOT create a new file.")

    @tool("generate", "Create or update the single section file for this theme and "
                       "return a short status. Owns compilation.",
          {"instruction": str, "attempt": int})
    async def generate(args):
        attempt = int(args.get("attempt", 1) or 1)
        guidance = str(args.get("instruction", "") or "")

        first = (attempt <= 1) or (not target.exists())
        instr = _write_instruction(guidance) if first else _edit_instruction(guidance)
        tools = ["Read", "Write", "Edit"] if first else ["Read", "Edit"]
        await call_agent(instr, gen_system, allowed_tools=tools, cwd=ctx.out_dir,
                         timeout=config.GEN_TIMEOUT, sem=ctx.sem, model=ctx.gen_model,
                         max_turns=config.GEN_MAX_TURNS,
                         label=f"gen[a{attempt}]", token_log=ctx.out_dir / "tokens.jsonl")

        if not target.exists():
            state["attempts"][attempt] = {"compiled": False, "words": 0, "cited": []}
            if state["best"] is None:
                state["best"] = attempt
            return _text(f"attempt {attempt}: no output file was written. Retry.")

        # Compile-fix loop. The hard word cap is enforced via the PROMPT (works well in
        # practice), so the deterministic CONDENSE loop (gen-cut) is DISABLED below.
        # To re-enable it, restore the four lines marked "CONDENSE-DISABLED".
        body, errors, compiled, wc = "", "", False, 0
        max_iters = config.GEN_MAX_FIX
        # CONDENSE-DISABLED: max_iters = config.GEN_MAX_FIX + (config.GEN_MAX_CONDENSE if ctx.word_hard else 0)
        for it in range(max_iters + 1):
            body = _section_body(ctx)
            target.write_text(body, encoding="utf-8")   # normalized body only
            harness = ctx.build_dir / "harness.tex"
            harness.write_text(
                wrap_section_doc(ctx.theme, f"\\input{{{target_abs}}}", ctx.bib_path),
                encoding="utf-8")
            ok, errors, _log = compile_doc(harness, ctx.build_dir)  # log NOT persisted
            wc = len(body.split())
            over = False
            # CONDENSE-DISABLED: over = bool(ctx.word_hard) and wc > ctx.word_hard
            if ok and not over:
                compiled = True
                break
            if it == max_iters:
                compiled = ok          # out of passes; keep whether it compiled
                break
            if not ok:
                instr_fix = _fix_instruction(errors, dollar_balance_issues(body))
                lbl = f"gen-fix[a{attempt}.{it + 1}]"
            else:                      # compiles but over the hard cap -> condense
                # CONDENSE-DISABLED (unreachable while `over` is forced False):
                instr_fix = _condense_instruction(wc)
                lbl = f"gen-cut[a{attempt}.{it + 1}]"
            await call_agent(
                instr_fix, gen_system, allowed_tools=["Read", "Edit"], cwd=ctx.out_dir,
                timeout=config.GEN_TIMEOUT, sem=ctx.sem, model=ctx.gen_model,
                max_turns=config.GEN_MAX_TURNS,
                label=lbl, token_log=ctx.out_dir / "tokens.jsonl")

        valid, _ = cited_keys(body, ctx.allowed)
        state["attempts"][attempt] = {"compiled": compiled, "words": wc, "cited": valid}
        state["best"] = attempt
        if compiled:
            return _text(f"attempt {attempt}: compiled OK, {wc} words, "
                         f"{len(valid)} anchors cited.")
        # CONDENSE-DISABLED: over-cap best-effort branch (re-enable with the condense loop)
        # if compiled:
        #     return _text(f"attempt {attempt}: compiled OK but {wc} words still exceeds the "
        #                  f"hard cap {ctx.word_hard} after {config.GEN_MAX_CONDENSE} condense "
        #                  f"passes; accepted best effort.")
        return _text(f"attempt {attempt}: STILL FAILS to compile after "
                     f"{config.GEN_MAX_FIX} fixes:\n{errors[:600]}")

    async def _run_review():
        instr = (f"Read the ENTIRE LaTeX section at:\n  {target_abs}\n"
                 f"and the ENTIRE {src_noun} at:\n  {ctx.input_path}\n"
                 f"(the {src_noun} may be long -- read ALL of it before judging).\n\n"
                 f"{review_task}\nRespond with STRICT JSON only.")
        return await call_agent(
            instr, review_system, allowed_tools=["Read"], cwd=ctx.out_dir,
            timeout=config.REVIEW_TIMEOUT, sem=ctx.sem, model=ctx.review_model,
            max_turns=config.REVIEW_MAX_TURNS,
            label="review", token_log=ctx.out_dir / "tokens.jsonl")

    @tool("review", "Check the section's faithfulness to the sources and correct "
                    "reference matching. Returns a short status.",
          {"attempt": int})
    async def review(args):
        attempt = int(args.get("attempt", state.get("best") or 1) or 1)
        if not target.exists():
            return _text("review: no section file yet; generate first.")

        out = await _run_review()
        transient = ("[sub-agent timed out" in out) or ("[sub-agent error" in out)
        if transient:
            await asyncio.sleep(5)             # let a transient overload / crash clear
            out = await _run_review()          # one retry on timeout/transient error
            transient = ("[sub-agent timed out" in out) or ("[sub-agent error" in out)

        parsed = _extract_json(out)
        (ctx.out_dir / f"review_attempt_{attempt:02d}.json").write_text(
            json.dumps({"parsed": parsed, "raw": out}, indent=1), encoding="utf-8")

        entry = state["attempts"].setdefault(attempt, {})
        if transient and not parsed:
            # a timeout is NOT a content failure -- don't force a regenerate loop.
            entry.update({"review_passed": None, "unfaithful": [], "miscited": []})
            return _text("review: INCONCLUSIVE (reviewer timed out/errored twice). The "
                         "draft compiled; you may report it as pass.")
        if not parsed:
            entry.update({"review_passed": False, "unfaithful": [], "miscited": []})
            return _text("review: could not parse a verdict; treat as NOT passed and "
                         "regenerate.")
        unf = parsed.get("unfaithful", []) or []
        mis = parsed.get("miscited", []) or []
        passed = bool(parsed.get("passed")) and not unf and not mis
        entry.update({"review_passed": passed, "unfaithful": unf, "miscited": mis})
        if passed:
            return _text("review: OK -- faithful and correctly cited.")
        issues = [f"{u}" for u in unf] + [f"(miscited) {m}" for m in mis]
        lines = "\n".join(f"- {i}" for i in issues[:12]) or "- unspecified"
        return _text(f"review: ISSUES:\n{lines}")

    @tool("report", "Finalize: promote the current section to final.tex/final.pdf and end.",
          {"status": str, "accepted_attempt": int})
    async def report(args):
        status = str(args.get("status", "pass") or "pass")
        acc = int(args.get("accepted_attempt", state.get("best") or 1) or 1)
        result, ok = finalize(ctx, state, acc, status)
        return _text(f"reported '{status}', accepted attempt {result['accepted_attempt']}; "
                     f"final.pdf {'written' if ok else 'FAILED to compile'}.")

    server = create_sdk_mcp_server("synth", tools=[generate, review, report])
    return server, state
