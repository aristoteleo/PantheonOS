"""Suggested publish settings for models discovered on an external API.

A provider's /v1/models reports only ids, so publishing used to rely on a
hand-typed context length (DeepSeek Flash was published at 65,536 instead of
1M). OpenRouter's public model list carries context length and supported
parameters for most provider models; match by model id and suggest them.
Suggestions only pre-fill the publish form; the owner still confirms.
"""
import re
import time

import httpx

OPENROUTER_MODELS = 'https://openrouter.ai/api/v1/models'
_cache = {'at': 0.0, 'models': []}


async def _models(client=None):
    if time.monotonic() - _cache['at'] < 3600 and _cache['models']:
        return _cache['models']
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        response = await client.get(OPENROUTER_MODELS)
        response.raise_for_status()
        _cache.update(at=time.monotonic(), models=response.json().get('data') or [])
    finally:
        if own:
            await client.aclose()
    return _cache['models']


def _base(model_id):
    """'~deepseek/deepseek-flash-latest:free' -> 'deepseek-flash'."""
    name = model_id.lstrip('~').split('/')[-1].split(':')[0].lower()
    return re.sub(r'-latest$', '', name)


def match(models, model_id, provider=''):
    wanted = _base(model_id)
    candidates = [m for m in models if _base(m.get('id', '')) == wanted]
    if provider:
        candidates = [m for m in candidates if m['id'].lstrip('~').startswith(provider + '/')] or candidates
    # Prefer the provider's moving alias (~…-latest), then the shortest id.
    candidates.sort(key=lambda m: (not m['id'].startswith('~'), len(m['id'])))
    return candidates[0] if candidates else None


def suggestion(entry):
    params = set(entry.get('supported_parameters') or [])
    modalities = set((entry.get('architecture') or {}).get('input_modalities') or [])
    context = entry.get('context_length')
    return dict(context=int(context) if context else None, tools='tools' in params,
                reasoning='reasoning' in params or 'include_reasoning' in params,
                vision='image' in modalities, source='openrouter:' + entry['id'])


async def suggest(model_ids, provider='', client=None):
    """{model_id: suggestion} for ids OpenRouter knows; unknown ids are omitted."""
    try:
        models = await _models(client)
    except Exception:
        return {}  # Suggestions are optional; never block discovery.
    found = {}
    for model_id in model_ids:
        entry = match(models, model_id, provider)
        if entry:
            found[model_id] = suggestion(entry)
    return found
