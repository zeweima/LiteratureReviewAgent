"""Run step -- orchestrate all units of a FLAT stage (fine or macro).

Reads the reusable inputs produced by `prep_inputs.py --step <step>`, runs one
orchestrator per unit (concurrency-limited), and writes `<step>/_<step>_manifest.json`
which the next step consumes. Cache = a unit's final.pdf already exists (skip unless
--fresh). Overall (c) is staged and dependency-ordered -- use `run_overall.py`.

    python prep_inputs.py --step fine  --track frontier
    python run_stage.py    --step fine  --track frontier
    python prep_inputs.py --step macro --track frontier
    python run_stage.py    --step macro --track frontier
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import config
from orchestrator import run_unit
from sdk_agents import install_quiet_exception_handler


def _fail_entry(u, e):
    """Minimal manifest entry for a unit whose run_unit itself raised (defensive:
    run_unit is designed never to raise, but the batch must survive if it does)."""
    return {"cluster": u.get("cluster"), "id": u["id"], "theme": u.get("theme", u["id"]),
            "description": u.get("description", ""), "status": f"error: {type(e).__name__}",
            "compiled": None, "review_passed": None, "body": "", "cited": []}


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()

    def flush(self):
        for st in self.streams:
            st.flush()


def _setup_logging(log_fp: Path):
    log_fp.parent.mkdir(parents=True, exist_ok=True)
    f = log_fp.open("w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, f)
    sys.stderr = _Tee(sys.__stderr__, f)
    return log_fp


async def _amain(args):
    step_meta = config.STEPS[args.step]
    if not step_meta["flat"]:
        sys.exit(f"step {args.step!r} is staged; use run_overall.py")
    mode = step_meta["mode"]
    cap_hi = step_meta.get("cap_hi")

    units_fp = config.AGENTIC / args.track / "inputs" / args.step / "_units.json"
    if not units_fp.exists():
        sys.exit(f"missing {units_fp}\nRun:  python prep_inputs.py --step {args.step} "
                 f"--track {args.track}")
    idx = json.loads(units_fp.read_text(encoding="utf-8"))
    units = idx["units"]
    if args.only:
        sel = args.only

        def _match(u):
            # exact cluster id or exact unit id
            if sel == str(u.get("cluster")) or u["id"] == sel:
                return True
            # a numeric selector matches only the zero-padded id prefix ("0" -> "00_"),
            # NOT any id that merely contains the digit (all ids start with a number).
            if sel.isdigit():
                return u["id"].startswith(f"{int(sel):02d}_")
            # a non-numeric selector is a name substring ("drainage-modeling")
            return sel in u["id"]

        units = [u for u in units if _match(u)]
        if not units:
            sys.exit(f"--only {sel!r} matched no unit")
    if args.limit:
        units = units[:args.limit]

    print(f"[run_stage] track={args.track} step={args.step} mode={mode} "
          f"units={len(units)} concurrency={args.concurrency} fresh={args.fresh}")
    print(f"[models] orch={args.orch_model} gen={args.gen_model} review={args.review_model}")

    install_quiet_exception_handler()
    sem_units = asyncio.Semaphore(args.concurrency)
    sem_sub = asyncio.Semaphore(config.SUBAGENT_CONCURRENCY)
    # fine + macro are synthesis stages (no paper-section role); overall (c) uses run_overall
    tasks = [run_unit(u, sem_units, sem_sub, track=args.track, step=args.step,
                      orch_model=args.orch_model, gen_model=args.gen_model,
                      review_model=args.review_model, fresh=args.fresh, mode=mode,
                      word_lo=None, word_hi=u.get("word_hi", cap_hi))
             for u in units]
    # return_exceptions: one unit's failure must never abort the batch or drop the
    # manifest. run_unit is bulletproof, but this is the outer safety net.
    results = await asyncio.gather(*tasks, return_exceptions=True)
    entries = []
    for u, r in zip(units, results):
        if isinstance(r, BaseException):
            print(f"  [error] {u['id']}: {type(r).__name__}: {r}")
            entries.append(_fail_entry(u, r))
        else:
            entries.append(r)

    man = {"track": args.track, "step": args.step, "name": idx.get("name"),
           "units": {str(e.get("cluster") if e.get("cluster") is not None else e["id"]): e
                     for e in entries}}
    out_fp = config.AGENTIC / args.track / args.step / f"_{args.step}_manifest.json"
    out_fp.parent.mkdir(parents=True, exist_ok=True)
    out_fp.write_text(json.dumps(man, indent=1), encoding="utf-8")

    n = len(entries)
    compiled = sum(1 for e in entries if e.get("compiled"))
    passed = sum(1 for e in entries if e.get("review_passed"))
    t_in = sum((e.get("tokens") or {}).get("in", 0) for e in entries)
    t_out = sum((e.get("tokens") or {}).get("out", 0) for e in entries)
    t_cost = sum((e.get("tokens") or {}).get("cost_usd", 0.0) for e in entries)
    print(f"\n[summary] {n} units | compiled {compiled}/{n} | review-passed {passed}/{n}")
    print(f"[tokens] total: in={t_in} out={t_out} cost=${t_cost:.4f}")
    print(f"[manifest] {out_fp}")
    for e in entries:
        tk = e.get("tokens") or {}
        print(f"  {e['id']}: status={e['status']} compiled={e['compiled']} "
              f"review={e['review_passed']} words={len((e['body'] or '').split())} "
              f"tokens(in/out)={tk.get('in', 0)}/{tk.get('out', 0)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--track", required=True, choices=sorted(config.TRACKS))
    ap.add_argument("--step", default="fine", choices=[s for s, m in config.STEPS.items() if m["flat"]])
    ap.add_argument("--fresh", action="store_true", help="ignore cache; rebuild every unit")
    ap.add_argument("--limit", type=int, default=0, help="TEST ONLY: cap number of units")
    ap.add_argument("--only", default="", help="TEST ONLY: select one unit by cluster id or id-substring")
    ap.add_argument("--concurrency", type=int, default=config.CONCURRENCY)
    ap.add_argument("--orch-model", default=config.ORCH_MODEL)
    ap.add_argument("--gen-model", default=config.GEN_MODEL)
    ap.add_argument("--review-model", default=config.REVIEW_MODEL)
    args = ap.parse_args()

    _setup_logging(config.AGENTIC / args.track / args.step / "_run.log")
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
