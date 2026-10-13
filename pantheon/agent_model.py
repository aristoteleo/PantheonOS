"""One model call of the agent loop (docs/agent-core.md).

complete() sends the model's view of the conversation and the tool
definitions to one model and returns the assistant message. complete_models()
picks the model: the run's model, then the Agent's fallback list. The Agent
does not retry a failed call; the model layer does (Model Services for Fleet
models, the provider call for direct ones).
"""
from __future__ import annotations

import copy
import time
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from .utils.llm import (
    TimingTracker,
    collect_message_stats_lightweight,
    process_messages_for_model,
)
from .utils.llm_providers import (
    ProviderType,
    call_llm_provider,
    create_enhanced_process_chunk,
    detect_provider,
    get_openai_effective_config,
)
from .utils.log import logger

if TYPE_CHECKING:
    from .agent import Agent

FLEET_PREFIXES = ("fleet-model://", "fleet-route://")


async def complete(
    agent: "Agent",
    messages: list[dict],
    model: str,
    tool_use: bool = True,
    response_format: Any | None = None,
    process_chunk: Callable | None = None,
    context_variables: dict | None = None,
) -> dict:
    """One completion: the model view of messages plus tools, to model."""
    from .agent import get_current_run_context

    tracker = TimingTracker()
    tracker.start("total")
    request_start_time = time.time()
    logger.info(f"🚀 [Agent:{agent.name}] Starting LLM request for model: {model}")

    if model.startswith(FLEET_PREFIXES):
        from pantheon.models.client import get_client
        client = agent.model_scope.fleet() if agent.model_scope is not None else get_client()
        _, published = await client.describe(model)
        if not published or not published.get('context'):
            raise ValueError('Set this model’s context limit in Model Services before using it with Agent history.')

    async with tracker.measure("message_processing"):
        from pantheon.utils.token_optimization import (
            _PROMPT_OVERHEAD_TOKENS,
            _estimate_message_tokens,
            build_llm_view_async,
            inject_cache_control_markers,
            supports_explicit_cache_control,
        )

        run_context = get_current_run_context()
        if run_context is not None:
            run_context.current_model = model
        optimization_memory = run_context.memory if run_context else None
        is_main_thread = run_context.execution_context_id is None if run_context else True
        # What the last call to this model cost beyond the message estimate
        # (tool definitions, tokenizer drift), so compaction triggers in time.
        overheads = agent.__dict__.setdefault("_prompt_overhead_tokens", {})
        overhead_token = _PROMPT_OVERHEAD_TOKENS.set(overheads.get(model, 0))
        try:
            messages = await build_llm_view_async(
                messages,
                memory=optimization_memory,
                is_main_thread=is_main_thread,
                autocompact_model=model,
                **({'model_scope': agent.model_scope, 'base_dir': agent.model_scope.settings.tmp_dir / 'tool-results'}
                   if agent.model_scope is not None else {}),
                context_window_model=model,
            )
        finally:
            _PROMPT_OVERHEAD_TOKENS.reset(overhead_token)
        # Headroom gates see the history without the system prompt, so the
        # system prompt counts as overhead along with the tool definitions.
        estimated_history_tokens = sum(
            _estimate_message_tokens(message) for message in messages if message.get("role") != "system"
        )
        if agent.tool_exposure == "core":
            from .agent_core_tools import CORE_TOOLS, fold_for_model
            messages = fold_for_model(messages, CORE_TOOLS)
        messages = process_messages_for_model(messages, model)
        # Re-pair tool calls and results right before the provider call so its
        # input never contains an orphaned tool result.
        messages = agent._sanitize_messages(messages)
        # Explicit prompt-cache breakpoints (Anthropic, Qwen); other providers
        # cache a stable prefix automatically.
        if supports_explicit_cache_control(model):
            messages = inject_cache_control_markers(messages)
        if run_context is not None:
            run_context.cache_safe_prompt_messages = [
                copy.deepcopy(m) if isinstance(m.get("content"), list) else {**m} for m in messages
            ]

    config_kwargs = {'settings': agent.model_scope.settings} if agent.model_scope is not None else {}
    provider_config = detect_provider(model, agent.relaxed_schema, **config_kwargs)
    # Seed OpenAI-routed config from settings; native providers resolve their
    # base URL and key inside pantheon.utils.llm.acompletion().
    if provider_config.provider_type == ProviderType.OPENAI:
        effective_base, effective_key = get_openai_effective_config(**config_kwargs)
        if not provider_config.base_url and effective_base:
            provider_config.base_url = effective_base
        if (not provider_config.api_key and effective_key
                and (agent.model_scope is None or not provider_config.base_url
                     or provider_config.base_url.rstrip('/') == effective_base.rstrip('/'))):
            provider_config.api_key = effective_key

    tools = None
    if tool_use:
        async with tracker.measure("tools_conversion"):
            tools = await agent.get_tools_for_llm() or None
            if run_context is not None and tools is not None:
                run_context.cache_safe_tool_definitions = copy.deepcopy(tools)
            # Non-OpenAI and OpenAI-compatible providers take a plainer schema.
            is_compat_provider = provider_config.api_key is not None
            if (provider_config.provider_type.value != "openai" or is_compat_provider) and tools:
                for tool in tools:
                    if "function" not in tool:
                        continue
                    func = tool["function"]
                    func.pop("strict", None)
                    if not func.get("description"):
                        func["description"] = func.get("name", "tool")
                    if "parameters" not in func:
                        func["parameters"] = {"type": "object", "properties": {}, "required": []}
                    else:
                        func["parameters"].pop("additionalProperties", None)

    message_id = str(uuid4())
    enhanced_process_chunk = create_enhanced_process_chunk(process_chunk, message_id, agent_name=agent.name)
    if enhanced_process_chunk:
        async with tracker.measure("begin_chunk"):
            await enhanced_process_chunk({"begin": True})

    model_params = agent.model_params
    if context_variables and "model_params" in context_variables:
        model_params = {**agent.model_params, **context_variables["model_params"]}

    if run_context is not None:
        from pantheon.utils.token_optimization import build_cache_safe_runtime_params

        run_context.cache_safe_runtime_params = build_cache_safe_runtime_params(
            model=model, model_params=model_params, response_format=response_format)

    # Stamp the first streamed chunk: setup vs time to first token vs generation.
    llm_start = time.time()
    ttft: dict = {}
    async def probe(chunk, _orig=enhanced_process_chunk):
        if "t" not in ttft:
            ttft["t"] = time.time()
        return await _orig(chunk)

    async with tracker.measure("llm_api"):
        from .utils.model_request import bounded_model_request

        retry_cfg = agent._settings().get("llm_retry", {})
        retry_cfg = retry_cfg if isinstance(retry_cfg, dict) else {}
        message = await bounded_model_request(
            lambda on_chunk: call_llm_provider(
                config=provider_config,
                messages=messages,
                tools=tools,
                response_format=response_format,
                process_chunk=on_chunk,
                model_params=model_params,
                **({'scope': agent.model_scope} if agent.model_scope is not None else {}),
            ),
            probe if enhanced_process_chunk is not None else None,
            model=model,
            idle_timeout=float(retry_cfg.get("idle_timeout", 120)),
            request_timeout=float(retry_cfg.get("request_timeout", 600)),
        )

    if message is None:
        message = {"role": "assistant", "content": "Error: Empty response from model."}

    end_timestamp = time.time()
    total_time = tracker.end("total")
    message["id"] = message_id
    message["timestamp"] = end_timestamp
    message.setdefault("_metadata", {}).update({
        "start_timestamp": request_start_time,
        "end_timestamp": end_timestamp,
    })

    prompt_tokens = (message.get("_metadata", {}).get("_debug_usage") or {}).get("prompt_tokens")
    if isinstance(prompt_tokens, int) and prompt_tokens > 0:
        agent.__dict__.setdefault("_prompt_overhead_tokens", {})[model] = max(0, prompt_tokens - estimated_history_tokens)

    collect_message_stats_lightweight(
        message=message, messages=messages, model=model,
        **({'model_scope': agent.model_scope} if agent.model_scope is not None else {}),
    )

    timings = tracker.get_all()
    meta = message.get("_metadata", {})
    total_tokens = meta.get("total_tokens", 0)
    max_tokens = meta.get("max_tokens", 200000)
    usage_pct = (total_tokens / max_tokens * 100) if max_tokens > 0 else 0
    current_cost = meta.get("current_cost", 0)
    shadow = isinstance(model, str) and model.startswith(("codex/", "gemini-cli/"))
    cost_label = f"~${current_cost:.4f} (shadow)" if shadow else f"${current_cost:.4f}"
    logger.info(
        f"📊 [Agent:{agent.name}] ⏳ Timing: Total: {total_time:.3f}s | "
        f"Message: {timings.get('message_processing', 0):.3f}s | LLM: {timings['llm_api']:.3f}s | "
        f"Tool: {timings.get('tools_conversion', 0):.3f}s for {len(tools or [])} tools "
        f"💬 Tokens: {total_tokens:,} | Usage: {usage_pct:.1f}% | Cost: {cost_label}"
    )
    fmt = lambda v: f"{v:.2f}s" if v is not None else "n/a"
    first = ttft.get("t")
    logger.warning(
        f"[TTAF] agent={agent.name} model={model} tokens={total_tokens} tools={len(tools or [])} | "
        f"setup={llm_start - request_start_time:.2f}s ttft={fmt(first - llm_start if first else None)} "
        f"gen={fmt(end_timestamp - first if first else None)} llm_total={timings['llm_api']:.2f}s "
        f"total={total_time:.2f}s"
    )
    return message


def _model_chain(agent: "Agent", model: str | list[str] | None) -> list:
    """The models to try, in order: the run's model, then the Agent's list.

    Tags resolve to their models and a ``+think`` suffix becomes the
    ``thinking`` model parameter.
    """
    from .agent import _is_model_tag, _parse_thinking_suffix, _resolve_model_tag

    if model is None:
        models = list(agent.models)
    elif isinstance(model, str):
        models = [model, *agent.models]
    else:
        models = [*model, *agent.models]
    if not models:
        raise RuntimeError(f"No model is available. models: {models}")
    normalized: list = []
    effort: str | None = None
    for item in models:
        if not isinstance(item, str):
            normalized.append(item)
            continue
        clean, think = _parse_thinking_suffix(item)
        if think and effort is None:
            effort = think
        if _is_model_tag(clean):
            normalized.extend(agent.model_scope.models(clean) if agent.model_scope is not None
                              else _resolve_model_tag(clean))
        else:
            normalized.append(clean)
    if effort:
        agent.model_params.setdefault("thinking", effort)
    return list(dict.fromkeys(normalized or models))


async def complete_models(
    agent: "Agent",
    history: list[dict],
    tool_use: bool,
    response_format: Any | None,
    process_chunk: Callable | None,
    model: str | list[str] | None = None,
    context_variables: dict | None = None,
) -> dict:
    """complete() on the first model that answers.

    A Fleet model is a placement the user chose: its failure is final. A direct
    model that fails moves to the next one in the chain; one that timed out is
    tried last for a while, so later turns do not wait on it again.
    """
    from .agent import StopRunning, get_current_run_context
    from .utils.model_request import is_model_timeout

    if agent._image_resolver is not None:
        from .utils.image_resources import expand_bound_images
        # Missing file authority cannot be repaired by another model.
        history = await expand_bound_images(history, agent._image_resolver)
        primary = (model[0] if isinstance(model, list) and model else model if isinstance(model, str)
                   else agent.models[0] if agent.models else None)
        if primary and not primary.startswith(FLEET_PREFIXES):
            from .utils.vision_downgrade import downgrade_blind_user_images
            history = await downgrade_blind_user_images(history, primary, scope=agent.model_scope)

    retry_cfg = agent._settings().get("llm_retry", {})
    retry_cfg = retry_cfg if isinstance(retry_cfg, dict) else {}
    timeout_cooldown = float(retry_cfg.get("timeout_cooldown", 600))

    models = _model_chain(agent, model)
    run_context = get_current_run_context()
    if run_context is not None:
        now = time.monotonic()
        models.sort(key=lambda name: run_context.model_retry_after.get(name, 0) > now)

    last_error: Exception | None = None
    for index, model_name in enumerate(models):
        if index:
            logger.warning(f"Trying {model_name} after the previous model failed")
        try:
            return await agent._acompletion(
                history,
                model=model_name,
                tool_use=tool_use,
                response_format=response_format,
                process_chunk=process_chunk,
                context_variables=context_variables,
            )
        except StopRunning:
            raise
        except Exception as error:
            if model_name.startswith(FLEET_PREFIXES):
                raise
            last_error = error
            if is_model_timeout(error) and run_context is not None:
                run_context.model_retry_after[model_name] = time.monotonic() + timeout_cooldown
            logger.opt(exception=True).error(f"[Agent:{agent.name}] Model {model_name} failed: {error}")
    raise RuntimeError(
        f"All {len(models)} model(s) failed. Models tried: {models}. Last error: {last_error}")
