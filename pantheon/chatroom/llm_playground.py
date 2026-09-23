"""Isolated, single-turn model experiments. Never changes the agent's routing or history."""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import llm_playground_media as media


@dataclass
class Route:
    source: str
    label: str
    billing: str
    sdk: str
    base: str | None
    key: str | None
    available: bool
    reason: str = ""

    def public(self) -> dict:
        parts = urlsplit(self.base or "")
        endpoint = urlunsplit((parts.scheme, parts.netloc.split("@")[-1], parts.path, "", ""))
        return dict(id=self.source, label=self.label, billing=self.billing,
                    endpoint=endpoint, available=self.available, reason=self.reason)


def routes() -> dict[str, Route]:
    from pantheon.settings import get_settings
    from pantheon.utils.llm_providers import get_provider_api_key, get_provider_base_url
    from pantheon.utils.provider_registry import load_catalog
    from pantheon.utils.oauth import CodexOAuthManager, GeminiCliOAuthManager
    import os

    settings = get_settings()
    platform_base = os.getenv("PANTHEON_PLATFORM_PROXY_BASE")
    platform_key = os.getenv("PANTHEON_PLATFORM_PROXY_KEY")
    # A plain custom LLM_API_BASE is not evidence of platform billing.
    if not platform_base and os.getenv("PLATFORM_MODEL_MODE"):
        platform_base = settings.get_api_key("LLM_API_BASE")
        platform_key = settings.get_api_key("LLM_API_KEY")
    out = {"platform": Route("platform", "Platform budget", "Platform budget", "openai",
                              platform_base, platform_key, bool(platform_base and platform_key),
                              "Connect to a platform-backed runtime to use your platform budget.")}
    for provider, config in load_catalog().get("providers", {}).items():
        key = get_provider_api_key(provider, config.get("api_key_env"))
        base = get_provider_base_url(provider, config) or config.get("base_url")
        billing = "Your API account"
        available = bool(key)
        reason = "Configure this provider's API key in Settings → LLM API Keys."
        sdk = config.get("sdk", "openai")
        if provider in ("codex", "gemini-cli"):
            manager = CodexOAuthManager() if provider == "codex" else GeminiCliOAuthManager()
            available = manager.is_authenticated()
            billing, sdk = "Your OAuth account", provider
            reason = "Sign in to this provider in Settings → LLM API Keys."
        elif config.get("local"):
            available, key, billing = True, "local", "Local compute"
            reason = "The model server must be reachable from the Agent runtime."
        out[provider] = Route(provider, f"{provider} · {'OAuth' if provider in ('codex', 'gemini-cli') else 'Local' if config.get('local') else 'BYOK'}",
                              billing, sdk, base, key, available, reason)
    return out


def _card(model: str, name: str, info: dict, source: str, vendor: str) -> dict:
    # Unknown prices/capabilities are null, not free/unsupported.
    caps = {label: info.get(key) for label, key in {
        "vision": "supports_vision", "tools": "supports_function_calling",
        "reasoning": "supports_reasoning", "structured_output": "supports_response_schema",
        "pdf": "supports_pdf_input", "audio": "supports_audio_input",
        "web_search": "supports_web_search",
    }.items()}
    operation = {"image_generation": "image", "audio_transcription": "transcription",
                 "audio_speech": "speech", "embedding": "embedding"}.get(info.get("mode"), "text")
    return dict(model=model, name=name, source=source, vendor=vendor, operations=[operation],
                operation_reasons={operation: media.route_reason(source, "", operation)},
                context=info.get("max_input_tokens"), max_output=info.get("max_output_tokens"),
                input_price=info.get("input_cost_per_million"), output_price=info.get("output_cost_per_million"),
                capabilities=caps, created=info.get("_created"), metadata_source="Bundled provider catalog")


async def catalog() -> dict:
    from pantheon.utils import openrouter_catalog as oc
    from pantheon.utils.provider_registry import load_catalog
    from pantheon.utils.model_selector import get_saved_models, fetch_ollama_status
    from pantheon.settings import get_settings

    await oc.ensure_fresh()
    source_routes = routes()
    models = []
    or_rows = oc.search("", limit=10000)
    for row in or_rows:
        info = oc.get_model_info(row["model"]) or {}
        detail = oc.get_model_card(row["model"]) or {}
        for source in ("platform", "openrouter"):
            card = _card(row["model"], row["name"], info, source, row["model"].split("/")[1])
            card.update(created=detail.get("created"), metadata_source="OpenRouter catalog",
                        efforts=(oc.effort_levels(row["model"]) or {}).get("efforts", []))
            models.append(card)
    saved = get_saved_models(get_settings())
    for provider, config in load_catalog().get("providers", {}).items():
        if provider == "openrouter":
            continue
        entries = dict(config.get("models", {}))
        for model in saved.get(provider, []):
            entries.setdefault(model, {})
        if provider == "ollama":
            base = (source_routes[provider].base or "http://localhost:11434").rstrip("/")
            available, discovered = await fetch_ollama_status(base.removesuffix("/v1"))
            source_routes[provider].available = available
            for model in discovered:
                entries.setdefault(model, {})
        for model, info in entries.items():
            models.append(_card(f"{provider}/{model}", model, info, provider, provider))
    media_models, warnings = await media.media_catalog()
    merged = {(card["source"], card["model"]): card for card in models}
    for card in media_models:
        # Dedicated modality endpoints are authoritative. Do not present speech or
        # embedding models discovered by /models as ordinary chat completions.
        key = (card["source"], card["model"])
        old = merged.get(key)
        if card["source"] == "platform" and "image" in card["operations"] and old is None:
            card["operation_reasons"]["image"] = "This model needs the dedicated image API. The current platform adapter uses chat-compatible image models; select OpenRouter BYOK for this model."
        if old and old.get("metadata_source") == "OpenRouter media catalog":
            card["operations"] = list(dict.fromkeys(old["operations"] + card["operations"]))
            card["operation_reasons"] = {**old["operation_reasons"], **card["operation_reasons"]}
        merged[key] = card
    public_sources = [route.public() for route in source_routes.values()]
    try:
        from pantheon.models.client import get_client
        fleet_sources, fleet_models = await get_client().catalog()
        public_sources.extend(fleet_sources)
        for card in fleet_models:
            merged[(card['source'], card['model'])] = card
    except Exception:
        warnings.append('Fleet Model Services catalog is unavailable. Platform and BYOK sources remain available.')
    return dict(success=True, sources=public_sources,
                models=list(merged.values()), warnings=warnings, catalog=oc.catalog_status())


class Playground:
    def __init__(self):
        self.tasks: dict[str, asyncio.Task] = {}
        self.recent: deque[str] = deque(maxlen=128)
        self.media = media.MediaStore()
        self.progress: dict[str, dict] = {}

    def status(self, request_id: str) -> dict:
        return {"running": request_id in self.tasks, **self.progress.get(request_id, {})}

    def cancel(self, request_id: str) -> dict:
        task = self.tasks.get(request_id)
        if task:
            self.progress.setdefault(request_id, {})['cancel_requested'] = True
            task.cancel()
        # Also guard cancellation racing ahead of the start RPC.
        if request_id not in self.recent:
            self.recent.append(request_id)
        return {"success": True, "cancelled": bool(task)}

    async def run(self, request_id: str, source: str, model: str, prompt: str,
                  system: str = "", max_tokens: int = 1024, temperature: float | None = None,
                  reasoning_effort: str = "", operation: str = "text", parameters: dict | None = None) -> dict:
        parameters = {} if parameters is None else parameters
        media.validate(operation, parameters)
        if not re.fullmatch(r"[\w-]{8,100}", request_id):
            raise ValueError("Invalid request id")
        if request_id in self.tasks or request_id in self.recent:
            raise ValueError("This request has already been submitted or cancelled. Start a new test.")
        if len(self.tasks) >= 4:
            raise ValueError("Four tests are already running. Wait for a result or cancel one.")
        if not isinstance(prompt, str) or (not prompt.strip() and operation != "transcription") or len(prompt) > 100000:
            raise ValueError("Enter a prompt of 1–100,000 characters.")
        if not isinstance(system, str) or len(system) > 20000:
            raise ValueError("System prompt is too long (20,000 character limit).")
        if not isinstance(model, str) or not model.strip() or len(model) > 4096:
            raise ValueError("Choose a concrete model.")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 32768:
            raise ValueError("Max output tokens must be between 1 and 32,768.")
        if temperature is not None and (not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or not 0 <= temperature <= 2):
            raise ValueError("Temperature must be between 0 and 2, or left unset.")
        if reasoning_effort not in ("", "none", "minimal", "low", "medium", "high", "xhigh", "max"):
            raise ValueError("Unsupported reasoning effort.")
        alias = source.startswith('fleet-route:')
        fleet = alias or source.startswith('fleet:')
        if fleet:
            from pantheon.models.client import parse_ref, parse_route_ref
            expected = 'fleet-route:' + parse_route_ref(model) if alias else 'fleet:' + parse_ref(model)[0]
            if source != expected or operation not in ('text', 'embedding', 'rerank'):
                raise ValueError('Choose a published model and operation from this Fleet service')
        route = (Route(source, source, 'Fleet model service', 'fleet', None, None, True)
                 if fleet else routes().get(source))
        if not route or not route.available:
            raise ValueError(route.reason if route else "Unknown source")
        # Never reinterpret another provider's id or silently fall back to a model.
        prefix = 'fleet-route://' if alias else 'fleet-model://' if fleet else "openrouter/" if source == "platform" else f"{source}/"
        if not model.startswith(prefix) or not model[len(prefix):]:
            raise ValueError(f"Choose a {source} model id beginning with {prefix}")
        reason = '' if fleet else media.route_reason(source, route.sdk, operation)
        if reason:
            raise ValueError(reason)
        self.recent.append(request_id)
        self.progress[request_id] = {"status": "submitting"}
        call = (self._complete_fleet_job(request_id, model, prompt, parameters) if fleet and operation == 'rerank'
                else self._complete_fleet(model, prompt, system, max_tokens, temperature, reasoning_effort, operation, parameters)
                if fleet else self._complete(route, model, prompt, system, max_tokens, temperature, reasoning_effort)
                if operation == "text" else media.complete(route, model, prompt, operation, parameters, self.media, self.progress[request_id]))
        task = asyncio.create_task(call)
        self.tasks[request_id] = task
        timeout = 600 if operation == "video" else 180
        try:
            return await asyncio.wait_for(task, timeout)
        except asyncio.CancelledError:
            return {"success": False, "cancelled": True, "job_id": self.progress[request_id].get("job_id"),
                    "job_ref": self.progress[request_id].get("job_ref"),
                    "job_policy": self.progress[request_id].get("job_policy"),
                    "message": "Stopped waiting. A submitted video job may continue at the provider and incur charges." if operation == "video" else "Test cancelled. The provider may bill work already performed."}
        except asyncio.TimeoutError:
            return {"success": False, "job_id": self.progress[request_id].get("job_id"),
                    "job_ref": self.progress[request_id].get("job_ref"),
                    "job_policy": self.progress[request_id].get("job_policy"),
                    "message": f"The model did not finish within {timeout} seconds. No automatic retry was sent." + (" The submitted video may continue at the provider." if operation == "video" else " The submitted Fleet job may continue; inspect its original job status." if fleet and operation == 'rerank' else "")}
        except Exception as exc:
            message = str(exc)
            if route.key:
                message = message.replace(route.key, "[redacted]")
            message = re.sub(r"sk-[\w-]+", "[redacted]", message)
            message = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", message)
            return {"success": False, "message": message[:1200],
                    "job_ref": self.progress[request_id].get("job_ref"),
                    "job_policy": self.progress[request_id].get("job_policy"),
                    "route": self.progress[request_id].get('route', route.public())}
        finally:
            self.tasks.pop(request_id, None)
            self.progress.pop(request_id, None)

    async def _complete_fleet(self, model, prompt, system, max_tokens, temperature, effort, operation, parameters):
        from pantheon.models.client import get_client
        messages = ([{'role': 'system', 'content': system}] if system else []) + [{'role': 'user', 'content': prompt}]
        params = dict(parameters)
        if operation == 'text':
            params['max_tokens'] = max_tokens
            if temperature is not None:
                params['temperature'] = temperature
            if effort:
                params['reasoning_effort'] = effort
        result = await get_client().complete(model, messages, model_params=params, operation=operation, inputs=prompt)
        return dict(success=True, model=model, returned_model=result.get('model'),
                    output=result.get('content', ''), reasoning=result.get('reasoning_content', result.get('reasoning', '')),
                    usage=result.get('usage', {}), route=result['route'], data=result.get('data'),
                    finish_reason=result.get('finish_reason'), elapsed_ms=result.get('elapsed_ms'),
                    first_token_ms=result.get('first_token_ms'),
                    cost_note='Local compute or your API account. Platform budget is not used.')

    async def _complete_fleet_job(self, request_id, model, query, parameters):
        from pantheon.models.client import get_client
        from pantheon.models.jobs import ACTIVE
        params = dict(parameters)
        inputs = {'query': query, 'documents': params.pop('documents')}
        async with get_client().inference(model, 'rerank') as session:
            # Record a stable handle before submission so a lost ACK is still
            # inspectable. No new ID, alias resolution or automatic replay.
            progress = self.progress[request_id]
            progress.update(job_id=request_id, job_ref=f'fleet-job://{session.deployment}/{request_id}',
                            job_policy=session.route.get('transport_policy', 'relay_allowed'), route=session.route)
            try:
                record = await session.submit(inputs, request_id=request_id, parameters=params)
                while record['state'] in ACTIVE:
                    progress['status'] = record['state']
                    await asyncio.sleep(.25)
                    record = await session.status(request_id)
            except asyncio.CancelledError:
                # Observer timeout/disconnection must not kill a durable job.
                # Only the user's explicit Cancel requests cancellation upstream.
                if progress.get('cancel_requested'):
                    try:
                        await asyncio.shield(session.cancel(request_id))
                    except Exception:
                        pass  # An uncertain cancellation must not become another submission.
                raise
            data = record.get('result') or {}
            return dict(success=record['state'] == 'succeeded', model=model, returned_model=record.get('model'),
                        output=json.dumps(data.get('results', []), ensure_ascii=False, indent=2) if data else '',
                        data=data, usage=data.get('usage', {}), route=session.route,
                        finish_reason=record['state'], elapsed_ms=record.get('elapsed_ms'),
                        job_id=record['job_id'], job_ref=record['ref'],
                        job_policy=progress['job_policy'],
                        message='' if record['state'] == 'succeeded' else 'Inference job ended: ' + record['state'],
                        cost_note='Local compute or your API account. Platform budget is not used.')

    async def _complete(self, route: Route, model: str, prompt: str, system: str,
                        max_tokens: int, temperature: float | None, effort: str) -> dict:
        from pantheon.utils.adapters import get_adapter
        from pantheon.utils.llm import stream_chunk_builder, _normalize_output_token_param
        from pantheon.utils.provider_registry import load_catalog

        started = time.monotonic()
        first_token = None

        async def chunk(delta):
            nonlocal first_token
            if first_token is None and (delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning")):
                first_token = round((time.monotonic() - started) * 1000)

        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        params: dict[str, Any] = {"max_tokens": max_tokens}
        if temperature is not None:
            params["temperature"] = temperature
        if effort:
            params["reasoning_effort"] = effort
        key = route.key
        if route.source == "codex":
            from pantheon.utils.oauth import CodexOAuthManager
            oauth = CodexOAuthManager()
            key = oauth.get_access_token(auto_refresh=True)
            account = oauth.get_account_id()
            if account:
                params["account_id"] = account
        elif route.source == "gemini-cli":
            from pantheon.utils.oauth import GeminiCliOAuthManager
            key = GeminiCliOAuthManager().build_api_key_payload(refresh_if_needed=True, import_if_missing=False)
        if not key:
            raise ValueError("Credentials are no longer available. Reconnect in Settings.")
        # Retain the refreshed credential only on this request-local route so errors
        # can redact it too. It is never included by Route.public().
        route.key = key
        requested = model if route.source == "platform" else model.split("/", 1)[1]
        from pantheon.utils.llm_providers import ProviderConfig, ProviderType, is_responses_api_model
        config = load_catalog().get("providers", {}).get(route.source, {})
        responses_only = route.sdk == "openai" and is_responses_api_model(ProviderConfig(
            provider_type=ProviderType.OPENAI, model_name=requested,
            responses_required_models=config.get("responses_required_models", {}),
        ))
        params = _normalize_output_token_param(model, params, api_mode="responses" if responses_only else "chat")
        call = dict(model=requested, messages=messages, tools=None, response_format=None,
                    process_chunk=chunk, base_url=route.base, api_key=key, **params)
        if responses_only:
            if temperature is not None:
                raise ValueError("Leave temperature unset for this Responses API model.")
            chunks = await get_adapter(route.sdk).acompletion_responses(**call)
        else:
            chunks = await get_adapter(route.sdk).acompletion(stream=True, num_retries=1, **call)
        response = chunks if isinstance(chunks, dict) else stream_chunk_builder(chunks)
        if isinstance(response, dict):
            output = response.get("content", "")
            usage = response.get("_metadata", {}).get("_debug_usage", response.get("usage", {}))
            reasoning = response.get("reasoning_content", "")
            returned_model = response.get("model") or None
            finish_reason = response.get("finish_reason")
        else:
            message = response.choices[0].message
            output = message.content or ""
            reasoning = getattr(message, "reasoning_content", "") or ""
            usage = vars(response.usage) if hasattr(response.usage, "__dict__") else response.usage or {}
            returned_model = response.model or None
            finish_reason = response.choices[0].finish_reason
        # Preserve provider-reported usage. Do not invent zero tokens when absent.
        from pantheon.utils import openrouter_catalog as oc
        if model.startswith("openrouter/"):
            info = oc.get_model_info(model) or {}
        else:
            info = load_catalog().get("providers", {}).get(route.source, {}).get("models", {}).get(requested, {})
        estimate = None
        inputs, outputs = usage.get("prompt_tokens"), usage.get("completion_tokens")
        inp_price, out_price = info.get("input_cost_per_million"), info.get("output_cost_per_million")
        if all(value is not None for value in (inputs, outputs, inp_price, out_price)):
            estimate = (inputs * inp_price + outputs * out_price) / 1_000_000
        return dict(success=True, model=model, returned_model=returned_model, route=route.public(),
                    output=output, reasoning=reasoning, usage=usage, finish_reason=finish_reason,
                    elapsed_ms=round((time.monotonic() - started) * 1000), first_token_ms=first_token,
                    estimated_cost_usd=estimate, cost_note="Catalog estimate; discounts, caching and provider fees may differ.")
