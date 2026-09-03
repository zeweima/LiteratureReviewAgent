"""Nesting-safe Claude Agent SDK sub-agent wrappers (fresh, minimal).

One shared coroutine drives every sub-agent (generator writes files, reviewer
reads them). Each `query()` is wrapped in `asyncio.wait_for` and its async
generator is `aclose()`d in a `finally` so the underlying `claude.exe` is reaped
-- the fix for the orphaned-subprocess hangs the old pipeline fought.

Verified safe to call from inside an orchestrator tool handler: the SDK
dispatches inbound tool calls on a *separate* asyncio task, so a nested
`query()` here runs on the same loop without deadlock.
"""
from __future__ import annotations

import asyncio
import json

from claude_agent_sdk import (
    query, ClaudeAgentOptions, AssistantMessage, TextBlock,
)

_USAGE_KEYS = ("input_tokens", "output_tokens",
               "cache_creation_input_tokens", "cache_read_input_tokens")


def _extract_usage(msg):
    """Normalized token usage from a message that carries it (the ResultMessage).

    Returns {in, out, cache_read, cache_creation, cost_usd} or None.
    """
    u = getattr(msg, "usage", None)
    if u is None:
        return None
    d = u if isinstance(u, dict) else {k: getattr(u, k, None) for k in _USAGE_KEYS}
    return {"in": d.get("input_tokens"), "out": d.get("output_tokens"),
            "cache_read": d.get("cache_read_input_tokens"),
            "cache_creation": d.get("cache_creation_input_tokens"),
            "cost_usd": getattr(msg, "total_cost_usd", None)}


def install_quiet_exception_handler():
    """Swallow benign SDK subprocess-cleanup noise on the running loop.

    When a nested `claude.exe` exits non-zero, the SDK's generator `aclose()`
    schedules an `async_generator_athrow` task whose ProcessError is never
    retrieved -> asyncio logs a scary 'Task exception was never retrieved'. The
    real error is already surfaced via `call_agent`'s return text and handled, so
    we drop only that specific noise and defer everything else to the default.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()

    def _handler(loop, context):
        msg = str(context.get("message", ""))
        exc = context.get("exception")
        text = f"{type(exc).__name__}: {exc}" if exc else ""
        noise = ("never retrieved" in msg or "async_generator_athrow" in msg
                 or "ProcessError" in text or "ProcessError" in msg
                 or "unhandled exception during" in msg)
        if noise:
            return
        loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


async def call_agent(instruction: str, system: str, *, allowed_tools, cwd,
                     timeout: float, sem: asyncio.Semaphore, model: str,
                     max_turns: int, label: str = "sub-agent", token_log=None) -> str:
    """Run one sub-agent to completion; return its concatenated assistant text.

    The real work (writing/reading files) is a side effect on disk; the returned
    text is only for logging/parsing. Never raises: timeouts and errors are
    folded into the returned string so a stuck sub-agent cannot wedge the run.

    Token usage (from the final ResultMessage) is printed as a compact `[tokens]`
    line and, if `token_log` (a path) is given, appended there as one JSON record.
    """
    texts: list[str] = []
    usage = {"val": None}
    opts = ClaudeAgentOptions(
        system_prompt=system,
        allowed_tools=list(allowed_tools),
        max_turns=max_turns,
        model=model,
        permission_mode="bypassPermissions",
        cwd=str(cwd),
    )

    async def _run():
        agen = query(prompt=instruction, options=opts)
        try:
            async for msg in agen:
                if isinstance(msg, AssistantMessage):
                    for b in msg.content:
                        if isinstance(b, TextBlock):
                            texts.append(b.text)
                u = _extract_usage(msg)              # ResultMessage carries cumulative usage
                if u:
                    usage["val"] = u
        finally:
            try:
                await agen.aclose()
            except BaseException:   # aclose on an errored generator re-raises
                pass

    async with sem:
        try:
            await asyncio.wait_for(_run(), timeout=timeout)
        except asyncio.CancelledError:
            raise                                    # honour real cancellation
        except asyncio.TimeoutError:
            texts.append(f"\n[sub-agent timed out after {timeout}s]")
        except BaseException as e:  # noqa: BLE001 -- incl. anyio ExceptionGroup / ProcessError
            # A nested claude.exe exiting non-zero surfaces here (often wrapped in an
            # ExceptionGroup, which is NOT an Exception). Fold it into the return text so
            # a sub-agent crash can never propagate up and poison the parent orchestrator.
            texts.append(f"\n[sub-agent error: {type(e).__name__}: {e}]")

    u = usage["val"]
    if u:
        print(f"    [tokens] {label} ({model}): in={u['in']} out={u['out']} "
              f"cache_read={u['cache_read']} cost=${u['cost_usd']}")
    else:
        print(f"    [tokens] {label} ({model}): (none -- timed out or crashed)")
    if token_log is not None:
        try:
            with open(str(token_log), "a", encoding="utf-8") as f:
                f.write(json.dumps({"label": label, "model": model,
                                    **(u or {"in": None, "out": None, "cache_read": None,
                                             "cache_creation": None, "cost_usd": None})}) + "\n")
        except Exception:
            pass
    return "".join(texts)
