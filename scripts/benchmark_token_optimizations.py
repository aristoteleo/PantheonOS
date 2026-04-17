#!/usr/bin/env python3
"""Benchmark all 5 token optimizations with real OpenAI API token counting.

Measures actual prompt_tokens reported by the API for each optimization
on/off, across multiple conversation sizes. Uses gpt-4.1-mini for minimal cost.

Usage:
    OPENAI_API_KEY=sk-... python scripts/benchmark_token_optimizations.py
    OPENAI_API_KEY=sk-... python scripts/benchmark_token_optimizations.py --skip-live
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pantheon.internal.memory import Memory
from pantheon.team.pantheon import DELEGATION_RECENT_TAIL_SIZE
from pantheon.utils.token_optimization import (
    ON_DEMAND_HINT,
    SnipConfig,
    TimeBasedMicrocompactConfig,
    apply_token_optimizations,
    apply_tool_result_budget,
    autocompact_messages,
    build_delegation_context_message,
    build_llm_view,
    collapse_read_search_groups,
    microcompact_messages,
    should_autocompact,
    snip_messages_to_budget,
    stabilize_tool_definitions,
)

CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def est_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
    return total // CHARS_PER_TOKEN


def _make_log_output(size_kb: int) -> str:
    line = "2026-03-31T06:41:47 INFO  server.handler  request_id=abc123 status=200 duration_ms=42 path=/api/v2/data user=admin@example.com\n"
    return line * max(1, (size_kb * 1024) // len(line))


def _tc(call_id: str, name: str) -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": "{}"}}


def _tool_msg(call_id: str, content: str, name: str = "shell") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "tool_name": name, "content": content}


def _asst(content: str, tool_calls=None, ts=None) -> dict:
    m: dict = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    if ts is not None:
        m["timestamp"] = ts
    return m


# ---------------------------------------------------------------------------
# Build test conversations
# ---------------------------------------------------------------------------

def build_conversation(
    num_rounds: int = 10,
    output_kb: int = 50,
    all_timestamps_old: bool = False,
) -> list[dict]:
    msgs: list[dict] = [
        {"role": "system", "content": "You are a software engineering assistant. Investigate issues methodically."},
        {"role": "user", "content": "Investigate the production outage in the payment service. Check logs, code, and recent deployments."},
    ]
    now = time.time()
    tools = ["shell", "read_file", "grep", "web_fetch", "bash"]

    for i in range(num_rounds):
        cid = f"call_{i:04d}"
        name = tools[i % len(tools)]

        if all_timestamps_old:
            ts = now - 7200 + i * 10  # all 2 hours ago
        else:
            ts = now - 7200 + i * 10 if i < num_rounds - 2 else now - 5

        msgs.append(_asst(f"Checking {name} for issue #{i}.", tool_calls=[_tc(cid, name)], ts=ts))
        msgs.append(_tool_msg(cid, _make_log_output(output_kb), name=name))

    msgs.append(_asst("Root cause: race condition in payment handler."))
    msgs.append({"role": "user", "content": "Please fix it."})
    return msgs


def build_collapsible_conversation(
    num_rounds: int = 10,
    output_kb: int = 50,
) -> list[dict]:
    """Build a conversation where tool calls have silent assistant messages
    (no text content), making them collapsible by contextCollapse."""
    msgs: list[dict] = [
        {"role": "system", "content": "You are a software engineering assistant."},
        {"role": "user", "content": "Investigate the bug."},
    ]
    now = time.time()
    tools = ["grep", "read_file", "glob", "shell", "web_fetch"]

    for i in range(num_rounds):
        cid = f"call_{i:04d}"
        name = tools[i % len(tools)]
        ts = now - 7200 + i * 10
        # Silent assistant: only tool_calls, NO text — collapsible
        msgs.append({
            "role": "assistant",
            "tool_calls": [_tc(cid, name)],
            "timestamp": ts,
        })
        msgs.append(_tool_msg(cid, _make_log_output(output_kb), name=name))

    msgs.append(_asst("I found the root cause."))
    msgs.append({"role": "user", "content": "Fix it."})
    return msgs


# ---------------------------------------------------------------------------
# Convert to OpenAI-compatible format for live API calls
# ---------------------------------------------------------------------------

def flatten_for_api(messages: list[dict]) -> list[dict]:
    """Convert tool messages to user messages for API token counting.

    OpenAI requires tool_calls + tool responses in matched pairs. To avoid
    that complexity, we flatten: embed tool results as user messages so the
    API counts all the tokens accurately.
    """
    result = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if not isinstance(content, str) or not content:
            continue
        if role == "tool":
            # Embed as user message to preserve token count
            result.append({"role": "user", "content": f"[Tool output ({m.get('tool_name','tool')})]\n{content}"})
        elif role in ("system", "user", "assistant"):
            clean = {"role": role, "content": content}
            result.append(clean)
        elif role == "compression":
            result.append({"role": "user", "content": content})
    return result


# ---------------------------------------------------------------------------
# Optimization benchmarks
# ---------------------------------------------------------------------------

@dataclass
class Result:
    name: str
    before_tokens: int
    after_tokens: int

    @property
    def saved(self) -> int:
        return self.before_tokens - self.after_tokens

    @property
    def pct(self) -> float:
        return round(self.saved / max(self.before_tokens, 1) * 100, 1)


def bench_opt1(msgs: list[dict], tmp: Path, budget: int = 30_000) -> Result:
    """Opt1: Tool result budget — externalize large outputs to disk."""
    before = est_tokens(msgs)
    mem = Memory("b-opt1")
    out = apply_tool_result_budget(copy.deepcopy(msgs), memory=mem, base_dir=tmp / "o1",
                                   per_message_limit=budget)
    return Result("1. Tool Result Budget", before, est_tokens(out))


def bench_opt2(msgs: list[dict]) -> Result:
    """Opt2: Micro-compact — clear old compactable tool results."""
    before = est_tokens(msgs)
    out = microcompact_messages(
        copy.deepcopy(msgs), is_main_thread=True,
        config=TimeBasedMicrocompactConfig(enabled=True, gap_threshold_minutes=1, keep_recent=2),
    )
    return Result("2. Micro-Compact", before, est_tokens(out))


def bench_opt3() -> Result:
    """Opt3: Cache stability — local idempotency check only.
    Real cache-hit measurement is done in bench_opt3_live().
    """
    tools = [
        {"function": {"name": "zeta", "parameters": {"type": "object", "required": ["b", "a"],
                                                      "properties": {"b": {"type": "str"}, "a": {"type": "str"}}}}},
        {"function": {"name": "alpha", "parameters": {"type": "object", "required": ["x"],
                                                       "properties": {"x": {"type": "str"}}}}},
        {"function": {"name": "mid", "parameters": {"type": "object", "required": ["q", "p"],
                                                     "properties": {"q": {"type": "str"}, "p": {"type": "str"}}}}},
    ]
    stable1 = stabilize_tool_definitions(copy.deepcopy(tools))
    stable2 = stabilize_tool_definitions(copy.deepcopy(tools))
    is_stable = json.dumps(stable1) == json.dumps(stable2)
    t = len(json.dumps(tools)) // CHARS_PER_TOKEN
    return Result(f"3. Cache Stability (idempotent={is_stable}, see live)", t, t)


def bench_opt4_snip(msgs: list[dict]) -> Result:
    """Opt4a: HISTORY_SNIP — token-budget truncation of oldest messages."""
    before = est_tokens(msgs)
    out, freed = snip_messages_to_budget(
        copy.deepcopy(msgs),
        config=SnipConfig(enabled=True, token_budget=20_000, keep_recent=4),
    )
    return Result("4a. HISTORY_SNIP", before, est_tokens(out))


def bench_opt4_collapse(msgs: list[dict], collapse_msgs: list[dict] | None = None) -> Result:
    """Opt4b: contextCollapse — fold consecutive read/search groups."""
    target = collapse_msgs or msgs
    before = est_tokens(target)
    out, saved = collapse_read_search_groups(copy.deepcopy(target), min_group_size=3)
    return Result("4b. contextCollapse", before, est_tokens(out))


def bench_opt4_autocompact(msgs: list[dict]) -> Result:
    """Opt4c: autocompact — last-resort LLM-based summarization (heuristic fallback)."""
    before = est_tokens(msgs)
    out, freed, _ = asyncio.run(autocompact_messages(
        copy.deepcopy(msgs), token_budget=20_000, keep_recent=4,
        model=None,  # heuristic fallback for benchmark (no API cost)
    ))
    return Result("4c. Autocompact (heuristic)", before, est_tokens(out))


def bench_opt4(msgs: list[dict], tmp: Path) -> Result:
    """Opt4: build_llm_view — full 5-stage projection pipeline."""
    before = est_tokens(msgs)
    mem = Memory("b-opt4")
    out = build_llm_view(copy.deepcopy(msgs), memory=mem, base_dir=tmp / "o4", is_main_thread=True)
    return Result("4. LLM View Layer (all stages)", before, est_tokens(out))


def bench_opt5(msgs: list[dict]) -> Result:
    """Opt5: Delegation summary-first vs full history.

    Compares what a sub-agent actually receives:
      BEFORE (old default use_summary=False): raw instruction + child gets the
              parent's full history via memory (simulated by est_tokens on full msgs)
      AFTER  (new default use_summary=True):  summary + recent tail context message
    """
    # BEFORE: old behavior (use_summary=False) — sub-agent sees full parent history
    # as its task_message was just the instruction, but `run_context.memory.get_messages`
    # would feed the parent conversation. We estimate what the child *actually* processes.
    system_tokens = 20  # system prompt overhead
    before = est_tokens(msgs) + system_tokens  # child processes full parent history

    # AFTER: new behavior — child only sees compact delegation context
    tail = msgs[-DELEGATION_RECENT_TAIL_SIZE:]
    ctx_after = build_delegation_context_message(
        history=tail, instruction="Fix the race condition.",
        summary_text="Parent investigated payment outage. Root cause: race condition in concurrent transaction handler. Logs and code examined.",
    )
    after = len(ctx_after) // CHARS_PER_TOKEN + system_tokens

    return Result("5. Delegation Summary-First", before, after)


def bench_combined(msgs: list[dict], tmp: Path) -> Result:
    """All optimizations stacked (opt1 + opt2 + opt4)."""
    before = est_tokens(msgs)
    mem = Memory("b-all")
    out = build_llm_view(copy.deepcopy(msgs), memory=mem, base_dir=tmp / "all", is_main_thread=True)
    out = microcompact_messages(
        out, is_main_thread=True,
        config=TimeBasedMicrocompactConfig(enabled=True, gap_threshold_minutes=1, keep_recent=2),
    )
    return Result("** ALL COMBINED **", before, est_tokens(out))


# ---------------------------------------------------------------------------
# Live API
# ---------------------------------------------------------------------------

def api_prompt_tokens(client, model: str, messages: list[dict]) -> int:
    flat = flatten_for_api(messages)
    if not flat:
        return 0
    resp = client.chat.completions.create(model=model, messages=flat, max_completion_tokens=1)
    return int(resp.usage.prompt_tokens or 0)


def _make_cache_tools(order: list[str]) -> list[dict]:
    """Build tool definitions in the given name order (to simulate unstable ordering)."""
    all_tools = {
        "read_file":   {"type": "function", "function": {"name": "read_file",   "description": "Read a file from disk",       "parameters": {"type": "object", "required": ["path"],  "properties": {"path":  {"type": "string"}}}}},
        "shell":       {"type": "function", "function": {"name": "shell",       "description": "Run a shell command",          "parameters": {"type": "object", "required": ["cmd"],   "properties": {"cmd":   {"type": "string"}}}}},
        "grep":        {"type": "function", "function": {"name": "grep",        "description": "Search file contents",         "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}},
        "web_fetch":   {"type": "function", "function": {"name": "web_fetch",   "description": "Fetch a URL",                  "parameters": {"type": "object", "required": ["url"],   "properties": {"url":   {"type": "string"}}}}},
        "write_file":  {"type": "function", "function": {"name": "write_file",  "description": "Write content to a file",      "parameters": {"type": "object", "required": ["path", "content"], "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}},
    }
    return [all_tools[n] for n in order]


def bench_opt3_live(client, model: str) -> dict:
    """Live cache-hit test for Opt3 (Cache Stability).

    OpenAI caches the prefix (system + tools + messages) when it is ≥1024 tokens
    and the request is identical. We send the same long conversation twice:
      - UNSTABLE: tools in different random orders each call → prefix mismatch → no cache
      - STABLE:   tools always in sorted order via stabilize_tool_definitions → cache hit

    We measure cached_tokens in the second request of each pair.
    """
    # Build a long enough prefix (need >1024 tokens cacheable)
    long_system = (
        "You are a software engineering assistant. "
        "Your job is to investigate complex production incidents. "
        "Always be methodical: check logs first, then code, then deployments. "
    ) * 30  # ~750 chars × 4 ≈ well over 1024 tokens with tools added

    messages = [
        {"role": "system", "content": long_system},
        {"role": "user", "content": "Investigate the payment service outage. " * 20},
        {"role": "assistant", "content": "I will start by checking the error logs. " * 15},
        {"role": "user", "content": "What did you find in the logs?"},
    ]

    order_a = ["read_file", "shell", "grep", "web_fetch", "write_file"]
    order_b = ["shell", "write_file", "read_file", "grep", "web_fetch"]  # different order

    stable_tools = stabilize_tool_definitions(_make_cache_tools(order_a))

    def call(tools_list, label):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools_list,
            tool_choice="none",   # don't invoke any tool, just count prefix tokens
            max_completion_tokens=8,
        )
        u = resp.usage
        prompt = int(u.prompt_tokens or 0)
        details = getattr(u, "prompt_tokens_details", None)
        cached = int(getattr(details, "cached_tokens", 0) or 0)
        hit_rate = round(cached / max(prompt, 1) * 100, 1)
        print(f"    {label}: prompt={prompt:,}  cached={cached:,}  hit_rate={hit_rate}%", flush=True)
        return {"prompt_tokens": prompt, "cached_tokens": cached, "hit_rate_pct": hit_rate}

    print("  [live opt3] UNSTABLE order — call 1 (warm-up)...", flush=True)
    unstable_call1 = call(_make_cache_tools(order_a), "unstable call1")
    print("  [live opt3] UNSTABLE order — call 2 (different order, expect 0 cache)...", flush=True)
    unstable_call2 = call(_make_cache_tools(order_b), "unstable call2")

    # Small delay to let cache warm
    time.sleep(1)

    print("  [live opt3] STABLE order — call 1 (warm-up)...", flush=True)
    stable_call1 = call(stable_tools, "stable   call1")
    print("  [live opt3] STABLE order — call 2 (same order, expect cache hit)...", flush=True)
    stable_call2 = call(stable_tools, "stable   call2")

    return {
        "unstable": {"call1": unstable_call1, "call2": unstable_call2},
        "stable":   {"call1": stable_call1,   "call2": stable_call2},
        "cache_gain_pct": round(
            stable_call2["hit_rate_pct"] - unstable_call2["hit_rate_pct"], 1
        ),
        "uncached_tokens_saved": (
            unstable_call2["prompt_tokens"] - unstable_call2["cached_tokens"]
        ) - (
            stable_call2["prompt_tokens"] - stable_call2["cached_tokens"]
        ),
    }


def run_live(model: str, scenarios: list[dict], tmp: Path) -> list[dict]:
    from openai import OpenAI
    client = OpenAI()
    results = []
    for sc in scenarios:
        label = sc["label"]
        msgs = sc["messages"]
        print(f"  [live] {label} — measuring raw...", flush=True)
        before = api_prompt_tokens(client, model, msgs)

        print(f"  [live] {label} — measuring optimized...", flush=True)
        mem = Memory(f"live-{label}")
        opt = build_llm_view(copy.deepcopy(msgs), memory=mem, base_dir=tmp / f"l-{label}", is_main_thread=True)
        opt = microcompact_messages(
            opt, is_main_thread=True,
            config=TimeBasedMicrocompactConfig(enabled=True, gap_threshold_minutes=1, keep_recent=2),
        )
        after = api_prompt_tokens(client, model, opt)
        saved = before - after
        pct = round(saved / max(before, 1) * 100, 1)
        results.append({"scenario": label, "before": before, "after": after, "saved": saved, "pct": pct})
    return results


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_table(title: str, rows: list[Result]):
    print(f"\n{'─' * 70}")
    print(f"  {title}")
    print(f"{'─' * 70}")
    print(f"  {'Optimization':<45} {'Before':>8} {'After':>8} {'Saved':>8} {'%':>7}")
    print(f"  {'─' * 45} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 7}")
    for r in rows:
        print(f"  {r.name:<45} {r.before_tokens:>8,} {r.after_tokens:>8,} {r.saved:>8,} {r.pct:>6.1f}%")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--skip-live", action="store_true")
    args = parser.parse_args()
    tmp = Path("/tmp/pantheon-token-bench")
    tmp.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  PantheonOS Token Optimization Benchmark")
    print("=" * 70)

    scales = [
        {"label": "Small  (5×10KB)",   "rounds": 5,  "kb": 10},
        {"label": "Medium (10×50KB)",   "rounds": 10, "kb": 50},
        {"label": "Large  (20×100KB)",  "rounds": 20, "kb": 100},
        {"label": "XL     (30×200KB)",  "rounds": 30, "kb": 200},
    ]

    all_json: list[dict] = []

    for scale in scales:
        # Build with ALL timestamps old so microcompact triggers
        msgs = build_conversation(num_rounds=scale["rounds"], output_kb=scale["kb"],
                                  all_timestamps_old=True)
        raw = est_tokens(msgs)

        # Build collapsible version (silent assistant messages) for contextCollapse
        collapse_msgs = build_collapsible_conversation(
            num_rounds=scale["rounds"], output_kb=scale["kb"],
        )
        rows = [
            bench_opt1(msgs, tmp),
            bench_opt2(msgs),
            bench_opt3(),
            bench_opt4_snip(msgs),
            bench_opt4_collapse(msgs, collapse_msgs),
            bench_opt4_autocompact(msgs),
            bench_opt4(msgs, tmp),
            bench_opt5(msgs),
            bench_combined(msgs, tmp),
        ]

        print_table(f"{scale['label']}   (raw ≈ {raw:,} tokens)", rows)
        all_json.append({
            "scale": scale["label"], "raw_tokens": raw,
            "opts": [{"name": r.name, "before": r.before_tokens, "after": r.after_tokens,
                       "saved": r.saved, "pct": r.pct} for r in rows],
        })

    # Live API
    if not args.skip_live and os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI
        client = OpenAI()

        # --- Opt 1/2/4/5 live token reduction ---
        print(f"\n{'=' * 70}")
        print("  Live API: Opt 1+2+4 combined token reduction (gpt-4.1-mini)")
        print(f"{'=' * 70}")
        live_scenarios = [
            {"label": "5×30KB",  "messages": build_conversation(5,  30, all_timestamps_old=True)},
            {"label": "10×50KB", "messages": build_conversation(10, 50, all_timestamps_old=True)},
            {"label": "15×80KB", "messages": build_conversation(15, 80, all_timestamps_old=True)},
        ]
        live = run_live(args.model, live_scenarios, tmp)
        print(f"\n  {'Scenario':<20} {'Before':>10} {'After':>10} {'Saved':>10} {'%':>7}")
        print(f"  {'─' * 20} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 7}")
        for lr in live:
            print(f"  {lr['scenario']:<20} {lr['before']:>10,} {lr['after']:>10,} {lr['saved']:>10,} {lr['pct']:>6.1f}%")

        # --- Opt 3 live cache-hit test ---
        print(f"\n{'=' * 70}")
        print("  Live API: Opt 3 — Cache Stability (cached_tokens comparison)")
        print(f"{'=' * 70}")
        cache_result = bench_opt3_live(client, args.model)
        print(f"\n  Unstable order → call2 cache hit rate: {cache_result['unstable']['call2']['hit_rate_pct']}%")
        print(f"  Stable   order → call2 cache hit rate: {cache_result['stable']['call2']['hit_rate_pct']}%")
        print(f"  Cache gain from stable ordering:       +{cache_result['cache_gain_pct']}%")
        print(f"  Uncached tokens saved (per request):   {cache_result['uncached_tokens_saved']:,}")

        all_json.append({"live_token_reduction": live, "live_cache_stability": cache_result})
    elif args.skip_live:
        print("\n[Skipped live API benchmark (--skip-live)]")
    else:
        print("\n[Skipped live API benchmark (OPENAI_API_KEY not set)]")

    # Write JSON
    out_path = tmp / "results.json"
    out_path.write_text(json.dumps(all_json, indent=2, ensure_ascii=False))
    print(f"\nJSON results saved to: {out_path}")


if __name__ == "__main__":
    main()
