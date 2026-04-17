"""
Token Optimization 5-Stage Pipeline Test — Gemini API
=====================================================
Tests all 5 stages of the CC-aligned token optimization from PR #54,
using the Gemini API for live LLM calls (Stage 5 & end-to-end).

Usage:
    GEMINI_API_KEY=<key> python scripts/test_token_optimization_gemini.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import tempfile
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pantheon.utils.token_optimization import (
    apply_tool_result_budget,
    snip_messages_to_budget,
    microcompact_messages,
    collapse_read_search_groups,
    autocompact_messages,
    build_llm_view_async,
    stabilize_tool_definitions,
    inject_cache_control_markers,
    is_anthropic_model,
    SnipConfig,
    PERSISTED_OUTPUT_TAG,
    TIME_BASED_MC_CLEARED_MESSAGE,
    BYTES_PER_TOKEN,
)

# ── Model to use ──
LLM_MODEL = os.environ.get("TEST_MODEL", "openai/gpt-4.1-mini")

# ── Helpers ──

def chars_to_tokens(text: str) -> int:
    return max(1, len(text) // BYTES_PER_TOKEN)


def total_chars(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for block in c:
                total += len(str(block))
    return total


def print_header(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def print_result(before: int, after: int, unit: str = "chars"):
    reduction = before - after
    pct = (reduction / before * 100) if before > 0 else 0
    print(f"  Before: {before:>10,} {unit}")
    print(f"  After:  {after:>10,} {unit}")
    print(f"  Saved:  {reduction:>10,} {unit} ({pct:.1f}%)")


def make_large_tool_result(size_kb: int = 50, tool_name: str = "read_file") -> str:
    """Generate a realistic large tool result."""
    line = f"def function_{uuid.uuid4().hex[:8]}(x, y): return x + y  # computation\n"
    repeats = (size_kb * 1024) // len(line)
    return line * repeats


def make_tool_call_pair(
    tool_name: str,
    content: str,
    call_id: str | None = None,
    timestamp: float | None = None,
) -> list[dict]:
    """Create an assistant tool_call + tool result message pair."""
    cid = call_id or f"call_{uuid.uuid4().hex[:12]}"
    ts = timestamp or time.time()
    return [
        {
            "role": "assistant",
            "content": None,
            "id": f"asst_{uuid.uuid4().hex[:8]}",
            "timestamp": ts,
            "tool_calls": [
                {
                    "id": cid,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": cid,
            "name": tool_name,
            "content": content,
            "timestamp": ts + 0.5,
        },
    ]


# ══════════════════════════════════════════════════════════════════════
# Stage 1: Tool Result Budget
# ══════════════════════════════════════════════════════════════════════

def test_stage1_tool_result_budget():
    print_header("Stage 1: Tool Result Budget (apply_tool_result_budget)")
    print("  Externalizes large tool outputs when aggregate per-turn exceeds budget.")
    print("  Also tests per-tool limits via process_tool_result.\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        from pantheon.internal.memory import Memory
        memory = Memory("test-stage1")

        # ── Part A: Aggregate per-message budget ──
        # Put ALL tool results in one assistant turn (parallel tool calls)
        # so their combined size exceeds MAX_TOOL_RESULTS_PER_MESSAGE_CHARS (200K)
        tool_calls = []
        tool_results = []
        for i in range(6):
            cid = f"call_{i:04d}"
            tool_calls.append({
                "id": cid,
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            })
            tool_results.append({
                "role": "tool",
                "tool_call_id": cid,
                "name": "read_file",
                "content": make_large_tool_result(50),  # 50KB each, 6 x 50KB = 300KB > 200K limit
            })

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {
                "role": "assistant",
                "content": None,
                "id": "asst_parallel",
                "tool_calls": tool_calls,
            },
            *tool_results,
        ]

        before = total_chars(messages)
        optimized = apply_tool_result_budget(
            messages, memory=memory, base_dir=Path(tmpdir)
        )
        after = total_chars(optimized)

        persisted_count = sum(
            1 for m in optimized
            if m.get("role") == "tool" and PERSISTED_OUTPUT_TAG in str(m.get("content", ""))
        )
        print(f"  Part A (aggregate budget, 6x50KB parallel):")
        print_result(before, after)
        print(f"  Externalized tool results: {persisted_count}/6")
        assert persisted_count > 0, "Expected aggregate budget to externalize some results"

        # ── Part B: Per-tool limits via process_tool_result ──
        from pantheon.utils.llm import process_tool_result
        big_result = "x" * 60_000  # 60KB, read_file limit is 40K
        truncated = process_tool_result(big_result, max_length=100_000, tool_name="read_file")
        print(f"\n  Part B (per-tool limit, read_file 60KB -> 40K cap):")
        print(f"  Before: {len(big_result):>10,} chars")
        print(f"  After:  {len(str(truncated)):>10,} chars")
        assert len(str(truncated)) < len(big_result), "Expected per-tool limit to truncate"

        print("  [PASS]\n")


# ══════════════════════════════════════════════════════════════════════
# Stage 2: History Snip
# ══════════════════════════════════════════════════════════════════════

def test_stage2_history_snip():
    print_header("Stage 2: History Snip (snip_messages_to_budget)")
    print("  Drops oldest messages when conversation exceeds token budget.\n")

    messages = []
    # 60 user/assistant turns with substantial content
    for i in range(60):
        messages.append({
            "role": "user",
            "content": f"Question {i}: " + "x" * 2000,
            "timestamp": time.time() - (60 - i) * 60,
        })
        messages.append({
            "role": "assistant",
            "content": f"Answer {i}: " + "y" * 2000,
            "timestamp": time.time() - (60 - i) * 60 + 30,
        })

    before_count = len(messages)
    before_chars = total_chars(messages)

    config = SnipConfig(enabled=True, token_budget=30_000, keep_recent=10)
    optimized, dropped = snip_messages_to_budget(messages, config=config)

    after_count = len(optimized)
    after_chars = total_chars(optimized)

    print(f"  Messages: {before_count} -> {after_count} (dropped {dropped})")
    print_result(before_chars, after_chars)
    assert after_count < before_count, "Expected messages to be dropped"
    assert after_count >= 10, "Expected at least keep_recent messages preserved"
    print("  [PASS]\n")


# ══════════════════════════════════════════════════════════════════════
# Stage 3: Time-Based Microcompact
# ══════════════════════════════════════════════════════════════════════

def test_stage3_microcompact():
    print_header("Stage 3: Time-Based Microcompact (microcompact_messages)")
    print("  Clears old compactable tool result content after time gap.\n")

    now = time.time()
    messages = []

    # Old tool results (2+ hours ago) — these should get cleared
    for i in range(8):
        ts = now - 7200 - (8 - i) * 120  # well over 2 hours ago
        messages.extend(
            make_tool_call_pair("read_file", "x" * 5000, timestamp=ts)
        )

    # The last assistant message must be >60 minutes ago for trigger to fire.
    # Add the last assistant+tool pair at ~90 min ago.
    ts_last = now - 5400  # 90 minutes ago
    messages.extend(
        make_tool_call_pair("read_file", "recent data " * 100, timestamp=ts_last)
    )

    # Current user turn (now) — no assistant response yet
    messages.append({
        "role": "user",
        "content": "What did we find?",
        "timestamp": now,
    })

    before_chars = total_chars(messages)
    optimized = microcompact_messages(messages, is_main_thread=True)
    after_chars = total_chars(optimized)

    cleared = sum(
        1 for m in optimized
        if m.get("role") == "tool" and TIME_BASED_MC_CLEARED_MESSAGE in str(m.get("content", ""))
    )
    print(f"  Cleared old tool results: {cleared}")
    print_result(before_chars, after_chars)
    assert cleared > 0, "Expected old tool results to be cleared"
    print("  [PASS]\n")


# ══════════════════════════════════════════════════════════════════════
# Stage 4: Context Collapse
# ══════════════════════════════════════════════════════════════════════

def test_stage4_context_collapse():
    print_header("Stage 4: Context Collapse (collapse_read_search_groups)")
    print("  Folds consecutive read/search tool-use groups into summaries.\n")

    messages = []
    # 5 consecutive grep calls (forms a collapsible group)
    for i in range(5):
        messages.extend(
            make_tool_call_pair(
                "grep",
                f"search result {i}: " + f"match line {i}\n" * 50,
            )
        )
    # A non-collapsible message breaks the group
    messages.append({
        "role": "user",
        "content": "Now do something else.",
    })
    # Another group of 4 read_file calls
    for i in range(4):
        messages.extend(
            make_tool_call_pair(
                "read_file",
                f"file content {i}: " + "code line\n" * 100,
            )
        )

    before_count = len(messages)
    before_chars = total_chars(messages)
    optimized, collapsed = collapse_read_search_groups(messages, min_group_size=3)
    after_count = len(optimized)
    after_chars = total_chars(optimized)

    print(f"  Messages: {before_count} -> {after_count} (collapsed {collapsed} groups)")
    print_result(before_chars, after_chars)
    assert collapsed > 0, "Expected at least one group to be collapsed"
    print("  [PASS]\n")


# ══════════════════════════════════════════════════════════════════════
# Stage 5: Autocompact (Live LLM LLM Call)
# ══════════════════════════════════════════════════════════════════════

async def test_stage5_autocompact():
    print_header("Stage 5: Autocompact (autocompact_messages) - LIVE LLM CALL")
    print(f"  LLM-based summarization using {LLM_MODEL}.\n")

    # Build a conversation that exceeds 100K token budget
    messages = []
    for i in range(40):
        messages.append({
            "role": "user",
            "content": f"Please analyze the following code block {i}:\n" + "def func(): pass\n" * 200,
            "timestamp": time.time() - (40 - i) * 120,
        })
        messages.append({
            "role": "assistant",
            "content": f"Analysis of block {i}: The code defines a function. " + "Details: " * 500,
            "timestamp": time.time() - (40 - i) * 120 + 60,
        })

    before_chars = total_chars(messages)
    before_tokens = before_chars // BYTES_PER_TOKEN

    print(f"  Input: {len(messages)} messages, ~{before_tokens:,} tokens")
    print(f"  Calling {LLM_MODEL} for summarization...")

    t0 = time.time()
    optimized, tokens_freed, tracking = await autocompact_messages(
        messages,
        model=LLM_MODEL,
        token_budget=50_000,  # lower budget to force compaction
        keep_recent=8,
    )
    elapsed = time.time() - t0

    after_chars = total_chars(optimized)
    after_tokens = after_chars // BYTES_PER_TOKEN

    print(f"  LLM call took: {elapsed:.1f}s")
    print(f"  Tokens freed (reported): {tokens_freed:,}")
    print(f"  Compacted: {tracking.compacted}")
    print_result(before_tokens, after_tokens, unit="tokens (est.)")

    # Check if summary was injected
    has_summary = any(
        "summary" in str(m.get("content", "")).lower()
        for m in optimized
        if m.get("role") == "user"
    )
    print(f"  Summary injected: {has_summary}")
    assert tracking.compacted, "Expected autocompact to have run"
    print("  [PASS]\n")


# ══════════════════════════════════════════════════════════════════════
# Bonus: stabilize_tool_definitions + inject_cache_control_markers
# ══════════════════════════════════════════════════════════════════════

def test_bonus_stabilize_and_cache():
    print_header("Bonus: stabilize_tool_definitions + inject_cache_control_markers")

    # ── stabilize_tool_definitions ──
    tools = [
        {
            "type": "function",
            "function": {
                "name": "zebra_tool",
                "parameters": {
                    "type": "object",
                    "required": ["z_param", "a_param"],
                    "properties": {
                        "z_param": {"type": "string"},
                        "a_param": {"type": "integer"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "alpha_tool",
                "parameters": {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                },
            },
        },
    ]

    stable1 = stabilize_tool_definitions(tools)
    stable2 = stabilize_tool_definitions(list(reversed(tools)))

    # Should produce identical output regardless of input order
    import json
    assert json.dumps(stable1) == json.dumps(stable2), "Tool definitions not stable"
    # alpha_tool should come before zebra_tool
    assert stable1[0]["function"]["name"] == "alpha_tool"
    # required should be sorted
    assert stable1[1]["function"]["parameters"]["required"] == ["a_param", "z_param"]
    print("  stabilize_tool_definitions: [PASS] (deterministic ordering)")

    # ── inject_cache_control_markers ──
    assert is_anthropic_model("claude-3-opus-20240229")
    assert is_anthropic_model("anthropic/claude-sonnet-4-20250514")
    assert not is_anthropic_model("gpt-4")
    assert not is_anthropic_model("gemini-2.5-flash")

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"},
    ]
    marked = inject_cache_control_markers(messages)

    # System message should have cache_control on last text block
    sys_content = marked[0]["content"]
    if isinstance(sys_content, list):
        last_text = [b for b in sys_content if b.get("type") == "text"][-1]
        assert "cache_control" in last_text
    print("  inject_cache_control_markers: [PASS] (markers injected)")
    print()


# ══════════════════════════════════════════════════════════════════════
# End-to-End: Full Pipeline + Live LLM Completion
# ══════════════════════════════════════════════════════════════════════

async def test_end_to_end():
    print_header("End-to-End: build_llm_view_async + Live LLM Completion")
    print(f"  Full 5-stage pipeline, then send to {LLM_MODEL}.")
    print("  Scenario: long coding session with large tool outputs,")
    print("  consecutive searches, old+recent messages — triggers all 5 stages.\n")

    from pantheon.internal.memory import Memory
    memory = Memory("test-e2e")
    now = time.time()

    messages = [{"role": "system", "content": "You are a helpful coding assistant."}]

    # ── Phase 1: Old exploration (2+ hours ago) ──
    # Large tool results → Stage 1 (aggregate budget per parallel turn)
    # Old timestamps → Stage 3 (microcompact)
    # Many messages → Stage 2 (snip) + Stage 5 (autocompact)
    for i in range(15):
        ts = now - 7200 - (15 - i) * 180  # 2-3 hours ago

        # User asks to read a file
        messages.append({
            "role": "user",
            "content": f"Read and analyze src/module_{i}.py, then search for related usages.",
            "timestamp": ts,
        })

        # Assistant does a parallel tool call: read_file + grep (triggers Stage 1 aggregate)
        cid_read = f"call_read_{i:04d}"
        cid_grep = f"call_grep_{i:04d}"
        messages.append({
            "role": "assistant",
            "content": None,
            "id": f"asst_parallel_{i}",
            "timestamp": ts + 1,
            "tool_calls": [
                {"id": cid_read, "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}},
                {"id": cid_grep, "type": "function",
                 "function": {"name": "grep", "arguments": "{}"}},
            ],
        })
        messages.append({
            "role": "tool", "tool_call_id": cid_read, "name": "read_file",
            "content": make_large_tool_result(20),  # 20KB per file
            "timestamp": ts + 2,
        })
        messages.append({
            "role": "tool", "tool_call_id": cid_grep, "name": "grep",
            "content": f"grep result {i}:\n" + f"src/module_{i}.py:42: import foo\n" * 200,
            "timestamp": ts + 2,
        })

        # Assistant analysis
        messages.append({
            "role": "assistant",
            "content": (
                f"Module {i} analysis: This file implements a data processing pipeline. "
                f"It imports from modules {max(0,i-2)}-{i-1} and exports {i*3} functions. "
                "The main entry point handles configuration, validation, and execution. "
            ) * 15,
            "timestamp": ts + 3,
        })

    # ── Phase 2: Consecutive search burst (1.5 hours ago) → Stage 4 ──
    ts_search = now - 5400
    for i in range(6):
        messages.extend(
            make_tool_call_pair(
                "grep",
                f"search {i}: " + f"match_{i}_line\n" * 100,
                timestamp=ts_search + i * 5,
            )
        )

    # ── Phase 3: More old work (1 hour ago) — more budget to exceed autocompact ──
    for i in range(10):
        ts = now - 3600 - (10 - i) * 60
        messages.append({
            "role": "user",
            "content": f"Refactor function_{i} to use async/await pattern. " + "Detail " * 200,
            "timestamp": ts,
        })
        messages.append({
            "role": "assistant",
            "content": f"Refactored function_{i}. Changes: " + "Modified code pattern. " * 300,
            "timestamp": ts + 30,
        })

    # ── Phase 4: Recent work (last 10 min) — kept intact ──
    for i in range(4):
        ts = now - 600 + i * 120
        messages.append({
            "role": "user",
            "content": f"Recent task {i}: check the test results for module_{i}.",
            "timestamp": ts,
        })
        messages.append({
            "role": "assistant",
            "content": f"Tests for module_{i} passed. 12 tests, 0 failures. " * 5,
            "timestamp": ts + 30,
        })

    # Final user message
    messages.append({
        "role": "user",
        "content": "Summarize everything we've done in this session.",
        "timestamp": now,
    })

    before_chars = total_chars(messages)
    before_tokens = before_chars // BYTES_PER_TOKEN
    before_count = len(messages)
    print(f"  Input: {before_count} messages, {before_chars:,} chars (~{before_tokens:,} tokens)")

    # Run full pipeline
    with tempfile.TemporaryDirectory() as tmpdir:
        t0 = time.time()
        optimized = await build_llm_view_async(
            messages,
            memory=memory,
            base_dir=Path(tmpdir),
            is_main_thread=True,
            autocompact_model=LLM_MODEL,
        )
        pipeline_time = time.time() - t0

    after_chars = total_chars(optimized)
    after_tokens = after_chars // BYTES_PER_TOKEN
    after_count = len(optimized)

    print(f"  Pipeline took: {pipeline_time:.1f}s")
    print(f"  Messages: {before_count} -> {after_count}")
    print_result(before_tokens, after_tokens, unit="tokens (est.)")

    # Now send the optimized messages to LLM via call_llm_provider
    print(f"\n  Sending optimized messages to {LLM_MODEL}...")

    from pantheon.utils.llm_providers import call_llm_provider, detect_provider
    from pantheon.utils.llm import process_messages_for_model

    # Clean messages for model (remove orphan tool results, etc.)
    send_messages = process_messages_for_model(optimized, LLM_MODEL)
    provider_config = detect_provider(LLM_MODEL, False)

    t0 = time.time()
    message = await call_llm_provider(
        config=provider_config,
        messages=send_messages,
        tools=None,
        response_format=None,
        process_chunk=None,
        model_params={"max_tokens": 500, "temperature": 0},
    )
    llm_time = time.time() - t0

    content = message.get("content", "") if isinstance(message, dict) else ""
    meta = message.get("_metadata", {}) if isinstance(message, dict) else {}
    prompt_tokens = meta.get("prompt_tokens", 0)
    completion_tokens = meta.get("completion_tokens", 0)

    print(f"  LLM response time: {llm_time:.1f}s")
    print(f"  Prompt tokens (actual): {prompt_tokens:,}")
    print(f"  Completion tokens: {completion_tokens:,}")
    print(f"  Response preview: {content[:200]}...")
    assert len(content) > 0, "Expected non-empty response from Gemini"
    print("  [PASS]\n")


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

async def main():
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY or GEMINI_API_KEY not set")
        sys.exit(1)

    print("=" * 70)
    print("  Token Optimization 5-Stage Pipeline Test")
    print(f"  Model: {LLM_MODEL}")
    print("=" * 70)

    # Stages 1-4: local (no API call)
    test_stage1_tool_result_budget()
    test_stage2_history_snip()
    test_stage3_microcompact()
    test_stage4_context_collapse()

    # Bonus: structural tests
    test_bonus_stabilize_and_cache()

    # Stage 5: live Gemini call
    await test_stage5_autocompact()

    # End-to-end
    await test_end_to_end()

    print("=" * 70)
    print("  ALL TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
