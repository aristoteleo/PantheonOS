"""Agent context accounting shared by the App API and terminal presentation."""

async def get_detailed_token_stats(chatroom, chat_id, team, fallback: dict, model_override: str | None = None) -> dict:
    """Gather detailed token statistics (async) including tools and system prompt."""
    from pantheon.utils.llm import count_tokens_in_messages, process_messages_for_model
    from pantheon.utils.log import logger

    tools = []
    messages = []
    raw_messages = []
    model = "unknown"
    system_prompt = None

    # Get agent/model/tools/instructions
    if team and team.agents:
        # Default to first agent unless we can determine active one
        agent = list(team.agents.values())[0]

        model = (agent.models[0] if isinstance(getattr(agent, 'models', None), list)
                 else getattr(agent, 'models', None) or getattr(agent, 'model', 'unknown'))

        # Resolve model tags (e.g. "high", "normal") to actual model names
        # so litellm.get_model_info() can look up the correct context window
        try:
            from pantheon.agent import _is_model_tag, _resolve_model_tag
            if isinstance(model, str) and _is_model_tag(model):
                resolved = _resolve_model_tag(model)
                if resolved:
                    model = resolved[0]
        except Exception:
            pass

        system_prompt = getattr(agent, 'instructions', None)

        try:
            tools = await agent.get_tools_for_llm()
        except Exception as e:
            logger.warning(f"Failed to get tools: {e}")

    # UI can pass the currently selected model directly — use it for catalog
    # lookup so the context window reflects the selection immediately (before
    # set_agent_model completes).  Token counting still uses agent.models[0]
    # (the model the messages were actually processed with).
    catalog_model = model_override if model_override else model

    tool_names = [t["function"]["name"] for t in tools] if tools else []

    # Try to get messages from chatroom via memory_manager
    if chatroom and chat_id:
        try:
            if hasattr(chatroom, 'memory_manager'):
                # Read-only: getting token/context statistics for display, no need to fix
                memory = chatroom.memory_manager.get_memory(chat_id)
                if memory:
                    # ✅ Get root agent messages for token/context statistics
                    # This prevents context% from being inflated by sub-agent calls
                    raw_messages = memory.get_messages(execution_context_id=None) or []
                    messages = process_messages_for_model(raw_messages, model)

                    # ✅ Get ALL messages for cost calculation (including sub-agents)
                    # Cost should include all LLM calls, not just root agent
                    all_messages_for_cost = memory.get_messages() or []
        except Exception as e:
            logger.warning(f"Failed to get messages for token stats: {e}")

    # Prepend system prompt if not present
    if system_prompt:
        if not messages or messages[0].get("role") != "system":
             messages.insert(0, {"role": "system", "content": system_prompt})

    if messages:
        try:
            # Calculate token statistics (root agent only)
            info = count_tokens_in_messages(
                messages,
                model,
                tools=tools
            )

            # ✅ Override max_tokens from catalog using the UI-selected model
            # (catalog_model).  This ensures the context ring reflects the window
            # of the model the user has selected, even before set_agent_model
            # completes on the backend.
            from pantheon.utils.provider_registry import find_provider_for_model as _fpfm, get_model_info as _gmi
            _provider_key, _, _ = _fpfm(catalog_model)
            _model_in_catalog = _provider_key != "unknown"
            if _model_in_catalog:
                _catalog_info = _gmi(catalog_model)
                _catalog_max = _catalog_info.get("max_input_tokens") or 0
                if _catalog_max > 0:
                    info["max_tokens"] = _catalog_max
                    info["remaining"] = max(0, _catalog_max - info.get("total", 0))
                    _total = info.get("total", 0)
                    info["usage_percent"] = round(_total / _catalog_max * 100, 1) if _catalog_max > 0 else 0
                    info["warning_90"] = info["usage_percent"] >= 90
                    info["critical_95"] = info["usage_percent"] >= 95

            # Fallback: if model not in catalog, try runtime-recorded max_tokens
            # from message metadata (written by collect_message_stats_lightweight).
            if not _model_in_catalog and info.get("max_tokens", 0) <= 200_000 and raw_messages:
                # Find last message with runtime metadata (written by collect_message_stats_lightweight)
                for msg in reversed(raw_messages):
                    meta = msg.get("_metadata", {})
                    runtime_max = meta.get("max_tokens", 0)
                    if runtime_max > 200_000:
                        # Override with runtime value and recalculate derived fields
                        info["max_tokens"] = runtime_max
                        info["remaining"] = max(0, runtime_max - info.get("total", 0))
                        total = info.get("total", 0)
                        info["usage_percent"] = round(total / runtime_max * 100, 1) if runtime_max > 0 else 0
                        info["warning_90"] = info["usage_percent"] >= 90
                        info["critical_95"] = info["usage_percent"] >= 95
                        break

            # ✅ Recalculate total_cost from ALL messages (including compressed)
            # Use for_llm=False to get full message history
            all_messages_for_cost = memory.get_messages(for_llm=False)

            from pantheon.utils.llm import calculate_total_cost_from_messages
            total_cost = calculate_total_cost_from_messages(all_messages_for_cost)

            # Override the cost from count_tokens_in_messages
            info["total_cost"] = total_cost

            info["model"] = model
            info["leader_tools"] = tool_names
            return info
        except Exception as e:
            logger.warning(f"Failed to count tokens: {e}")

    # Fallback if calculation failed — also try to read max_tokens from metadata
    runtime_max_tokens = 200_000
    if raw_messages:
        for msg in reversed(raw_messages):
            meta = msg.get("_metadata", {})
            if meta.get("max_tokens", 0) > 0:
                runtime_max_tokens = meta["max_tokens"]
                break

    total = fallback.get("total_input_tokens", 0) + fallback.get("total_output_tokens", 0)
    return {
        "total": total, "max_tokens": runtime_max_tokens, "remaining": runtime_max_tokens - total,
        "usage_percent": round(total / runtime_max_tokens * 100, 1) if total else 0,
        "by_role": {"user": fallback.get("total_input_tokens", 0), "assistant": fallback.get("total_output_tokens", 0)},
        "message_counts": {"user": fallback.get("message_count", 0), "assistant": fallback.get("message_count", 0)},
        "warning_90": False, "critical_95": False, "current_cost": 0, "model": model,
        "system_prompt": 0, "tools_definition": 0, "error": None, "leader_tools": tool_names
    }
