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
        _mark("llm")


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


_EVAL: Dict[str, float] = {"calls": 0, "calls_low": 0, "calls_harness": 0,
                           "case_executions": 0, "eval_seconds": 0.0}


def add_eval(fidelity: str, metrics: Dict[str, Any], source: str = "search") -> None:
    """Book one evaluator invocation, whoever asked for it.

    The loop's recorded measurements, a method's cheap screens and an agent's inner
    `run_evaluator` probes all pass through `evaluate_files`, so booking there counts every
    verifier run a method causes -- the other half of a fair budget besides LLM spend.
    `case_executions` (cases x repeats, from the task evaluator's own report) is the comparable
    unit; tasks that do not report it still get call counts.
    """
    cases = int(metrics.get("num_cases", 0) or 0) * int(metrics.get("n_eval_runs", 1) or 1)
    with _LOCK:
        _EVAL["calls"] += 1
        if source != "search":
            # warm-up reads and drift probes are the harness measuring ITSELF; kept out of
            # the search's budget arithmetic and out of the budget-axis curves
            _EVAL["calls_harness"] += 1
        if fidelity and fidelity != "full":
            _EVAL["calls_low"] += 1
        _EVAL["case_executions"] += cases
        _EVAL["eval_seconds"] += float(metrics.get("eval_time", 0.0) or 0.0)
        _mark("eval")


def eval_snapshot() -> Dict[str, float]:
    with _LOCK:
        return dict(_EVAL)


_TIMELINE: list = []


def _mark(kind: str) -> None:
    """One timeline row per booking: when it happened and where both ledgers stood.

    Exists for budget-axis evolution curves (score vs cumulative LLM calls / evaluator calls).
    Run totals alone cannot place a measurement on those axes; wave5 had to reconstruct them by
    time-share, and this is the fix. Appended under the same lock as the ledgers, so a row is
    always a consistent read of both.
    """
    import time as _time

    _TIMELINE.append({"t": _time.time(), "kind": kind,
                      "llm_calls": _USAGE["calls"],
                      "prompt_tokens": _USAGE["prompt_tokens"],
                      "completion_tokens": _USAGE["completion_tokens"],
                      "eval_calls": _EVAL["calls"], "eval_calls_low": _EVAL["calls_low"],
                      "eval_calls_harness": _EVAL["calls_harness"],
                      "case_executions": _EVAL["case_executions"]})


def timeline() -> list:
    with _LOCK:
        return list(_TIMELINE)


def restore(llm: Dict[str, Any] | None, ev: Dict[str, Any] | None,
            tl: list | None) -> None:
    """Reload a previous session's ledgers, so a resumed run CONTINUES its budget.

    Without this, a resume restarts every ceiling from zero and a "480-call run" done in two
    sessions costs 960 calls. Called once at startup, before any booking."""
    with _LOCK:
        for k in _USAGE:
            _USAGE[k] = (llm or {}).get(k, 0) or 0
        for k in _EVAL:
            _EVAL[k] = (ev or {}).get(k, 0) or 0
        _TIMELINE.clear()
        _TIMELINE.extend(tl or [])
