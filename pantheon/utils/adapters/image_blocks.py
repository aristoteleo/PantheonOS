"""
Helpers for translating OpenAI-style image content into provider-native
blocks. Used by anthropic/gemini/responses adapters so that tool messages
can carry image content natively.

Internal canonical format (OpenAI-compatible):
    {"type": "image_url", "image_url": {"url": "data:image/<mime>;base64,<data>" or "file://..."}}

Text blocks stay as {"type": "text", "text": "..."}.
"""

from __future__ import annotations

import base64
import io
import re
from typing import Any


_DATA_URI_RE = re.compile(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.*)$", re.DOTALL)


def _parse_data_uri(url: str) -> tuple[str, str] | None:
    """Parse a data:image URI → (mime, base64_data). Returns None on failure."""
    m = _DATA_URI_RE.match(url)
    if not m:
        return None
    mime_ext = m.group(1).lower()
    # Normalise: "jpg" → "jpeg" (MIME spec)
    if mime_ext == "jpg":
        mime_ext = "jpeg"
    return f"image/{mime_ext}", m.group(2)


def _file_to_data_uri(url: str) -> tuple[str, str] | None:
    """Read file:// or local path → (mime, base64_data). Returns None on failure."""
    from ..vision import get_image_base64

    try:
        data_uri = get_image_base64(url)
    except Exception:
        return None
    return _parse_data_uri(data_uri)


def resolve_image_url(url: str) -> tuple[str, str] | None:
    """Resolve an image URL to (mime, base64_data). Handles data URIs and file paths.

    Returns None for HTTP URLs (let provider handle those) or unparseable inputs.
    """
    if not url:
        return None
    if url.startswith("data:image/"):
        return _parse_data_uri(url)
    if url.startswith("file://") or url.startswith("/") or url.startswith("./"):
        return _file_to_data_uri(url)
    # HTTP / https URLs: the caller decides what to do.
    return None


def split_text_and_images(content: Any) -> tuple[str, list[tuple[str, str]], list[str]]:
    """Split OpenAI-style content into (text, [(mime, base64_data)], [http_urls]).

    Args:
        content: String or list of content blocks.

    Returns:
        (joined_text, inline_images, http_image_urls)
        - joined_text: all text blocks concatenated with blank lines
        - inline_images: list of (mime, base64_data) tuples that can be embedded
        - http_image_urls: HTTP(S) URLs that need provider-specific handling
    """
    if isinstance(content, str):
        return content, [], []

    if not isinstance(content, list):
        return str(content or ""), [], []

    texts: list[str] = []
    inline: list[tuple[str, str]] = []
    http_urls: list[str] = []

    for item in content:
        if not isinstance(item, dict):
            # Stringify unknown items so nothing is silently dropped.
            texts.append(str(item))
            continue

        itype = item.get("type")
        if itype == "text":
            text = item.get("text", "")
            if text:
                texts.append(text)
        elif itype == "image_url":
            url = (item.get("image_url") or {}).get("url", "")
            if not url:
                continue
            resolved = resolve_image_url(url)
            if resolved is not None:
                inline.append(resolved)
            elif url.startswith(("http://", "https://")):
                http_urls.append(url)
            # else: silently skip — unparseable

    return "\n\n".join(texts), inline, http_urls


def has_image_content(content: Any) -> bool:
    """Quick check: does this content list contain any image_url blocks?"""
    if not isinstance(content, list):
        return False
    return any(
        isinstance(i, dict) and i.get("type") == "image_url"
        for i in content
    )


__all__ = [
    "has_image_content",
    "resolve_image_url",
    "split_text_and_images",
]
