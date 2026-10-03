"""Provider/model metadata for platform clients, without Agent execution."""

import asyncio
from pantheon.settings import get_settings
from pantheon.toolset import tool
from pantheon.utils.log import logger


class ModelDirectoryAPI:
    def _provider_settings(self):
        return get_settings()

    @tool
    async def reload_settings(self) -> dict:
        """Refresh platform configuration, without changing any running App.

        Platform readers resolve the selected project's settings per operation.
        Validate a fresh isolated view here, preserving deployment environment
        precedence. Agent's legacy override keeps its process-local reload until
        it moves into the separately managed Agent App.
        """
        def refresh():
            settings = self._provider_settings()
            settings.reload(env_override=False)
            return {
                "success": True,
                "scope": "platform",
                "project_path": str(settings.work_dir),
                "message": "Platform settings refreshed. Running Apps manage their own configuration reload.",
            }
        try:
            return await asyncio.to_thread(refresh)
        except Exception:
            # Parser/credential errors can contain file contents or keys.
            logger.warning("Platform settings reload failed")
            return {"success": False, "scope": "platform",
                    "message": "Could not reload platform settings."}

    async def _stop_model_directory(self):
        task = getattr(self, '_ollama_refresh_task', None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @tool
    async def saved_models(self, saved_models: dict[str, list[str]] | None = None) -> dict:
        """Get or persist provider models used by the UI model selector."""
        return await asyncio.to_thread(self._saved_models, saved_models)

    def _saved_models(self, saved_models: dict[str, list[str]] | None = None) -> dict:
        """Get or persist provider models used by the UI model selector."""
        from pantheon.utils.model_selector import get_saved_models, normalize_saved_models

        settings = self._provider_settings()
        if saved_models is None:
            return {
                "success": True,
                "saved_models": get_saved_models(settings),
            }

        normalized = normalize_saved_models(saved_models)
        settings.persist_project_value("models.saved_models", normalized)
        settings.reload()

        return {
            "success": True,
            "saved_models": normalized,
            "message": "Saved models updated.",
        }


    @tool
    async def discover_provider_models(
        self,
        provider: str,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> dict:
        """Discover provider models asynchronously without persisting them."""
        from pantheon.utils.model_discovery import discover_provider_models, normalize_provider_name
        from pantheon.utils.provider_registry import get_provider_config
        from pantheon.utils.llm_providers import get_provider_base_env

        def credentials():
            normalized = normalize_provider_name(provider)
            config = get_provider_config(normalized) or {}
            settings = self._provider_settings()
            key_env = config.get('api_key_env')
            key = (api_key or '').strip() or (settings.get_api_key(key_env) if key_env else '') or ''
            if key.startswith('proxy-mode'):
                key = ''
            base_env = get_provider_base_env(normalized, config)
            base = (api_base or '').strip() or (settings.get_api_key(base_env) if base_env else '') or settings.get_api_key('LLM_API_BASE')
            return normalized, key, base
        normalized, key, base = await asyncio.to_thread(credentials)
        return await discover_provider_models(normalized, key, base)


    @tool
    async def list_available_models(self) -> dict:
        """List all available models based on configured API keys.

        Returns models grouped by provider. Only providers with valid API keys
        are included.

        Returns:
            {
                "success": True,
                "available_providers": ["openai", "anthropic"],
                "current_provider": "openai",
                "models_by_provider": {
                    "openai": ["openai/gpt-5.4", "openai/gpt-5.2", ...],
                    "anthropic": ["anthropic/claude-opus-4-5-20251101", ...]
                },
                "supported_tags": ["high", "normal", "low", "vision", ...]
            }
        """
        try:
            from pantheon.utils.model_selector import ModelSelector, refresh_ollama_cache
            from pantheon.utils import openrouter_catalog

            task = getattr(self, '_ollama_refresh_task', None)
            if task is None or task.done():
                self._ollama_refresh_task = asyncio.create_task(refresh_ollama_cache())
            settings = await asyncio.to_thread(self._provider_settings)
            selector = ModelSelector(settings)
            selector._available_providers = None
            selector._detected_provider = None
            # Refresh the OpenRouter catalog (public /models) so its featured tiers
            # + per-model classification are ready. TTL-cached, so only the first
            # call per hour actually fetches; guarded so it never fails the RPC.
            mode = openrouter_catalog.platform_model_mode()
            # Refresh unconditionally (TTL-guarded, best-effort): the catalog's created +
            # cost data drives the picker's newest-first / cheapest-tie-break ordering in
            # BOTH modes, not just openrouter mode.
            try:
                await openrouter_catalog.ensure_fresh()
            except Exception as _or_e:  # noqa: BLE001
                logger.warning(f"openrouter catalog refresh skipped: {_or_e}")
            resp = await asyncio.to_thread(selector.list_available_models)
            # Platform view: when the deployment routes the platform budget through
            # OpenRouter (PLATFORM_MODEL_MODE=openrouter), expose its models as
            # familiar VENDOR groups (Anthropic/OpenAI/Gemini/…) so the picker can
            # present them like separate providers. The frontend switches to this
            # view when the user has the platform budget ON; BYOK still uses
            # models_by_provider (the user's own keys).
            resp["platform_model_mode"] = mode
            # Report the catalog's state explicitly. Without this the frontend could only
            # infer it from "mode=openrouter + no groups", which is indistinguishable from
            # plain BYOK — so a failed fetch silently rendered the user's own-key list as
            # if it were the platform's. Now the picker can show a retry instead of a lie,
            # and flag a stale (disk/snapshot) list rather than passing it off as live.
            _cat = openrouter_catalog.catalog_status()
            resp["platform_catalog_ready"] = bool(_cat["ready"])
            resp["platform_catalog_live"] = bool(_cat["live"])
            resp["platform_catalog_source"] = _cat["source"]
            if mode == "openrouter" and _cat["ready"]:
                resp["platform_models_by_provider"] = openrouter_catalog.by_vendor()
            # Consistent picker ordering + hide openai/codex '-pro' variants, applied to
            # BOTH the direct (models_by_provider) and openrouter (by_vendor) views.
            try:
                if isinstance(resp.get("models_by_provider"), dict):
                    resp["models_by_provider"] = openrouter_catalog.reorder_and_filter(
                        resp["models_by_provider"]
                    )
                if isinstance(resp.get("platform_models_by_provider"), dict):
                    resp["platform_models_by_provider"] = openrouter_catalog.reorder_and_filter(
                        resp["platform_models_by_provider"]
                    )
            except Exception as _reorder_e:  # noqa: BLE001
                logger.warning(f"picker reorder/filter skipped: {_reorder_e}")
            # Per-model reasoning-effort ladders (drive the UI's effort toggle) — the real
            # supported levels per model from the OpenRouter catalog (e.g. gpt-5.6 →
            # none/low/medium/high/xhigh/max; grok-4.5 → low/medium/high, reasoning mandatory).
            try:
                efforts: dict[str, dict] = {}
                for _grp in (resp.get("models_by_provider"), resp.get("platform_models_by_provider")):
                    if isinstance(_grp, dict):
                        for _models in _grp.values():
                            for _m in _models or []:
                                if _m not in efforts:
                                    _lv = openrouter_catalog.effort_levels(_m)
                                    if _lv:
                                        efforts[_m] = _lv
                # Resolve each quality TAG (high/normal/low) to the concrete model it maps to,
                # so the picker can (a) show that model on hover and (b) surface the effort
                # toggle for the default selection (shown as "normal").
                tag_models: dict[str, str] = {}
                for _tag in ("high", "normal", "low"):
                    try:
                        _chain = selector.resolve_model(_tag)
                        if _chain:
                            tag_models[_tag] = _chain[0]
                            if _tag not in efforts:
                                _lv = openrouter_catalog.effort_levels(_chain[0])
                                if _lv:
                                    efforts[_tag] = _lv
                    except Exception:  # noqa: BLE001
                        pass
                resp["reasoning_efforts"] = efforts
                resp["tag_models"] = tag_models
            except Exception as _eff_e:  # noqa: BLE001
                logger.warning(f"reasoning_efforts build skipped: {_eff_e}")
            try:
                from pantheon.models.client import get_client
                services, models = await get_client().catalog()
                for service in services:
                    ids = [m['model'] for m in models if m['source'] == service['id']
                           and 'text' in m['operations']]
                    if service['available'] and ids:
                        label = 'Fleet · ' + service['label']
                        resp.setdefault('models_by_provider', {})[label] = ids
                        resp.setdefault('platform_models_by_provider', {})[label] = ids
            except Exception:
                pass  # A disconnected Fleet must not hide existing platform models.
            return resp
        except Exception as e:
            logger.error(f"Error listing available models: {e}")
            return {"success": False, "message": str(e)}


    @tool
    async def search_openrouter_models(self, query: str = "", limit: int = 40) -> dict:
        """Search the full OpenRouter model catalog (fetched from the public
        /models API) so the UI can pin any of the ~346 models beyond the featured
        tiers.

        Args:
            query: Substring to match against the model id / display name (empty = all).
            limit: Max rows to return.

        Returns:
            {success, results: [{model, name, tier, vision, reasoning, tools,
             input_cost_per_million, output_cost_per_million, context}, ...]}
        """
        try:
            from pantheon.utils import openrouter_catalog

            await openrouter_catalog.ensure_fresh()
            return {"success": True, "results": openrouter_catalog.search(query, limit)}
        except Exception as e:
            logger.error(f"Error searching OpenRouter models: {e}")
            return {"success": False, "message": str(e), "results": []}


    @tool
    async def get_model_details(self, model: str) -> dict:
        """Detail card for the model picker's ⓘ info dialog: price (per 1M in/out), input
        modalities, context window, and release date. Sourced from the OpenRouter catalog
        first (full data in platform-openrouter mode); falls back to litellm's static
        cost/context map for a direct-mode id the catalog doesn't carry.

        Args:
            model: The model id shown in the picker (e.g. "openrouter/x-ai/grok-4.5",
                   "x-ai/grok-4.5", or a bare "gpt-5.6").

        Returns:
            {success, source: "openrouter"|"litellm"|None, info: {model, name, vendor,
             tier, input_cost_per_million, output_cost_per_million, max_input_tokens,
             max_output_tokens, created, modalities:{image,pdf,audio},
             capabilities:{vision,tools,reasoning,web_search,pdf_input,audio_input}}}
        """
        try:
            if model.startswith(('fleet-model://', 'fleet-route://')):
                from pantheon.models.client import get_client
                row, spec = await get_client().describe(model)
                if not spec:
                    return {'success': False, 'message': 'Model is no longer published by this service'}
                return {'success': True, 'source': 'fleet', 'info': {
                    'model': model, 'name': spec.get('name') or spec['id'],
                    'vendor': row['engine'], 'node_id': row['node_id'],
                    'description': row['name'] + ' · ' + (row.get('node_name') or row['node_id']),
                    'max_input_tokens': spec.get('context'), 'max_output_tokens': None,
                    'input_cost_per_million': None, 'output_cost_per_million': None,
                    'capabilities': {k: spec.get(k) for k in ('vision', 'tools', 'reasoning', 'structured_output')},
                    'modalities': {'image': spec.get('vision'), 'pdf': None, 'audio': None},
                }}
            from pantheon.utils import openrouter_catalog

            try:
                await openrouter_catalog.ensure_fresh()
            except Exception:  # noqa: BLE001
                pass
            card = openrouter_catalog.get_model_card(model)
            if card:
                return {"success": True, "source": "openrouter", "info": card}
            # Direct-mode fallback: litellm's static model cost/capability map.
            try:
                def lookup():
                    import litellm
                    return litellm.get_model_info(model) or {}
                mi = await asyncio.to_thread(lookup)
            except Exception:  # noqa: BLE001
                mi = {}
            if mi:
                info = {
                    "model": model,
                    "name": model,
                    "vendor": mi.get("litellm_provider", ""),
                    "tier": "normal",
                    "input_cost_per_million": (mi.get("input_cost_per_token") or 0.0) * 1_000_000,
                    "output_cost_per_million": (mi.get("output_cost_per_token") or 0.0) * 1_000_000,
                    "max_input_tokens": mi.get("max_input_tokens") or 0,
                    "max_output_tokens": mi.get("max_output_tokens") or 0,
                    "created": 0,
                    "modalities": {
                        "image": bool(mi.get("supports_vision")),
                        "pdf": bool(mi.get("supports_pdf_input")),
                        "audio": bool(mi.get("supports_audio_input")),
                    },
                    "capabilities": {
                        "vision": bool(mi.get("supports_vision")),
                        "tools": bool(mi.get("supports_function_calling")),
                        "reasoning": bool(mi.get("supports_reasoning")),
                        "web_search": bool(mi.get("supports_web_search")),
                        "pdf_input": bool(mi.get("supports_pdf_input")),
                        "audio_input": bool(mi.get("supports_audio_input")),
                    },
                }
                return {"success": True, "source": "litellm", "info": info}
            # Nothing known — minimal card so the dialog can show "info unavailable".
            return {"success": True, "source": None, "info": {"model": model, "name": model}}
        except Exception as e:
            logger.error(f"Error getting model details for {model}: {e}")
            return {"success": False, "message": str(e)}


    @tool(exclude=True)
    async def check_api_keys(self) -> dict:
        """Report configured provider credentials without returning their secrets."""
        return await asyncio.to_thread(self._check_api_keys)

    def _check_api_keys(self) -> dict:
        """Check the configuration status of LLM API keys.

        Returns a dict with each key's status (configured, source, masked value)
        and whether any key is configured at all.
        """
        import os
        from pantheon.settings import LEGACY_API_KEY_ENV_MAP

        settings = self._provider_settings()
        from pantheon.utils.llm_providers import get_provider_base_env
        from pantheon.utils.provider_registry import load_catalog

        key_names = []
        base_url_names = []
        for provider_key, provider_config in load_catalog().get("providers", {}).items():
            key_env = provider_config.get("api_key_env")
            if key_env and key_env not in key_names:
                key_names.append(key_env)
            base_env = get_provider_base_env(provider_key, provider_config)
            if base_env and base_env not in base_url_names:
                base_url_names.append(base_env)
        fallback_names = [
            "LLM_API_BASE",
            "LLM_API_KEY",
        ]

        def _status(name: str) -> dict:
            value = settings.get_api_key(name)
            if not value:
                return {"configured": False, "source": None, "masked": None}

            legacy_key = LEGACY_API_KEY_ENV_MAP.get(name)
            source = "settings"
            environment = getattr(settings, "_environment", None)
            if environment is None:
                environment = os.environ
            if environment.get(name) or (legacy_key and environment.get(legacy_key)):
                source = "env"

            masked = value if "BASE" in name else (value[:6] + "***" if len(value) > 6 else "***")
            return {"configured": True, "source": source, "masked": masked}

        keys = {}
        for key in key_names:
            keys[key] = _status(key)

        base_urls = {}
        for key in base_url_names:
            base_urls[key] = _status(key)

        fallback = {}
        for key in fallback_names:
            fallback[key] = _status(key)

        has_any_key = any(v["configured"] for v in keys.values())
        has_any_base = any(v["configured"] for v in base_urls.values())
        has_fallback = all(v["configured"] for v in fallback.values())
        return {
            "keys": keys,
            "base_urls": base_urls,
            "fallback": fallback,
            "has_any_key": has_any_key,
            "has_any_base_url": has_any_base,
            "has_fallback": has_fallback,
        }


    @tool
    async def ollama_status(self, url: str = "http://localhost:11434") -> dict:
        """Check Ollama server status and list available models.

        Args:
            url: Ollama server URL (default: http://localhost:11434)

        Returns:
            Dict with running status, model list, and URL.
        """
        try:
            from pantheon.utils.model_selector import fetch_ollama_status

            running, models = await fetch_ollama_status(url)
            return {"running": running, "models": models, "url": url}
        except Exception:
            return {"running": False, "models": [], "url": url}
