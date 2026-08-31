"""One process-wide LLM usage ledger for an evolution run.

Exists because the three operator families spend the model completely differently -- SimpleTES
is one fat completion per candidate, the agent variators are multi-call tool sessions, the idea
variators one small call -- and a method comparison that matches only the ITEM budget says
nothing about LLM spend. Every call site adds what it saw; `run_bench` dumps the snapshot into
`summary.json` so the imbalance is a number instead of a suspicion.

A module-level accumulator is enough: each bench arm is its own process. Cost is best-effort
(the OpenAI-protocol response carries no price; agent-path cost comes from the adapter's
registry lookup) -- tokens and call counts are the trustworthy columns.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
_USAGE: Dict[str, float] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                            "cost_usd": 0.0}


def add(calls: int = 0, prompt_tokens: int = 0, completion_tokens: int = 0,
        cost_usd: float = 0.0) -> None:
    with _LOCK:
        _USAGE["calls"] += calls
        _USAGE["prompt_tokens"] += prompt_tokens
        _USAGE["completion_tokens"] += completion_tokens
        _USAGE["cost_usd"] += cost_usd


def add_response(resp: Any) -> None:
    """Book one OpenAI-protocol response (the completion/idea paths)."""
    u = getattr(resp, "usage", None)
    add(calls=1,
        prompt_tokens=int(getattr(u, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(u, "completion_tokens", 0) or 0))


def add_agent_messages(messages: Optional[list]) -> float:
    """Book one agent session from its message history; returns the summed cost.

    Each assistant message carries ITS call's `_metadata.current_cost` and `total_tokens`
    (prompt+completion of that call) -- session totals are sums over messages, not the last
    message's values.
    """
    calls, toks, cost = 0, 0, 0.0
    for msg in messages or []:
        if msg.get("role") != "assistant":
            continue
        meta = msg.get("_metadata") or {}
        calls += 1
        toks += int(meta.get("total_tokens", 0) or 0)
        cost += float(meta.get("current_cost", 0.0) or 0.0)
    # per-call total_tokens is context+output; without the split, book it as prompt-side
    add(calls=calls, prompt_tokens=toks, cost_usd=cost)
    return cost


def snapshot() -> Dict[str, float]:
    with _LOCK:
        return dict(_USAGE)
