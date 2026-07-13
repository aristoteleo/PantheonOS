"""
OpenRouter model catalog — fetch the live /models list and derive per-model
metadata (vision / tools / reasoning / context / cost) so ANY OpenRouter model is
correctly classified without hardcoding it.

OpenRouter's ``/api/v1/models`` is public (no key needed) and returns an
OpenAI-like ``{"data": [...]}`` payload with rich per-model metadata. We cache the
derived catalog in-process with a TTL; ``ensure_fresh()`` (async) does the fetch,
``get_model_info()`` / ``featured_by_tier()`` / ``all_model_ids()`` read it
synchronously (returning empty/None until the first successful fetch, so callers
degrade gracefully).
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .log import logger

_MODELS_URL = "https://openrouter.ai/api/v1/models"
_TTL_SECONDS = 3600  # refresh at most hourly

# openrouter id (e.g. "openai/gpt-5.6") -> derived metadata dict (+ _-prefixed extras)
_CACHE: dict[str, dict] = {}
_FETCHED_AT: float = 0.0

# Vendors surfaced in the picker's DEFAULT (featured) tiers. The full fetched list
# is always searchable; this just keeps the dropdown mainstream.
_NOTABLE_VENDORS = {
    "anthropic", "openai", "google", "x-ai", "meta-llama", "mistralai",
    "qwen", "deepseek", "moonshotai", "z-ai", "microsoft", "amazon", "cohere",
}
_FEATURED_PER_TIER = 8


def _to_float(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _tier_for(out_cost_per_million: float) -> str:
    """Cost-based quality tier from output price ($/1M tokens)."""
    if out_cost_per_million >= 10.0:
        return "high"
    if out_cost_per_million >= 1.5:
        return "normal"
    return "low"


def _derive(entry: dict) -> dict | None:
    """One OpenRouter /models entry -> catalog-style metadata (+ _-prefixed extras)."""
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    arch = entry.get("architecture") or {}
    in_mods = arch.get("input_modalities") or []
    params = entry.get("supported_parameters") or []
    pricing = entry.get("pricing") or {}
    top = entry.get("top_provider") or {}

    in_cost_m = _to_float(pricing.get("prompt")) * 1_000_000
    out_cost_m = _to_float(pricing.get("completion")) * 1_000_000

    return {
        "max_input_tokens": int(entry.get("context_length") or top.get("context_length") or 0) or 200_000,
        "max_output_tokens": int(top.get("max_completion_tokens") or 0) or 32_000,
        "input_cost_per_million": in_cost_m,
        "output_cost_per_million": out_cost_m,
        "input_cost_per_token": in_cost_m / 1_000_000,
        "output_cost_per_token": out_cost_m / 1_000_000,
        "supports_vision": "image" in in_mods,
        "supports_function_calling": "tools" in params,
        "supports_response_schema": ("structured_outputs" in params) or ("response_format" in params),
        "supports_reasoning": ("reasoning" in params) or ("include_reasoning" in params),
        "supports_pdf_input": "file" in in_mods,
        "supports_audio_input": "audio" in in_mods,
        "supports_web_search": "web_search_options" in params,
        "supports_audio_output": False,
        "supports_computer_use": False,
        "supports_assistant_prefill": False,
        # picker-only extras (stripped by get_model_info)
        "_name": entry.get("name") or model_id,
        "_created": int(entry.get("created") or 0),
        "_vendor": model_id.split("/", 1)[0] if "/" in model_id else "",
        "_tier": _tier_for(out_cost_m),
    }


def _parse(payload: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in payload.get("data", []):
        if isinstance(entry, dict):
            info = _derive(entry)
            if info:
                out[entry["id"]] = info
    return out


async def ensure_fresh(force: bool = False) -> None:
    """Fetch + cache the OpenRouter model list if stale (public endpoint, no key).
    Best-effort — a failure leaves the previous cache (or empty) in place."""
    global _FETCHED_AT
    if not force and _CACHE and (time.monotonic() - _FETCHED_AT) < _TTL_SECONDS:
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.get(_MODELS_URL, headers={"Accept": "application/json"})
        resp.raise_for_status()
        parsed = _parse(resp.json())
        if parsed:
            _CACHE.clear()
            _CACHE.update(parsed)
            _FETCHED_AT = time.monotonic()
            logger.info(f"[openrouter_catalog] cached {len(parsed)} models")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[openrouter_catalog] refresh failed: {e}")


def is_loaded() -> bool:
    return bool(_CACHE)


def get_model_info(model_id: str) -> dict | None:
    """Derived metadata for an OpenRouter model id (e.g. 'openai/gpt-5.6'), or None
    if the catalog hasn't been fetched / the id is unknown. Strips _-extras."""
    info = _CACHE.get(model_id)
    if not info:
        return None
    return {k: v for k, v in info.items() if not k.startswith("_")}


def get_model_card(model_id: str) -> dict | None:
    """Display card for the picker's info dialog — price, modalities, context, release —
    normalized into the shape the frontend renders. Accepts a bare (``x-ai/grok-4.5``) or
    ``openrouter/``-prefixed id. Returns None when the catalog doesn't have it (→ caller
    falls back to litellm's static cost map for a direct-mode model)."""
    mid = model_id[len("openrouter/") :] if model_id.startswith("openrouter/") else model_id
    _ensure_loaded_sync()
    info = _CACHE.get(mid)
    if not info:
        return None
    return {
        "model": f"openrouter/{mid}",
        "name": info.get("_name", mid),
        "vendor": info.get("_vendor", ""),
        "tier": info.get("_tier", "normal"),
        "input_cost_per_million": info.get("input_cost_per_million", 0.0),
        "output_cost_per_million": info.get("output_cost_per_million", 0.0),
        "max_input_tokens": info.get("max_input_tokens", 0),
        "max_output_tokens": info.get("max_output_tokens", 0),
        "created": info.get("_created", 0),  # unix ts — the "released" date
        "modalities": {  # input modalities present (text is always in)
            "image": info.get("supports_vision", False),
            "pdf": info.get("supports_pdf_input", False),
            "audio": info.get("supports_audio_input", False),
        },
        "capabilities": {
            "vision": info.get("supports_vision", False),
            "tools": info.get("supports_function_calling", False),
            "reasoning": info.get("supports_reasoning", False),
            "web_search": info.get("supports_web_search", False),
            "pdf_input": info.get("supports_pdf_input", False),
            "audio_input": info.get("supports_audio_input", False),
        },
    }


def _ensure_loaded_sync() -> None:
    """Best-effort SYNC load when the async cache is empty (e.g. a fresh process that
    hasn't served list_available_models yet). Fetches once, TTL-guarded. Used by
    canonical_openrouter_id from the synchronous provider-detection path."""
    global _FETCHED_AT
    if _CACHE and (time.monotonic() - _FETCHED_AT) < _TTL_SECONDS:
        return
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0)) as client:
            resp = client.get(_MODELS_URL, headers={"Accept": "application/json"})
        resp.raise_for_status()
        parsed = _parse(resp.json())
        if parsed:
            _CACHE.clear()
            _CACHE.update(parsed)
            _FETCHED_AT = time.monotonic()
            logger.info(f"[openrouter_catalog] sync-cached {len(parsed)} models")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[openrouter_catalog] sync refresh failed: {e}")


# Bare-model-name prefix → OpenRouter vendor. Lets bare ids route WITHOUT the live catalog
# (the agent runs in the endpoint process, where the sync catalog fetch has proven flaky —
# so relying on it left a bare "grok-4.5" the proxy couldn't place). Deterministic + offline.
_BARE_PREFIX_TO_VENDOR = {
    "grok": "x-ai", "kimi": "moonshotai", "glm": "z-ai", "deepseek": "deepseek",
    "qwen": "qwen", "qwq": "qwen", "gemma": "google", "llama": "meta-llama",
    "mistral": "mistralai", "mixtral": "mistralai", "codestral": "mistralai",
    "command": "cohere", "phi": "microsoft", "nova": "amazon", "sonar": "perplexity",
}
# Vendors that live ONLY on OpenRouter (no proxy-native route), so "<vendor>/<model>" can be
# namespaced to openrouter/ without a catalog-membership check.
_OR_ONLY_VENDORS = frozenset({
    "x-ai", "moonshotai", "z-ai", "deepseek", "qwen", "meta-llama", "mistralai",
    "cohere", "microsoft", "amazon", "perplexity", "nvidia", "google",
})


def canonical_openrouter_id(model: str) -> str | None:
    """Canonicalize a model to the full ``openrouter/<vendor>/<model>`` id the LiteLLM
    proxy's ``openrouter/*`` group needs — platform mode routes EVERY model through OpenRouter
    (unified billing). Accepts:
      • already-namespaced ``openrouter/...``            → unchanged
      • ``<vendor>/<model>`` (``x-ai/grok-4.5``)         → ``openrouter/<vendor>/<model>``
        (OpenRouter-only vendors go straight through; anthropic/openai check the catalog for
        the dash-vs-dot naming mismatch so a native-only release falls back to its own route)
      • a bare name (``grok-4.5``)                       → vendor via catalog, else prefix map
    Returns None only when the vendor can't be determined (→ caller keeps the native route)."""
    if not model or not isinstance(model, str):
        return None
    if model.startswith("openrouter/"):
        return model
    if "/" in model:
        vendor = model.split("/", 1)[0].lower()
        if vendor in _OR_ONLY_VENDORS:
            return f"openrouter/{model}"  # OpenRouter-only vendor — no catalog needed
        # anthropic/openai/gemini and the like: only via OpenRouter if it actually serves it
        # (our ``claude-opus-4-8`` dash id isn't OpenRouter's ``claude-opus-4.8`` dot id).
        _ensure_loaded_sync()
        return f"openrouter/{model}" if model in _CACHE else None
    # Bare name — prefer the catalog's exact id, then fall back to the offline prefix map.
    _ensure_loaded_sync()
    hits = [mid for mid in _CACHE if mid.rsplit("/", 1)[-1] == model]
    if hits:
        return f"openrouter/{sorted(hits, key=len)[0]}"
    prefix = model.split("-", 1)[0].split(".")[0].lower()
    vendor = _BARE_PREFIX_TO_VENDOR.get(prefix)
    return f"openrouter/{vendor}/{model}" if vendor else None


def featured_by_tier(per_tier: int = _FEATURED_PER_TIER) -> dict[str, list[str]]:
    """Curated dropdown default: notable-vendor models grouped by cost tier, newest
    first, capped per tier. Returns ``openrouter/<id>`` strings. Empty until fetched."""
    # Keep the dropdown to mainstream chat models — drop image/audio/free/preview
    # variants (still searchable). Substring match on the id is enough.
    _NOISE = ("-image", "-audio", "-tts", "-realtime", ":free", "-online", ":extended")
    buckets: dict[str, list[tuple[int, str]]] = {"high": [], "normal": [], "low": []}
    for mid, info in _CACHE.items():
        if info.get("_vendor") not in _NOTABLE_VENDORS:
            continue
        if any(n in mid for n in _NOISE):
            continue
        buckets.setdefault(info.get("_tier", "normal"), []).append(
            (info.get("_created", 0), mid)
        )
    out: dict[str, list[str]] = {}
    for tier, items in buckets.items():
        items.sort(key=lambda t: t[0], reverse=True)
        out[tier] = [f"openrouter/{mid}" for _, mid in items[:per_tier]]
    return out


# OpenRouter id vendor prefix -> our provider key (so the picker can present the
# platform's OpenRouter models as familiar vendor groups). Only these are surfaced
# as groups; other vendors are reachable via search.
_VENDOR_TO_PROVIDER = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "gemini",
    "x-ai": "grok",
    "moonshotai": "moonshot",
    "qwen": "qwen",
    "deepseek": "deepseek",
    "meta-llama": "meta",
    "mistralai": "mistral",
    "z-ai": "zai",
    "cohere": "cohere",
    "microsoft": "microsoft",
    "amazon": "amazon",
    "nvidia": "nvidia",
    "perplexity": "perplexity",
}


def by_vendor(per_vendor: int = 16) -> dict[str, list[str]]:
    """Group OpenRouter models by vendor -> provider key for the platform picker
    view. Returns {provider_key: ['openrouter/<vendor>/<model>', ...]} (routed via
    OpenRouter), newest first, capped per vendor, noise-filtered. Empty until fetched.
    Final ordering + the openai/codex '-pro' hide are applied by reorder_and_filter()."""
    _NOISE = ("-image", "-audio", "-tts", "-realtime", ":free", "-online", ":extended")
    buckets: dict[str, list[tuple[int, str]]] = {}
    for mid, info in _CACHE.items():
        pkey = _VENDOR_TO_PROVIDER.get(info.get("_vendor", ""))
        if not pkey or any(n in mid for n in _NOISE):
            continue
        buckets.setdefault(pkey, []).append((info.get("_created", 0), mid))
    out: dict[str, list[str]] = {}
    for pkey, items in buckets.items():
        items.sort(key=lambda t: t[0], reverse=True)
        out[pkey] = [f"openrouter/{mid}" for _, mid in items[:per_vendor]]
    return out


# Providers whose '-pro' variants are hidden from the picker (per product decision:
# they're the priciest, least-used tier). Applied by provider key AND vendor segment.
_HIDE_PRO_PROVIDERS = ("openai", "codex")
_DAY_SECONDS = 86400


def _catalog_key(model_id: str) -> str:
    """Strip a leading 'openrouter/' so an id from either picker view maps to the catalog
    key (`<vendor>/<model>`). Direct-mode ids like 'openai/gpt-5.6' pass through unchanged."""
    return model_id[len("openrouter/") :] if model_id.startswith("openrouter/") else model_id


def _picker_sort_key(model_id: str) -> tuple:
    """Sort key: NEWEST first (created bucketed to the day so a same-day release batch groups
    together), then CHEAPEST first within a day (output then input cost). Models the catalog
    doesn't carry get created=0 (oldest) + high cost, so they sink to the bottom in a stable
    way (unknown → last, order otherwise preserved)."""
    info = _CACHE.get(_catalog_key(model_id)) or {}
    created = int(info.get("_created", 0) or 0)
    out_cost = info.get("output_cost_per_million")
    in_cost = info.get("input_cost_per_million")
    out_cost = out_cost if out_cost is not None else 1e12
    in_cost = in_cost if in_cost is not None else 1e12
    return (-(created // _DAY_SECONDS), out_cost, in_cost)


def _is_hidden_pro(pkey: str, model_id: str) -> bool:
    """True for an openai/codex model whose base name ends in '-pro'. Checks BOTH the group
    key (direct mode has an 'openai'/'codex' provider group) and the id's vendor segment (the
    openrouter view groups codex under 'openai'), so it catches the variant in either mode."""
    base = model_id.rsplit("/", 1)[-1].lower()
    if not base.endswith("-pro"):
        return False
    key = _catalog_key(model_id)
    vendor = key.split("/", 1)[0].lower() if "/" in key else ""
    return str(pkey).lower() in _HIDE_PRO_PROVIDERS or vendor in _HIDE_PRO_PROVIDERS


def reorder_and_filter(groups: dict[str, list[str]]) -> dict[str, list[str]]:
    """Shared post-processing for BOTH picker views — the direct ``models_by_provider`` AND
    the openrouter ``by_vendor`` view — so ordering/visibility match in every mode:
      • hide openai/codex ``-pro`` variants, then
      • sort each group newest-first with a cheapest-first tie-break within a release day.
    Unknown-vendor groups are only re-sorted (their ids just sort last if not in the catalog).
    Best-effort: an empty catalog makes the sort a stable no-op, but the ``-pro`` hide (pure
    string check) still applies."""
    if not isinstance(groups, dict):
        return groups
    out: dict[str, list[str]] = {}
    for pkey, models in groups.items():
        if not isinstance(models, list):
            out[pkey] = models
            continue
        kept = [m for m in models if isinstance(m, str) and not _is_hidden_pro(pkey, m)]
        kept.sort(key=_picker_sort_key)
        out[pkey] = kept
    return out


def search(query: str, limit: int = 40) -> list[dict]:
    """Search the full fetched list by id/name substring. Returns picker rows:
    {model: 'openrouter/<id>', name, tier, vision, reasoning, tools,
     input_cost_per_million, output_cost_per_million, context}."""
    q = (query or "").strip().lower()
    rows: list[tuple[int, dict]] = []
    for mid, info in _CACHE.items():
        hay = f"{mid} {info.get('_name', '')}".lower()
        if q and q not in hay:
            continue
        rows.append((info.get("_created", 0), {
            "model": f"openrouter/{mid}",
            "name": info.get("_name", mid),
            "tier": info.get("_tier", "normal"),
            "vision": info.get("supports_vision", False),
            "reasoning": info.get("supports_reasoning", False),
            "tools": info.get("supports_function_calling", False),
            "input_cost_per_million": info.get("input_cost_per_million", 0.0),
            "output_cost_per_million": info.get("output_cost_per_million", 0.0),
            "context": info.get("max_input_tokens", 0),
        }))
    rows.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in rows[:limit]]


def platform_model_mode() -> str:
    """Deployment-level platform model source mode: 'direct' (default) or
    'openrouter'. Set per env (staging/prod) via PLATFORM_MODEL_MODE."""
    import os

    return (os.getenv("PLATFORM_MODEL_MODE", "direct") or "direct").strip().lower()


__all__ = [
    "ensure_fresh",
    "is_loaded",
    "get_model_info",
    "featured_by_tier",
    "by_vendor",
    "search",
    "platform_model_mode",
]
