from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
from dataclasses import asdict, dataclass
from typing import Any

from pantheon.agent import Agent, AgentRunContext
from pantheon.internal.memory import Memory
from pantheon.team.pantheon import (
    _get_cache_safe_child_fork_context_messages,
    _get_cache_safe_child_run_overrides,
)
from pantheon.utils.llm import count_tokens_in_messages, process_messages_for_model
from pantheon.utils.token_optimization import (
    build_cache_safe_runtime_params,
    build_delegation_context_message,
    build_llm_view,
)


@dataclass
class LiveUsageMetrics:
    prompt_tokens: int
    cached_tokens: int
    uncached_prompt_tokens: int
    cache_hit_rate: float


def _build_agent(name: str, instructions: str, model: str) -> Agent:
    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        model_params={"temperature": 0.7},
    )

    def alpha_tool(path: str) -> str:
        return path

    def beta_tool(query: str) -> str:
        return query

    agent.tool(beta_tool)
    agent.tool(alpha_tool)
    return agent


async def build_benchmark_state(model: str, prefix_repeat: int) -> dict[str, Any]:
    instructions = "You are a software engineering agent."
    caller = _build_agent("caller", instructions, model)
    target = _build_agent("target", instructions, model)

    history = [
        {"role": "system", "content": instructions},
        {
            "role": "user",
            "content": "Production outage investigation context. " * prefix_repeat,
        },
        {"role": "assistant", "content": "I will inspect logs and code."},
        {
            "role": "tool",
            "tool_call_id": "tool-1",
            "tool_name": "shell",
            "content": "ERROR " * 5000,
        },
        {
            "role": "assistant",
            "content": "The failure involves the cache layer and delegation path.",
        },
    ]

    parent_messages = build_llm_view(
        history,
        memory=Memory("prompt-cache-benchmark-parent"),
        is_main_thread=True,
    )
    parent_tools = await caller.get_tools_for_llm()
    parent_runtime = build_cache_safe_runtime_params(
        model=model,
        model_params={"temperature": 0, "top_p": 1},
        response_format=None,
    )
    parent_processed = process_messages_for_model(
        copy.deepcopy(parent_messages),
        model,
    )
    run_context = AgentRunContext(
        agent=caller,
        memory=None,
        execution_context_id=None,
        process_step_message=None,
        process_chunk=None,
        cache_safe_runtime_params=parent_runtime,
        cache_safe_prompt_messages=parent_processed,
        cache_safe_tool_definitions=parent_tools,
    )

    task_message = build_delegation_context_message(
        history=history,
        instruction="Analyze the cache issue and propose a fix.",
        summary_text=(
            "Parent found a likely cache-layer bug and wants a focused "
            "root-cause analysis."
        ),
    )

    before_child_messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": task_message},
    ]
    before_runtime = build_cache_safe_runtime_params(
        model=target.models[0],
        model_params=target.model_params,
        response_format=target.response_format,
    )
    before_processed = process_messages_for_model(
        copy.deepcopy(before_child_messages),
        model,
    )

    child_run_overrides, child_context_variables = _get_cache_safe_child_run_overrides(
        run_context,
        target,
        {},
    )
    fork_context_messages = await _get_cache_safe_child_fork_context_messages(
        run_context,
        target,
    )

    after_child_messages = [
        {"role": "system", "content": instructions},
        *(fork_context_messages or []),
        {"role": "user", "content": task_message},
    ]
    after_runtime = build_cache_safe_runtime_params(
        model=child_run_overrides.get("model", target.models[0]),
        model_params=child_context_variables.get(
            "model_params", target.model_params
        ),
        response_format=child_run_overrides.get(
            "response_format", target.response_format
        ),
    )
    after_processed = process_messages_for_model(
        copy.deepcopy(after_child_messages),
        model,
    )

    return {
        "instructions": instructions,
        "parent_messages": parent_processed,
        "before_child_messages": before_processed,
        "after_child_messages": after_processed,
        "parent_tools": parent_tools,
        "target_tools": await target.get_tools_for_llm(),
        "parent_runtime": parent_runtime,
        "before_runtime": before_runtime,
        "after_runtime": after_runtime,
        "child_context_variables": child_context_variables,
        "fork_context_messages": fork_context_messages or [],
        "compatibility": {
            "same_instructions": target.instructions == caller.instructions,
            "same_model_chain": list(target.models) == list(caller.models),
            "same_tools": parent_tools == await target.get_tools_for_llm(),
            "same_response_format": target.response_format == caller.response_format,
        },
    }


def build_structural_metrics(state: dict[str, Any], model: str) -> dict[str, Any]:
    parent_messages = state["parent_messages"]
    before_child_messages = state["before_child_messages"]
    after_child_messages = state["after_child_messages"]
    target_tools = state["target_tools"]

    before_prefix_hit = (
        before_child_messages[: len(parent_messages)] == parent_messages
    )
    after_prefix_hit = after_child_messages[: len(parent_messages)] == parent_messages

    before_tokens = count_tokens_in_messages(
        before_child_messages,
        model,
        target_tools,
    )["total"]
    after_tokens = count_tokens_in_messages(
        after_child_messages,
        model,
        target_tools,
    )["total"]

    return {
        "cache_prefix_hit_before": before_prefix_hit,
        "cache_prefix_hit_after": after_prefix_hit,
        "child_prompt_tokens_before": before_tokens,
        "child_prompt_tokens_after": after_tokens,
        "child_prompt_token_delta": after_tokens - before_tokens,
        "fork_context_message_count": len(state["fork_context_messages"]),
    }


def run_live_request(
    client: Any,
    *,
    model: str,
    messages: list[dict],
    max_completion_tokens: int,
) -> LiveUsageMetrics:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_completion_tokens=max_completion_tokens,
    )
    usage = resp.usage
    prompt_tokens = int(usage.prompt_tokens or 0)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    cached_tokens = int(getattr(prompt_details, "cached_tokens", 0) or 0)
    uncached_prompt_tokens = prompt_tokens - cached_tokens
    cache_hit_rate = round(
        cached_tokens / prompt_tokens * 100,
        2,
    ) if prompt_tokens else 0.0
    return LiveUsageMetrics(
        prompt_tokens=prompt_tokens,
        cached_tokens=cached_tokens,
        uncached_prompt_tokens=uncached_prompt_tokens,
        cache_hit_rate=cache_hit_rate,
    )


def sanitize_messages_for_live_chat(messages: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role not in {"system", "user", "assistant"}:
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        sanitized.append({"role": role, "content": content})
    return sanitized


def build_live_metrics(
    *,
    model: str,
    parent_messages: list[dict],
    before_child_messages: list[dict],
    after_child_messages: list[dict],
    max_completion_tokens: int,
) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI()
    parent_messages = sanitize_messages_for_live_chat(parent_messages)
    before_child_messages = sanitize_messages_for_live_chat(before_child_messages)
    after_child_messages = sanitize_messages_for_live_chat(after_child_messages)

    parent_before = run_live_request(
        client,
        model=model,
        messages=parent_messages,
        max_completion_tokens=max_completion_tokens,
    )
    child_before = run_live_request(
        client,
        model=model,
        messages=before_child_messages,
        max_completion_tokens=max_completion_tokens,
    )
    parent_after = run_live_request(
        client,
        model=model,
        messages=parent_messages,
        max_completion_tokens=max_completion_tokens,
    )
    child_after = run_live_request(
        client,
        model=model,
        messages=after_child_messages,
        max_completion_tokens=max_completion_tokens,
    )

    return {
        "parent_before": asdict(parent_before),
        "child_before": asdict(child_before),
        "parent_after": asdict(parent_after),
        "child_after": asdict(child_after),
        "cache_hit_rate_before_pct": child_before.cache_hit_rate,
        "cache_hit_rate_after_pct": child_after.cache_hit_rate,
        "uncached_prompt_tokens_before": child_before.uncached_prompt_tokens,
        "uncached_prompt_tokens_after": child_after.uncached_prompt_tokens,
        "uncached_prompt_token_delta": (
            child_after.uncached_prompt_tokens
            - child_before.uncached_prompt_tokens
        ),
        "warm_parent_vs_cached_child_uncached_delta": (
            parent_before.uncached_prompt_tokens
            - child_after.uncached_prompt_tokens
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark delegation prompt-cache behavior before/after "
            "cache-safe prefix sharing."
        )
    )
    parser.add_argument(
        "--model",
        default="gpt-4.1-mini",
        help="OpenAI model for live benchmark. Default: gpt-4.1-mini",
    )
    parser.add_argument(
        "--prefix-repeat",
        type=int,
        default=260,
        help="How many times to repeat the parent context sentence.",
    )
    parser.add_argument(
        "--max-completion-tokens",
        type=int,
        default=16,
        help="max_completion_tokens for each live request.",
    )
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Only compute structural metrics; skip live API requests.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = asyncio.run(
        build_benchmark_state(
            model=args.model,
            prefix_repeat=args.prefix_repeat,
        )
    )

    result: dict[str, Any] = {
        "model": args.model,
        "compatibility": state["compatibility"],
        "structural": build_structural_metrics(state, args.model),
        "notes": {
            "compatible_agent_rule": (
                "Prefix sharing only activates when caller/target are local Agent "
                "instances and the cache-critical surface matches: instructions, "
                "model chain, tools, and response format. Runtime model/model_params "
                "are then inherited from the parent request."
            ),
        },
    }

    api_key_present = bool(os.environ.get("OPENAI_API_KEY"))
    if args.skip_live:
        result["live"] = {"skipped": True, "reason": "--skip-live specified"}
    elif not api_key_present:
        result["live"] = {
            "skipped": True,
            "reason": "OPENAI_API_KEY not set",
        }
    else:
        result["live"] = build_live_metrics(
            model=args.model,
            parent_messages=state["parent_messages"],
            before_child_messages=state["before_child_messages"],
            after_child_messages=state["after_child_messages"],
            max_completion_tokens=args.max_completion_tokens,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
