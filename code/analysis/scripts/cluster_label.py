"""
Label theme clusters with the Claude Code SDK (Tier-4 step 2).

For each cluster, sends its characteristic keywords + representative finding
statements to Claude (via claude_agent_sdk, which drives the authenticated
``claude`` CLI -- no API key needed) and gets back a concise THEME NAME and a
short grounded DESCRIPTION.

Grounding discipline (same as the extraction step): the model is told to base the
theme ONLY on the supplied statements and to return strict JSON, so labels are
auditable and reproducible. Results are cached to disk keyed by the prompt hash,
so re-runs are free and deterministic.

Scales fine: #LLM calls = #clusters (tens), not #papers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)

SYSTEM = (
    "You are a scientific literature analyst specializing in agricultural "
    "subsurface (tile) drainage. You assign a concise research THEME to a cluster "
    "of extracted finding statements. Base the theme ONLY on the supplied "
    "statements -- do not invent scope. Respond with strict JSON only."
)


def _prompt(top_terms: str, bullets: list[str]) -> str:
    joined = "\n".join(f"- {b}" for b in bullets)
    return (
        "An algorithm grouped the following finding statements (from the "
        "tile-drainage literature) into ONE cluster.\n\n"
        f"Characteristic keywords: {top_terms}\n\n"
        f"Statements:\n{joined}\n\n"
        "Return JSON exactly: "
        '{"theme": "<specific 3-7 word theme name>", '
        '"description": "<1-2 sentences on what this theme covers, grounded only '
        'in the statements above>"}'
    )


def _parse(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"theme": "", "description": ""}
    try:
        obj = json.loads(m.group(0))
        return {"theme": str(obj.get("theme", "")).strip(),
                "description": str(obj.get("description", "")).strip()}
    except json.JSONDecodeError:
        return {"theme": "", "description": ""}


async def _ask(prompt: str, model: str | None, system: str = SYSTEM) -> str:
    """One-shot text query to Claude via the SDK (no tools, single turn)."""
    opts = ClaudeAgentOptions(
        system_prompt=system,
        allowed_tools=[],            # pure text task; no tool use
        max_turns=1,
        model=model,
        permission_mode="bypassPermissions",
    )
    out = []
    async for msg in query(prompt=prompt, options=opts):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, TextBlock):
                    out.append(b.text)
    return "".join(out)


MACRO_SYSTEM = (
    "You are a scientific literature analyst specializing in agricultural "
    "subsurface (tile) drainage. Several fine-grained research sub-themes have "
    "been grouped into ONE broad umbrella theme. Name the umbrella theme so it "
    "clearly spans its sub-themes without being generic. Respond with strict JSON only."
)


def _macro_prompt(subthemes: list[str]) -> str:
    joined = "\n".join(f"- {s}" for s in subthemes)
    return (
        "The following fine-grained sub-themes (each: name -- keywords, #papers) "
        "were grouped into ONE broad umbrella theme of the tile-drainage "
        f"literature.\n\nSub-themes:\n{joined}\n\n"
        "Return JSON exactly: "
        '{"theme": "<specific 2-6 word umbrella theme name>", '
        '"description": "<1-2 sentences on what this umbrella theme spans, grounded '
        'only in the sub-themes above>"}'
    )


async def label_macros(macros: list[dict], model: str | None,
                       cache_dir: Path) -> list[dict]:
    """macros: list of {macro_cluster, subthemes:[...]} -> adds theme/description."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for mprompt_src in macros:
        prompt = _macro_prompt(mprompt_src["subthemes"])
        key = hashlib.sha1(("macro" + str(model) + prompt).encode("utf-8")).hexdigest()[:16]
        cache_fp = cache_dir / f"{key}.json"
        if cache_fp.exists():
            parsed = json.loads(cache_fp.read_text(encoding="utf-8"))
            src = "cache"
        else:
            parsed = _parse(await _ask(prompt, model, MACRO_SYSTEM))
            cache_fp.write_text(json.dumps(parsed), encoding="utf-8")
            src = "llm"
        print(f"  MACRO {mprompt_src['macro_cluster']:>2} ({src}) {parsed['theme']}")
        results.append({**mprompt_src, **parsed})
    return results


def run_macro(macros: list[dict], model: str | None, cache_dir: Path) -> list[dict]:
    return asyncio.run(label_macros(macros, model, cache_dir))


async def label_clusters(clusters: list[dict], model: str | None,
                         cache_dir: Path) -> list[dict]:
    """clusters: list of {cluster, top_terms, bullets:[...]} -> adds theme/description."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for c in clusters:
        prompt = _prompt(c["top_terms"], c["bullets"])
        key = hashlib.sha1((str(model) + prompt).encode("utf-8")).hexdigest()[:16]
        cache_fp = cache_dir / f"{key}.json"
        if cache_fp.exists():
            parsed = json.loads(cache_fp.read_text(encoding="utf-8"))
            src = "cache"
        else:
            raw = await _ask(prompt, model)
            parsed = _parse(raw)
            cache_fp.write_text(json.dumps(parsed), encoding="utf-8")
            src = "llm"
        print(f"  [{c['cluster']:>2}] ({src}) {parsed['theme']}")
        results.append({**c, **parsed})
    return results


def run(clusters: list[dict], model: str | None, cache_dir: Path) -> list[dict]:
    return asyncio.run(label_clusters(clusters, model, cache_dir))
