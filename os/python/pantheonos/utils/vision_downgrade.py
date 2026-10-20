"""
Vision fallback for user-message images on a non-vision main model.

When the main run model can't see images (catalog `supports_vision` is false),
image blocks embedded in USER messages (e.g. a client-annotated image) are
invisible to it. Mirror the `observe_images` sub-agent pattern: describe each
image with a vision-capable model — found across providers that have credentials,
honouring the `vision.vision_model` setting — and replace the image block with
the text description, so a text-only model still gets the content. If no vision
provider is configured, replace the image with a note telling the model/user to
switch or add a key.

Called before the LLM call, on the *deepcopied*
history, so nothing is persisted. A bounded in-memory cache keyed by the image
bytes + accompanying text avoids re-describing the same image on every turn.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any

from loguru import logger

# Legacy per-process LRU; explicit App calls use ModelCallScope's own cache.
# Keys include the active model, image URL and question. Neither cache persists.
_DESC_CACHE: "OrderedDict[str, str]" = OrderedDict()
_DESC_CACHE_MAX = 256

_DESCRIBE_SYSTEM = (
    "You are a vision assistant. Describe the attached image factually and "
    "concisely so a colleague who cannot see it understands what it shows. If the "
    "message lists numbered annotations, describe specifically what is at each "
    "numbered mark."
)

_NO_VISION_NOTE = (
    "[An image was attached here, but the current model cannot see images and no "
    "vision-capable provider is configured. Tell the user to switch to a "
    "vision-capable model, or add an API key for one (Anthropic / OpenAI / Gemini "
    "/ Z.ai / Moonshot / Qwen / Groq / Mistral / OpenRouter).]"
)


def _is_image_block(block: Any) -> bool:
    return (
        isinstance(block, dict)
        and block.get("type") == "image_url"
        and isinstance(block.get("image_url"), dict)
        and bool(block["image_url"].get("url"))
    )


def _has_image(content: Any) -> bool:
    return isinstance(content, list) and any(_is_image_block(b) for b in content)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
    return ""


def _model_supports_vision(model: str | None, scope=None) -> bool:
    if not model:
        return False
    try:
        from .provider_registry import get_model_info

        info = scope.model_info(model) if scope is not None else get_model_info(model)
        return bool(info.get("supports_vision"))
    except Exception:
        return False


def _vision_candidates(active_model: str | None, scope=None) -> list[str]:
    """Vision-capable models reachable with current credentials (same selection
    as observe_images: honour vision.vision_model, else the auto chain)."""
    try:
        from .model_selector import get_model_selector
        from ..settings import get_settings
    except Exception:
        return []

    try:
        vision_cfg = (scope.settings if scope is not None else get_settings()).get_vision_model()
    except Exception:
        vision_cfg = "auto"

    if scope is not None:
        from .model_selector import ModelSelector
        selector = ModelSelector(scope.settings, scope=scope)
    else:
        selector = get_model_selector()
    try:
        return selector.find_vision_models(active_model, vision_cfg)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[vision_downgrade] candidate search failed: {e}")
        return []


async def _describe(question: str, image_url: str, active_model: str | None, scope=None) -> str | None:
    """Describe one image via a vision model. None if no vision provider / all fail."""
    from .llm import acompletion

    candidates = _vision_candidates(active_model, scope) if scope is not None else _vision_candidates(active_model)
    if not candidates:
        return None

    user_content = [
        {"type": "text", "text": question or "Describe this image in detail."},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    for model in candidates:
        try:
            resp = await acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": _DESCRIBE_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                model_params={"temperature": 0.0},
                num_retries=1,
                **({'scope': scope} if scope is not None else {}),
            )
            text = ((resp.get('content') if isinstance(resp, dict) else resp.choices[0].message.content) or "").strip()
            if text:
                logger.info(f"[vision_downgrade] described image via {model}")
                return text
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[vision_downgrade] describe via {model} failed: {e}")
            continue
    return None


async def downgrade_blind_user_images(history: list[dict], model: str | None, *, scope=None) -> list[dict]:
    """Replace image blocks in USER messages with a vision-model text description
    when `model` cannot see images. No-op for vision models and image-free
    history. Mutates `history` in place and returns it.

    Safe to call every turn: the vision model / image-free fast paths return
    immediately, and repeated images hit the in-memory cache instead of
    re-describing.
    """
    if not any(isinstance(msg, dict) and msg.get('role') == 'user' and _has_image(msg.get('content'))
               for msg in history) or _model_supports_vision(model, scope):
        return history

    cache = scope.vision_descriptions if scope is not None else _DESC_CACHE

    for msg in history:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not _has_image(content):
            continue

        question = _text_of(content)
        new_content: list[dict] = []
        for block in content:
            if not _is_image_block(block):
                new_content.append(block)
                continue
            url = block["image_url"]["url"]
            key = hashlib.sha256(
                ((model or "") + "\x00" + question + "\x00" + url).encode("utf-8", "ignore")
            ).hexdigest()
            desc = cache.get(key)
            if desc is not None:
                cache.move_to_end(key)
            if desc is None:
                desc = (await _describe(question, url, model, scope) if scope is not None
                        else await _describe(question, url, model))
                if desc is not None:
                    cache[key] = desc
                    cache.move_to_end(key)
                    while len(cache) > _DESC_CACHE_MAX:
                        cache.popitem(last=False)
            if desc:
                new_content.append({
                    "type": "text",
                    "text": (
                        "[Image description (the current model cannot see images "
                        f"directly, so a vision model was asked to look): {desc}]"
                    ),
                })
            else:
                new_content.append({"type": "text", "text": _NO_VISION_NOTE})
        msg["content"] = new_content

    return history


__all__ = ["downgrade_blind_user_images"]
