"""Regression tests for Gemini image-generation response parsing.

Two bugs made multimodal image generation silently return empty image lists:

1. The Gemini SSE adapter ignored `inlineData` parts in the response — the
   image bytes were dropped on the floor.
2. ``stream_chunk_builder`` didn't aggregate per-chunk ``images`` deltas into
   the final assembled message, so even if (1) were fixed the downstream
   ImageGenerationToolSet would see ``message.images == None``.

These tests exercise the fixed code paths end-to-end in-process without
hitting a real Gemini endpoint.
"""

from __future__ import annotations

from pantheon.utils.llm import stream_chunk_builder


_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABXvMqOgAAAABJRU5ErkJggg=="
)


def test_stream_chunk_builder_aggregates_images_delta():
    """images deltas emitted by the gemini adapter must land on message.images."""
    chunks = [
        {
            "choices": [{
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "images": [
                        {"image_url": {"url": f"data:image/png;base64,{_TINY_PNG_B64}"}},
                    ],
                },
                "finish_reason": None,
            }],
        },
        {
            "choices": [{
                "index": 0,
                "delta": {"content": "Here is the image."},
                "finish_reason": None,
            }],
        },
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]

    response = stream_chunk_builder(chunks)
    msg = response.choices[0].message

    assert msg.content == "Here is the image."
    assert msg.images is not None
    assert len(msg.images) == 1
    assert msg.images[0]["image_url"]["url"].startswith("data:image/png;base64,")

    dumped = msg.model_dump()
    assert dumped["images"] == msg.images


def test_stream_chunk_builder_no_images_leaves_attribute_none():
    """For text-only streams message.images stays None — not an empty list —
    so downstream ``or []`` fallbacks keep working."""
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    response = stream_chunk_builder(chunks)
    assert response.choices[0].message.images is None


def test_gemini_adapter_extracts_inline_data_part():
    """Verify the SSE part parser turns an inlineData part into an images
    delta. Runs the adapter's stream loop over a fake httpx response body.
    """
    import asyncio
    import json
    import os
    from unittest.mock import AsyncMock, MagicMock, patch

    # Two SSE frames: one text part, one inlineData part.
    sse_frames = [
        "data: " + json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{"text": "Here you go:"}],
                },
                "finishReason": None,
            }],
        }),
        "",
        "data: " + json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": _TINY_PNG_B64,
                        },
                    }],
                },
                "finishReason": "STOP",
            }],
            "usageMetadata": {
                "promptTokenCount": 5,
                "candidatesTokenCount": 2,
            },
        }),
        "",
    ]

    class _FakeResponse:
        status_code = 200

        async def aiter_lines(self):
            for line in sse_frames:
                yield line

        async def aread(self):
            return b""

    class _FakeStreamCM:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, *_a):
            return False

    class _FakeClientCM:
        async def __aenter__(self):
            fake_client = MagicMock()
            fake_client.stream = _FakeStreamCM
            return fake_client

        async def __aexit__(self, *_a):
            return False

    from pantheon.utils.adapters import gemini_adapter as mod

    async def run():
        with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy"}, clear=False):
            with patch.object(mod.httpx, "AsyncClient", lambda **_kw: _FakeClientCM()):
                adapter = mod.GeminiAdapter()
                chunks = await adapter.acompletion(
                    messages=[{"role": "user", "content": "make an image"}],
                    model="gemini-3.1-flash-image-preview",
                    modalities=["text", "image"],
                )
                return chunks

    chunks = asyncio.run(run())
    response = stream_chunk_builder(chunks)
    msg = response.choices[0].message

    assert msg.content == "Here you go:"
    assert msg.images and len(msg.images) == 1
    url = msg.images[0]["image_url"]["url"]
    assert url == f"data:image/png;base64,{_TINY_PNG_B64}"
