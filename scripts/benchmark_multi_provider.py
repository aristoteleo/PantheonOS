#!/usr/bin/env python3
"""Multi-provider token optimization benchmark.

Compares all 5 stages of token optimization + cache behavior across
OpenAI, Gemini, DeepSeek, Qwen, Kimi, and Claude.

Usage:
    OPENAI_API_KEY=... GEMINI_API_KEY=... DEEPSEEK_API_KEY=... \
    DASHSCOPE_API_KEY=... MOONSHOT_API_KEY=... ANTHROPIC_API_KEY=... \
    python scripts/benchmark_multi_provider.py
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pantheon.internal.memory import Memory
from pantheon.utils.token_optimization import (
    SnipConfig,
    TimeBasedMicrocompactConfig,
    apply_tool_result_budget,
    build_llm_view,
    collapse_read_search_groups,
    microcompact_messages,
    snip_messages_to_budget,
    stabilize_tool_definitions,
    supports_explicit_cache_control,
    is_anthropic_model,
)

CHARS_PER_TOKEN = 4
TMP = Path("/tmp/pantheon-multi-bench")
TMP.mkdir(parents=True, exist_ok=True)

# ── Provider definitions ──

PROVIDERS = {
    "openai": {
        "model": "openai/gpt-4.1-mini",
        "env": "OPENAI_API_KEY",
        "cache_field": "prompt_tokens_details.cached_tokens",
        "cache_type": "auto-prefix",
    },
    "gemini": {
        "model": "gemini/gemini-2.5-flash",
        "env": "GEMINI_API_KEY",
        "cache_field": "implicit (no field)",
        "cache_type": "implicit-auto",
    },
    "deepseek": {
        "model": "deepseek/deepseek-chat",
        "env": "DEEPSEEK_API_KEY",
        "cache_field": "prompt_cache_hit_tokens",
        "cache_type": "auto-disk",
    },
    "qwen": {
        "model": "zai/qwen-turbo",
        "env": "DASHSCOPE_API_KEY",
        "cache_field": "prompt_tokens_details.cached_tokens",
        "cache_type": "explicit+implicit",
    },
    "kimi": {
        "model": "moonshot/kimi-k2.5",
        "env": "MOONSHOT_API_KEY",
        "cache_field": "auto-prefix",
        "cache_type": "auto-prefix",
        "base_url_override": "https://api.moonshot.cn/v1",
    },
    "claude": {
        "model": "anthropic/claude-3-haiku-20240307",
        "env": "ANTHROPIC_API_KEY",
        "cache_field": "cache_read_input_tokens",
        "cache_type": "explicit-markers",
    },
}


# ── Helpers ──

def est_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for b in c:
                total += len(str(b))
    return total // CHARS_PER_TOKEN


def _log_line() -> str:
    return "2026-04-01T10:00:00 INFO server.handler request_id=abc status=200 duration_ms=42 path=/api/v2/data\n"


def _tc(call_id, name):
    return {"id": call_id, "function": {"name": name, "arguments": "{}"}}


def _tool_msg(call_id, content, name="shell"):
    return {"role": "tool", "tool_call_id": call_id, "tool_name": name, "content": content}


def _asst(content, tool_calls=None, ts=None):
    m = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    if ts is not None:
        m["timestamp"] = ts
    return m


def build_conversation(rounds=10, kb=50):
    """Build a realistic coding session conversation."""
    msgs = [
        {"role": "system", "content": "You are a software engineering assistant."},
        {"role": "user", "content": "Investigate the production outage."},
    ]
    now = time.time()
    tools = ["shell", "read_file", "grep", "web_fetch", "bash"]
    line = _log_line()
    for i in range(rounds):
        cid = f"call_{i:04d}"
        name = tools[i % len(tools)]
        ts = now - 7200 + i * 10  # all 2 hours ago for microcompact
        msgs.append(_asst(f"Checking {name} #{i}.", tool_calls=[_tc(cid, name)], ts=ts))
        msgs.append(_tool_msg(cid, line * max(1, (kb * 1024) // len(line)), name=name))
    msgs.append(_asst("Root cause found: race condition."))
    msgs.append({"role": "user", "content": "Please fix it."})
    return msgs


def build_collapsible(rounds=10, kb=50):
    """Build conversation with silent assistant messages for contextCollapse."""
    msgs = [
        {"role": "system", "content": "You are a software engineering assistant."},
        {"role": "user", "content": "Investigate the bug."},
    ]
    now = time.time()
    tools = ["grep", "read_file", "glob", "shell", "web_fetch"]
    line = _log_line()
    for i in range(rounds):
        cid = f"call_{i:04d}"
        name = tools[i % len(tools)]
        msgs.append({"role": "assistant", "tool_calls": [_tc(cid, name)], "timestamp": now - 7200 + i * 10})
        msgs.append(_tool_msg(cid, line * max(1, (kb * 1024) // len(line)), name=name))
    msgs.append(_asst("Found root cause."))
    msgs.append({"role": "user", "content": "Fix it."})
    return msgs


def flatten_for_api(messages):
    """Convert tool messages to user messages for API token counting."""
    result = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if not isinstance(content, str) or not content:
            continue
        if role == "tool":
            result.append({"role": "user", "content": f"[Tool output]\n{content}"})
        elif role in ("system", "user", "assistant"):
            result.append({"role": role, "content": content})
        elif role == "compression":
            result.append({"role": "user", "content": content})
    return result


@dataclass
class Row:
    name: str
    before: int
    after: int

    @property
    def saved(self): return self.before - self.after
    @property
    def pct(self): return round(self.saved / max(self.before, 1) * 100, 1)


def print_table(title, rows):
    print(f"\n{'─' * 72}")
    print(f"  {title}")
    print(f"{'─' * 72}")
    print(f"  {'Optimization':<42} {'Before':>9} {'After':>9} {'Saved':>9} {'%':>7}")
    print(f"  {'─' * 42} {'─' * 9} {'─' * 9} {'─' * 9} {'─' * 7}")
    for r in rows:
        print(f"  {r.name:<42} {r.before:>9,} {r.after:>9,} {r.saved:>9,} {r.pct:>6.1f}%")


# ═══════════════════════════════════════════════════════════════════════
# Part 1: Local benchmarks (identical across all providers)
# ═══════════════════════════════════════════════════════════════════════

def run_local_benchmarks():
    print("=" * 72)
    print("  Part 1: Local Token Optimization (provider-independent)")
    print("=" * 72)

    scales = [
        ("Medium (10x50KB)", 10, 50),
        ("Large  (20x100KB)", 20, 100),
    ]

    for label, rounds, kb in scales:
        msgs = build_conversation(rounds, kb)
        collapse_msgs = build_collapsible(rounds, kb)
        raw = est_tokens(msgs)

        rows = []
        # Opt 1: Tool result budget
        mem = Memory("b1")
        out = apply_tool_result_budget(copy.deepcopy(msgs), memory=mem, base_dir=TMP / "o1")
        rows.append(Row("1. Tool Result Budget", raw, est_tokens(out)))

        # Opt 2: Microcompact
        out = microcompact_messages(copy.deepcopy(msgs), is_main_thread=True,
                                     config=TimeBasedMicrocompactConfig(enabled=True, gap_threshold_minutes=1, keep_recent=2))
        rows.append(Row("2. Micro-Compact", raw, est_tokens(out)))

        # Opt 3: Cache stability (local check)
        tools = [{"function": {"name": "z", "parameters": {"required": ["b", "a"]}}},
                 {"function": {"name": "a", "parameters": {"required": ["x"]}}}]
        s1 = stabilize_tool_definitions(copy.deepcopy(tools))
        s2 = stabilize_tool_definitions(copy.deepcopy(tools))
        stable = json.dumps(s1) == json.dumps(s2)
        rows.append(Row(f"3. Cache Stability (stable={stable})", raw, raw))

        # Opt 4a: Snip
        out, _ = snip_messages_to_budget(copy.deepcopy(msgs),
                                          config=SnipConfig(enabled=True, token_budget=20_000, keep_recent=4))
        rows.append(Row("4a. HISTORY_SNIP", raw, est_tokens(out)))

        # Opt 4b: Collapse
        out, _ = collapse_read_search_groups(copy.deepcopy(collapse_msgs), min_group_size=3)
        rows.append(Row("4b. contextCollapse", est_tokens(collapse_msgs), est_tokens(out)))

        # Opt 4: Full pipeline
        mem = Memory("b4")
        out = build_llm_view(copy.deepcopy(msgs), memory=mem, base_dir=TMP / "o4", is_main_thread=True)
        rows.append(Row("4. Full LLM View (all stages)", raw, est_tokens(out)))

        # Combined
        mem = Memory("ball")
        out = build_llm_view(copy.deepcopy(msgs), memory=mem, base_dir=TMP / "all", is_main_thread=True)
        out = microcompact_messages(out, is_main_thread=True,
                                     config=TimeBasedMicrocompactConfig(enabled=True, gap_threshold_minutes=1, keep_recent=2))
        rows.append(Row("** ALL COMBINED **", raw, est_tokens(out)))

        print_table(f"{label}  (raw ~ {raw:,} tokens)", rows)


# ═══════════════════════════════════════════════════════════════════════
# Part 2: Live API — Combined pipeline token reduction per provider
# ═══════════════════════════════════════════════════════════════════════

def api_call_openai_compat(model, api_key, base_url, messages, tools=None, tool_choice=None, max_tokens=1):
    """OpenAI-compatible API call, returns (prompt_tokens, cached_tokens, raw_usage)."""
    from openai import OpenAI
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)

    call_kwargs = {"model": model, "messages": messages, "max_completion_tokens": max_tokens}
    if tools:
        call_kwargs["tools"] = tools
        call_kwargs["tool_choice"] = tool_choice or "none"

    resp = client.chat.completions.create(**call_kwargs)
    u = resp.usage
    prompt = int(u.prompt_tokens or 0)

    # Extract cached tokens (provider-specific)
    cached = 0
    details = getattr(u, "prompt_tokens_details", None)
    if details:
        cached = int(getattr(details, "cached_tokens", 0) or 0)

    # DeepSeek uses different field names
    raw = u.model_dump() if hasattr(u, 'model_dump') else {}
    ds_hit = raw.get("prompt_cache_hit_tokens", 0)
    if ds_hit and not cached:
        cached = ds_hit

    return prompt, cached, raw


def api_call_anthropic(model, api_key, messages, max_tokens=1):
    """Anthropic API call, returns (input_tokens, cache_read_tokens, raw_usage)."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    # Convert messages
    system = None
    anthropic_msgs = []
    for m in messages:
        if m["role"] == "system":
            content = m["content"]
            if isinstance(content, str):
                system = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
            else:
                system = content
        elif m["role"] in ("user", "assistant"):
            anthropic_msgs.append({"role": m["role"], "content": m["content"]})

    kwargs = {"model": model, "messages": anthropic_msgs, "max_tokens": max_tokens}
    if system:
        kwargs["system"] = system

    resp = client.messages.create(**kwargs)
    u = resp.usage
    input_tokens = int(getattr(u, "input_tokens", 0))
    cache_read = int(getattr(u, "cache_read_input_tokens", 0) or 0)
    cache_create = int(getattr(u, "cache_creation_input_tokens", 0) or 0)

    return input_tokens, cache_read, {
        "input_tokens": input_tokens,
        "cache_read": cache_read,
        "cache_create": cache_create,
    }


def api_call_gemini(model, api_key, messages, max_tokens=1):
    """Gemini API call, returns (prompt_tokens, cached_tokens, raw_usage)."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    model_name = model.replace("gemini/", "")

    system_text = None
    contents = []
    for m in messages:
        if m["role"] == "system":
            system_text = m["content"] if isinstance(m["content"], str) else str(m["content"])
        elif m["role"] == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=m["content"])]))
        elif m["role"] == "assistant":
            contents.append(types.Content(role="model", parts=[types.Part(text=m["content"])]))

    config = types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        temperature=0,
    )
    if system_text:
        config.system_instruction = system_text

    resp = client.models.generate_content(model=model_name, contents=contents, config=config)

    meta = resp.usage_metadata
    prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
    cached = int(getattr(meta, "cached_content_token_count", 0) or 0)

    return prompt, cached, {"prompt": prompt, "cached": cached}


def get_provider_config(name):
    """Get model name, base_url, and api_key for a provider."""
    from pantheon.utils.provider_registry import load_catalog
    catalog = load_catalog()

    cfg = PROVIDERS[name]
    env = cfg["env"]
    api_key = os.environ.get(env, "")
    model = cfg["model"]
    bare_model = model.split("/", 1)[1] if "/" in model else model

    # Use override base_url if specified, else from catalog
    base_url = cfg.get("base_url_override")
    if not base_url:
        # Resolve provider key from catalog
        prov_key = model.split("/", 1)[0] if "/" in model else name
        prov = catalog["providers"].get(prov_key, {})
        base_url = prov.get("base_url")

    return bare_model, base_url, api_key


def run_live_token_reduction(available_providers):
    print(f"\n{'=' * 72}")
    print("  Part 2: Live API — Combined Pipeline Token Reduction")
    print(f"{'=' * 72}")

    msgs = build_conversation(10, 50)
    raw_flat = flatten_for_api(msgs)

    mem = Memory("live-opt")
    optimized = build_llm_view(copy.deepcopy(msgs), memory=mem, base_dir=TMP / "live", is_main_thread=True)
    optimized = microcompact_messages(optimized, is_main_thread=True,
                                       config=TimeBasedMicrocompactConfig(enabled=True, gap_threshold_minutes=1, keep_recent=2))
    opt_flat = flatten_for_api(optimized)

    print(f"\n  {'Provider':<12} {'Model':<28} {'Raw':>8} {'Optimized':>10} {'Saved':>8} {'%':>7}")
    print(f"  {'─' * 12} {'─' * 28} {'─' * 8} {'─' * 10} {'─' * 8} {'─' * 7}")

    for name in available_providers:
        model_full = PROVIDERS[name]["model"]
        try:
            if name == "claude":
                bare, _, api_key = get_provider_config(name)
                before, _, _ = api_call_anthropic(bare, api_key, raw_flat, max_tokens=1)
                after, _, _ = api_call_anthropic(bare, api_key, opt_flat, max_tokens=1)
            elif name == "gemini":
                bare, _, api_key = get_provider_config(name)
                before, _, _ = api_call_gemini(model_full, api_key, raw_flat, max_tokens=1)
                after, _, _ = api_call_gemini(model_full, api_key, opt_flat, max_tokens=1)
            else:
                bare, base_url, api_key = get_provider_config(name)
                before, _, _ = api_call_openai_compat(bare, api_key, base_url, raw_flat, max_tokens=1)
                after, _, _ = api_call_openai_compat(bare, api_key, base_url, opt_flat, max_tokens=1)

            saved = before - after
            pct = round(saved / max(before, 1) * 100, 1)
            print(f"  {name:<12} {model_full:<28} {before:>8,} {after:>10,} {saved:>8,} {pct:>6.1f}%")
        except Exception as e:
            print(f"  {name:<12} {model_full:<28} {'ERROR':>8} {str(e)[:30]}")


# ═══════════════════════════════════════════════════════════════════════
# Part 3: Live API — Cache hit comparison per provider
# ═══════════════════════════════════════════════════════════════════════

def run_cache_benchmark(available_providers):
    print(f"\n{'=' * 72}")
    print("  Part 3: Live API — Cache Hit Comparison")
    print(f"{'=' * 72}")
    print("  Method: send identical request twice, measure cached tokens on call 2")

    # Build a conversation long enough to trigger caching (>1024 tokens)
    long_system = ("You are a software engineering assistant. " * 30)
    messages = [
        {"role": "system", "content": long_system},
        {"role": "user", "content": "Investigate the payment service outage. " * 20},
        {"role": "assistant", "content": "I will start by checking the error logs. " * 15},
        {"role": "user", "content": "What did you find?"},
    ]

    tools_a = [
        {"type": "function", "function": {"name": "read_file", "description": "Read file", "parameters": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "shell", "description": "Run command", "parameters": {"type": "object", "required": ["cmd"], "properties": {"cmd": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "grep", "description": "Search files", "parameters": {"type": "object", "required": ["q"], "properties": {"q": {"type": "string"}}}}},
    ]
    tools_b = list(reversed(tools_a))  # shuffled order
    tools_stable = stabilize_tool_definitions(copy.deepcopy(tools_a))

    print(f"\n  {'Provider':<12} {'Unstable(c1)':>13} {'Unstable(c2)':>13} {'Stable(c1)':>12} {'Stable(c2)':>12} {'Cache Type':<16}")
    print(f"  {'─' * 12} {'─' * 13} {'─' * 13} {'─' * 12} {'─' * 12} {'─' * 16}")

    for name in available_providers:
        model_full = PROVIDERS[name]["model"]
        cache_type = PROVIDERS[name]["cache_type"]

        try:
            if name == "claude":
                # Anthropic: use explicit cache_control markers
                bare, _, api_key = get_provider_config(name)
                # Anthropic doesn't use tools in the same way, test with messages only
                _, uc1, _ = api_call_anthropic(bare, api_key, messages, max_tokens=8)
                _, uc2, _ = api_call_anthropic(bare, api_key, messages, max_tokens=8)
                time.sleep(1)
                _, sc1, _ = api_call_anthropic(bare, api_key, messages, max_tokens=8)
                _, sc2, _ = api_call_anthropic(bare, api_key, messages, max_tokens=8)
                print(f"  {name:<12} {uc1:>13,} {uc2:>13,} {sc1:>12,} {sc2:>12,} {cache_type:<16}")
            elif name == "gemini":
                bare, _, api_key = get_provider_config(name)
                _, gc1, _ = api_call_gemini(model_full, api_key, messages, max_tokens=8)
                _, gc2, _ = api_call_gemini(model_full, api_key, messages, max_tokens=8)
                time.sleep(1)
                _, gs1, _ = api_call_gemini(model_full, api_key, messages, max_tokens=8)
                _, gs2, _ = api_call_gemini(model_full, api_key, messages, max_tokens=8)
                print(f"  {name:<12} {gc1:>13,} {gc2:>13,} {gs1:>12,} {gs2:>12,} {cache_type:<16}")
            else:
                # OpenAI-compatible: test with tools
                bare, base_url, api_key = get_provider_config(name)

                # Unstable: different tool order each call
                _, uc1, _ = api_call_openai_compat(bare, api_key, base_url, messages, tools_a, max_tokens=8)
                _, uc2, _ = api_call_openai_compat(bare, api_key, base_url, messages, tools_b, max_tokens=8)
                time.sleep(1)
                # Stable: same order both calls
                _, sc1, _ = api_call_openai_compat(bare, api_key, base_url, messages, tools_stable, max_tokens=8)
                _, sc2, _ = api_call_openai_compat(bare, api_key, base_url, messages, tools_stable, max_tokens=8)
                print(f"  {name:<12} {uc1:>13,} {uc2:>13,} {sc1:>12,} {sc2:>12,} {cache_type:<16}")

        except Exception as e:
            print(f"  {name:<12} {'ERROR':>13} {str(e)[:50]}")

    print(f"\n  Note: Numbers show cached_tokens. Higher = better cache utilization.")
    print(f"  Unstable c1/c2 = different tool order. Stable c1/c2 = same order.")
    print(f"  OpenAI/DeepSeek: auto prefix cache. Anthropic/Qwen: explicit markers.")
    print(f"  Gemini 2.5+: implicit auto cache. Kimi: auto prefix cache.")


# ═══════════════════════════════════════════════════════════════════════
# Part 4: Cache control marker compatibility check
# ═══════════════════════════════════════════════════════════════════════

def run_cache_marker_check():
    print(f"\n{'=' * 72}")
    print("  Part 4: Cache Control Marker Compatibility")
    print(f"{'=' * 72}")

    models = [
        ("openai/gpt-4.1-mini", "auto-prefix (no markers needed)"),
        ("anthropic/claude-haiku-3.5", "explicit cache_control markers"),
        ("gemini/gemini-2.5-flash", "implicit auto (no markers needed)"),
        ("deepseek/deepseek-chat", "auto-disk (no markers needed)"),
        ("zai/qwen-turbo", "explicit cache_control markers"),
        ("moonshot/kimi-k2.5", "auto-prefix (no markers needed)"),
    ]

    print(f"\n  {'Model':<32} {'Explicit Markers?':>18} {'Strategy'}")
    print(f"  {'─' * 32} {'─' * 18} {'─' * 40}")
    for model, strategy in models:
        explicit = supports_explicit_cache_control(model)
        marker = "YES" if explicit else "no"
        print(f"  {model:<32} {marker:>18} {strategy}")


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 72)
    print("  PantheonOS Multi-Provider Token Optimization Benchmark")
    print("=" * 72)

    # Detect available providers
    available = []
    for name, cfg in PROVIDERS.items():
        if os.environ.get(cfg["env"]):
            available.append(name)
            print(f"  [+] {name:<12} {cfg['model']:<30} ({cfg['env']})")
        else:
            print(f"  [-] {name:<12} {cfg['model']:<30} ({cfg['env']} not set)")

    if not available:
        print("\nERROR: No API keys set. Set at least one provider's API key.")
        sys.exit(1)

    # Part 1: Local benchmarks
    run_local_benchmarks()

    # Part 2: Live token reduction
    run_live_token_reduction(available)

    # Part 3: Cache hit comparison
    run_cache_benchmark(available)

    # Part 4: Cache marker compatibility
    run_cache_marker_check()

    print(f"\n{'=' * 72}")
    print("  BENCHMARK COMPLETE")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
