"""Per-unit orchestrator: ONE LLM agent per fine theme, driving the tools.

The orchestrator never writes or compiles LaTeX itself -- it only calls
generate / review / report. Every streamed message is appended to
orchestrator.jsonl live, and status.json is written at start and end, so a hang
loses nothing. If the orchestrator ends without calling report, we finalize the
best attempt deterministically as a fallback.
"""
from __future__ import annotations

import asyncio
import json
import shutil

from claude_agent_sdk import (
    query, ClaudeAgentOptions, AssistantMessage, TextBlock,
)

import config
from tools import UnitCtx, build_server, finalize
from texcompile import remove_stray_nul, force_rmtree
from sdk_agents import _extract_usage


ORCH_SYSTEM = (
    "You are the ORCHESTRATOR producing ONE compilable, source-faithful LaTeX "
    "section for a single unit of an academic review on agricultural subsurface "
    "(tile) drainage (the unit may be a fine theme, an integrated macro chapter, or "
    "one section of the overall paper). You NEVER write or compile LaTeX yourself -- "
    "you only call tools.\n"
    "Tools:\n"
    "- generate(instruction, attempt): writes a COMPILABLE draft to disk (it owns "
    "compilation) and returns a short status. 'instruction' is your short guidance "
    "on what to write or fix; 'attempt' is an integer you increment each call.\n"
    "- review(attempt): checks the draft's faithfulness to the sources AND correct "
    "reference matching; returns OK or a list of issues.\n"
    "- report(status, accepted_attempt): finalizes the chosen attempt and ends.\n\n"
    "The findings are at: {input_path}\n"
    "The theme is: {theme}\n\n"
    "Procedure:\n"
    "1. Call generate(instruction='write the section', attempt=1).\n"
    "2. If generate reports it STILL FAILS to compile, call generate again with the "
    "next attempt number and instruction on what to fix.\n"
    "3. Once a draft compiled, call review(attempt=<that attempt>).\n"
    "4. If review returns issues, call generate again (next attempt) with a focused "
    "instruction addressing the flagged claims/citations, then review again.\n"
    "5. When a draft BOTH compiled AND review is OK, call "
    "report(status='pass', accepted_attempt=<n>). If review is INCONCLUSIVE "
    "(e.g., the reviewer timed out), do NOT keep regenerating -- the draft already "
    "compiled, so call report(status='pass', accepted_attempt=<n>). If you reach "
    "attempt {max_attempts} without a clean pass, call report(status='max_attempts', "
    "accepted_attempt=<best n>).\n\n"
    "Judgement: accept minor faithfulness nits rather than looping forever; prefer "
    "targeted fixes for small issues. Keep instructions to generate concrete and "
    "short. Do nothing else."
)


def _ser(msg) -> dict:
    """Best-effort JSON record of one streamed SDK message."""
    rec = {"type": type(msg).__name__}
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        blocks = []
        for b in content:
            if isinstance(b, TextBlock):
                blocks.append({"t": "text", "text": b.text})
            else:
                d = {"t": type(b).__name__}
                for attr in ("name", "id", "input", "content", "tool_use_id", "is_error"):
                    v = getattr(b, attr, None)
                    if v is not None:
                        d[attr] = v if isinstance(v, (str, int, float, bool, list, dict)) else str(v)
                blocks.append(d)
        rec["blocks"] = blocks
    else:
        for attr in ("subtype", "result", "num_turns", "total_cost_usd", "is_error"):
            v = getattr(msg, attr, None)
            if v is not None:
                rec[attr] = v if isinstance(v, (str, int, float, bool)) else str(v)
        u = _extract_usage(msg)     # orchestrator's own token usage (ResultMessage)
        if u:
            rec["usage"] = u
    return rec


def _tokens_summary(out_dir) -> dict:
    """Aggregate the per-unit tokens.jsonl (one record per sub-agent call)."""
    tot = {"calls": 0, "in": 0, "out": 0, "cache_read": 0, "cost_usd": 0.0, "by_label": {}}
    tp = out_dir / "tokens.jsonl"
    if not tp.exists():
        return tot
    for line in tp.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        tot["calls"] += 1
        for k in ("in", "out", "cache_read"):
            tot[k] += r.get(k) or 0
        tot["cost_usd"] += r.get("cost_usd") or 0.0
        lbl = r.get("label", "?")
        b = tot["by_label"].setdefault(lbl, {"in": 0, "out": 0})
        b["in"] += r.get("in") or 0
        b["out"] += r.get("out") or 0
    return tot


def _entry(unit: dict, out_dir) -> dict:
    """Manifest entry, read back from disk so it works for fresh runs and cache hits."""
    st = {}
    sp = out_dir / "status.json"
    if sp.exists():
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            st = {}
    body = ""
    bp = out_dir / "body.tex"
    if bp.exists():
        body = bp.read_text(encoding="utf-8", errors="replace")
    return {
        "cluster": unit.get("cluster"),
        "id": unit["id"],
        "theme": unit["theme"],
        "description": unit.get("description", ""),
        "file": f"{unit['id']}/final.tex",
        "pdf": f"{unit['id']}/final.pdf",
        "macro_cluster": unit.get("macro_cluster"),
        "kind": unit.get("kind"),             # overall (c) section kind, if any
        "n_papers": unit.get("n_papers"),
        "body": body,
        "cited": st.get("cited", []),
        "status": st.get("status", "unknown"),
        "compiled": st.get("compiled"),
        "review_passed": st.get("review_passed"),
        "accepted_attempt": st.get("accepted_attempt"),
        "words": st.get("words"),
        "tokens": _tokens_summary(out_dir),
    }


async def run_unit(unit: dict, sem_units: asyncio.Semaphore, sem_sub: asyncio.Semaphore, *,
                   track: str, step: str, orch_model: str, gen_model: str,
                   review_model: str, fresh: bool, mode: str = "fine",
                   word_lo=None, word_hi=None, word_hard=None,
                   allow_cite: bool = True, use_subsections: bool = True,
                   role: str = "", paper: bool = False) -> dict:
    # a soft cap implies a hard ceiling the generator is forced under (soft x overflow)
    if word_hard is None and word_hi:
        word_hard = round(word_hi * config.HARD_OVERFLOW)
    uid = unit["id"]
    out_dir = config.AGENTIC / track / step / uid
    build_dir = out_dir / "_build"
    final_pdf = out_dir / "final.pdf"

    if final_pdf.exists() and not fresh:
        print(f"  (cache) {uid}: final.pdf exists -> skip")
        return _entry(unit, out_dir)

    if fresh and out_dir.exists():
        force_rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tokens.jsonl").unlink(missing_ok=True)   # fresh token log per run
    (out_dir / "status.json").write_text(
        json.dumps({"id": uid, "theme": unit["theme"], "state": "running"}, indent=1),
        encoding="utf-8")

    ctx = UnitCtx(
        uid=uid, theme=unit["theme"], input_path=unit["input_path"],
        out_dir=out_dir, build_dir=build_dir, allowed=set(unit["allowed_keys"]),
        bib_path=config.BIB.resolve().as_posix(), sem=sem_sub,
        gen_model=gen_model, review_model=review_model,
        mode=mode, word_lo=word_lo, word_hi=word_hi, word_hard=word_hard,
        track_focus=config.TRACK_FOCUS.get(track, ""),
        allow_cite=allow_cite, use_subsections=use_subsections, role=role, paper=paper)
    server, state = build_server(ctx)

    opts = ClaudeAgentOptions(
        system_prompt=ORCH_SYSTEM.format(
            theme=unit["theme"], input_path=unit["input_path"],
            max_attempts=config.MAX_ATTEMPTS),
        mcp_servers={"synth": server},
        allowed_tools=["mcp__synth__generate", "mcp__synth__review", "mcp__synth__report"],
        max_turns=config.ORCH_MAX_TURNS, model=orch_model,
        permission_mode="bypassPermissions", cwd=str(out_dir))
    kickoff = (f"Produce the review section for theme:\n  {unit['theme']}\n"
               f"Findings file: {unit['input_path']}\n"
               "Begin by calling generate(instruction='write the section', attempt=1).")

    print(f"  [run] {uid} ...")
    # markers that warrant a retry: API transients AND subprocess crashes (a nested
    # claude.exe exiting non-zero surfaces here as a ProcessError / exit-code string).
    transient = ("overloaded", "529", "429", "rate limit", "temporarily", "try again",
                 "processerror", "exit code 1", "exit code: 1", "check stderr",
                 "connection", "timeout")
    async with sem_units:
        with (out_dir / "orchestrator.jsonl").open("w", encoding="utf-8") as jf:
            for ratt in range(1, config.ORCH_RETRIES + 1):
                err = {"hit": False, "text": ""}

                async def _run():
                    agen = query(prompt=kickoff, options=opts)
                    try:
                        async for msg in agen:
                            jf.write(json.dumps(_ser(msg), ensure_ascii=False) + "\n")
                            jf.flush()
                            if getattr(msg, "is_error", False):
                                err["hit"] = True
                                err["text"] = str(getattr(msg, "result", "") or "")
                    finally:
                        try:
                            await agen.aclose()
                        except BaseException:   # aclose on an errored generator re-raises
                            pass

                try:
                    await asyncio.wait_for(_run(), timeout=config.ORCH_TIMEOUT)
                except asyncio.CancelledError:
                    raise                                   # honour real cancellation
                except asyncio.TimeoutError:
                    jf.write(json.dumps({"type": "error", "text": "orchestrator timed out"}) + "\n")
                    break
                except BaseException as e:  # noqa: BLE001 -- incl. anyio ExceptionGroup
                    # a nested sub-agent crash must NOT abort the batch: log, then let
                    # the transient check below decide retry-vs-stop.
                    jf.write(json.dumps({"type": "error",
                                         "text": f"orchestrator error: {type(e).__name__}: {e}"}) + "\n")
                    jf.flush()
                    err["hit"] = True
                    err["text"] = f"{type(e).__name__}: {e}"

                if not err["hit"]:
                    break                                   # clean finish
                if not any(m in err["text"].lower() for m in transient):
                    break                                   # non-transient; don't retry
                if ratt < config.ORCH_RETRIES:
                    jf.write(json.dumps({"type": "retry", "attempt": ratt,
                                         "reason": err["text"][:160]}) + "\n")
                    jf.flush()
                    print(f"  [retry] {uid}: transient error ({err['text'][:60]}), "
                          f"retry {ratt}/{config.ORCH_RETRIES - 1}")
                    await asyncio.sleep(config.ORCH_RETRY_BACKOFF * ratt)

    # fallback: orchestrator ended (or crashed) without calling report. Promote the
    # best compiled attempt if there is one, else record a failure. NEVER leave the
    # unit stuck in "running" and NEVER raise out of here.
    try:
        if state.get("final") is None:
            if state.get("attempts"):
                finalize(ctx, state, state.get("best"), "fallback")
            else:
                (out_dir / "status.json").write_text(
                    json.dumps({"id": uid, "theme": unit["theme"], "state": "failed",
                                "status": "no_attempts"}, indent=1), encoding="utf-8")
    except asyncio.CancelledError:
        raise
    except BaseException as e:  # noqa: BLE001
        try:
            (out_dir / "status.json").write_text(
                json.dumps({"id": uid, "theme": unit["theme"], "state": "failed",
                            "status": f"finalize_error: {type(e).__name__}: {e}"}, indent=1),
                encoding="utf-8")
        except Exception:
            pass

    try:
        remove_stray_nul(out_dir)   # tidy the Windows reserved-name artifact
    except Exception:
        pass
    entry = _entry(unit, out_dir)
    tk = entry["tokens"]
    print(f"  [done] {uid}: status={entry['status']} compiled={entry['compiled']} "
          f"review_passed={entry['review_passed']} | tokens: {tk['calls']} calls, "
          f"in={tk['in']} out={tk['out']} cache_read={tk['cache_read']} "
          f"cost=${tk['cost_usd']:.4f}")
    return entry
