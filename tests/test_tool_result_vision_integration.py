"""
End-to-end integration tests for native tool-result image routing.

These tests hit real provider APIs to verify:
1. Anthropic accepts our `tool_result` with image content blocks.
2. Gemini accepts our `functionResponse` + `inline_data` pattern.
3. OpenAI Chat Completions does NOT reject messages after our sanitiser
   strips images from tool-role content.

Each test crafts a minimal conversation:
    user: "what's in the picture?"
    assistant: tool_call(observe_images, {...})
    tool:  [{"type":"text", "text":"..."}, {"type":"image_url", "image_url":{"url":"..."}}]

then asks the model to describe the image. The image contains a distinctive
marker (text rendered via PIL) so we can assert the model actually SAW it.

Skip behavior: each test skips if the corresponding API key is missing.
Run with ``pytest -m live_llm`` to execute only these tests, or
``pytest --no-header`` with keys set to run as part of the full suite.
"""

from __future__ import annotations

import base64
import io
import os
import sys
import uuid

import pytest

# Load .env file if present (mirrors test_provider_adapters.py).
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image, ImageDraw

from pantheon.utils.adapters.anthropic_adapter import AnthropicAdapter
from pantheon.utils.adapters.gemini_adapter import GeminiAdapter
from pantheon.utils.adapters.openai_adapter import OpenAIAdapter


pytestmark = pytest.mark.live_llm


# ============================================================================
# Helpers
# ============================================================================


def _marker_seen(response_text: str, marker: str) -> bool:
    """Fuzzy check: the marker was correctly read.

    PIL's default font renders character boundaries imperfectly, so a vision
    model may transcribe a gap (e.g. between letters and digits) as a slash
    or space. Accept the marker if its two halves both appear in the response.
    """
    response_lower = response_text.lower()
    marker_lower = marker.lower()
    if marker_lower in response_lower:
        return True
    # Split into alphabetic/numeric halves and ensure both appear.
    import re as _re
    parts = [p for p in _re.split(r"(\d+)", marker_lower) if p]
    return all(part in response_lower for part in parts)


def _render_marker_image(marker: str) -> str:
    """Render a PNG with bold recognisable text and return a data URI.

    Large, high-contrast text so any capable vision model can read it.
    """
    img = Image.new("RGB", (640, 200), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Use default PIL font (no external font file required).
    # Default font is small; duplicate the text a few times so it fills the
    # canvas even at the default pixel size.
    lines = [marker, marker, marker]
    y = 40
    for line in lines:
        draw.text((40, y), line, fill=(0, 0, 0))
        y += 50
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def _build_conversation(data_uri: str, marker: str) -> list[dict]:
    """Build a Chat-Completions-style conversation where a tool returned an image."""
    tool_call_id = f"call_{uuid.uuid4().hex[:12]}"
    return [
        {
            "role": "user",
            "content": (
                "I need you to describe what text appears in the image that a "
                "tool is about to return. Answer with the text content only, "
                "nothing else."
            ),
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": "observe_images",
                        "arguments": '{"question": "what text?"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": "observe_images",
            "content": [
                {"type": "text", "text": "Here is the requested image."},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        },
    ]


def _build_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "observe_images",
                "description": "Observe an image and answer a question about it.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                    },
                    "required": ["question"],
                },
            },
        }
    ]


async def _collect_text_from_chunks(chunks) -> str:
    """Walk an iterable of OpenAI-style chunk dicts and join delta content."""
    parts: list[str] = []
    async_iter = chunks if hasattr(chunks, "__aiter__") else None
    if async_iter is not None:
        async for chunk in chunks:
            _accumulate(chunk, parts)
    else:
        # Already a list (our adapters return list[dict])
        for chunk in chunks:
            _accumulate(chunk, parts)
    return "".join(parts)


def _accumulate(chunk: dict, parts: list[str]) -> None:
    if not isinstance(chunk, dict):
        return
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if content:
            parts.append(content)


# ============================================================================
# Anthropic
# ============================================================================


@pytest.mark.asyncio
async def test_anthropic_sees_image_in_tool_result():
    """End-to-end: Anthropic should receive the image via tool_result and read it."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    marker = "PANTHEON7777"
    data_uri = _render_marker_image(marker)
    messages = _build_conversation(data_uri, marker)

    adapter = AnthropicAdapter()
    chunks = await adapter.acompletion(
        model="claude-sonnet-4-5",
        messages=messages,
        tools=_build_tools(),
        stream=True,
        num_retries=1,
        max_tokens=128,
        temperature=0.0,
    )
    text = await _collect_text_from_chunks(chunks)
    assert text, "Anthropic returned empty response"
    assert _marker_seen(text, marker), (
        f"Anthropic reply did not contain marker {marker!r}. Got: {text[:300]}"
    )


# ============================================================================
# Gemini
# ============================================================================


@pytest.mark.asyncio
async def test_gemini_sees_image_in_tool_result():
    """End-to-end: Gemini should receive the image via functionResponse + inline_data."""
    api_key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get(
        "GOOGLE_API_KEY", ""
    )
    if not api_key:
        pytest.skip("GEMINI_API_KEY (or GOOGLE_API_KEY) not set")

    marker = "PANTHEON8888"
    data_uri = _render_marker_image(marker)
    messages = _build_conversation(data_uri, marker)

    adapter = GeminiAdapter()
    chunks = await adapter.acompletion(
        model="gemini-2.5-flash",
        messages=messages,
        tools=_build_tools(),
        stream=True,
        num_retries=1,
        max_output_tokens=128,
        temperature=0.0,
        api_key=api_key,
    )
    text = await _collect_text_from_chunks(chunks)
    assert text, "Gemini returned empty response"
    assert _marker_seen(text, marker), (
        f"Gemini reply did not contain marker {marker!r}. Got: {text[:300]}"
    )


# ============================================================================
# OpenAI Chat Completions (fallback): must NOT reject the call
# ============================================================================


# ---- OpenAI Chat Completions fallback: must NOT reject the call ----

# Default is gpt-5.4-mini (current mainline). gpt-4o-mini exercises the
# older Chat Completions path. Both should survive our sanitiser.
CHAT_COMPLETIONS_MODELS = [
    "gpt-5.4-mini",
    "gpt-4o-mini",
]


@pytest.mark.parametrize("model", CHAT_COMPLETIONS_MODELS)
@pytest.mark.asyncio
async def test_openai_chat_completions_accepts_sanitised_tool_image(model):
    """OpenAI Chat Completions must NOT 400 after the sanitiser strips
    images from tool message.

    The model can't see the image (that's a provider limitation), but the
    call must NOT raise a ``BadRequestError`` complaining about image_url
    in tool messages. Auth errors (401) are treated as "key problem, skip".
    """
    import openai as openai_mod

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")

    marker = "PANTHEON9999"
    data_uri = _render_marker_image(marker)
    messages = _build_conversation(data_uri, marker)

    # Newer OpenAI models (gpt-5.x) require max_completion_tokens; older ones
    # use max_tokens. We pass both isn't allowed, so pick based on model name.
    token_param = (
        "max_completion_tokens"
        if model.startswith(("gpt-5", "o1", "o3", "o4"))
        else "max_tokens"
    )
    call_kwargs = {token_param: 64}

    adapter = OpenAIAdapter()
    try:
        await adapter.acompletion(
            model=model,
            messages=messages,
            tools=_build_tools(),
            stream=True,
            num_retries=1,
            **call_kwargs,
        )
    except openai_mod.AuthenticationError:
        pytest.skip(f"OPENAI_API_KEY invalid (401) on {model}")
    except openai_mod.NotFoundError:
        pytest.skip(f"Model {model} not available on this account")
    except openai_mod.BadRequestError as e:
        # A 400 mentioning image URLs means our sanitiser did not strip them.
        msg = str(e).lower()
        if "image" in msg and ("tool" in msg or "role" in msg):
            raise AssertionError(
                f"Sanitiser failed to strip image from tool message: {e}"
            ) from e
        # Parameter-compat errors (temperature not supported, etc.) are not
        # about our changes — skip so the test stays useful across model lineups.
        if "unsupported parameter" in msg or "not supported" in msg:
            pytest.skip(f"{model} rejected a benign request param: {e}")
        raise  # any other 400 is a real bug we should see
    # Reaching here means the request was well-formed for Chat Completions.


# ---- OpenAI Responses API: images in tool_result ARE supported ----

# Models that go through acompletion_responses (contain "codex" or end with "-pro").
RESPONSES_API_MODELS = [
    "codex-mini-latest",
    "gpt-5.2-codex",
]


@pytest.mark.parametrize("model", RESPONSES_API_MODELS)
@pytest.mark.asyncio
async def test_openai_responses_api_sees_image_in_tool_result(model):
    """End-to-end: OpenAI Responses API should receive the image via
    ``function_call_output`` with ``input_image`` items and read the marker.

    The acompletion_responses code path is a separate route from Chat
    Completions and exercises llm._tool_output_for_responses.
    """
    import openai as openai_mod
    from pantheon.utils.llm import acompletion_responses

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set")

    marker = f"PANTHEON_RESP_{abs(hash(model)) % 10000:04d}"
    data_uri = _render_marker_image(marker)
    messages = _build_conversation(data_uri, marker)

    try:
        resp = await acompletion_responses(
            messages=messages,
            model=model,
            tools=_build_tools(),
            model_params={"max_output_tokens": 256},
        )
    except openai_mod.AuthenticationError:
        pytest.skip(f"OPENAI_API_KEY invalid (401) on {model}")
    except openai_mod.NotFoundError:
        pytest.skip(f"Model {model} not available on this account")
    except openai_mod.PermissionDeniedError:
        pytest.skip(f"Model {model} not permitted on this account")
    except openai_mod.BadRequestError as e:
        msg = str(e).lower()
        if "unsupported parameter" in msg or "not supported" in msg:
            pytest.skip(f"{model} rejected a benign request param: {e}")
        if "does not exist" in msg or "model_not_found" in msg:
            pytest.skip(f"{model} not available on this account")
        raise

    # acompletion_responses returns a dict with .choices or a normalised message.
    text = ""
    if isinstance(resp, dict):
        # Walk a few common shapes for the response text.
        choices = resp.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            text = msg.get("content") or ""
        if not text:
            text = resp.get("content") or ""
    else:
        # SimpleNamespace / pydantic object path.
        try:
            text = resp.choices[0].message.content or ""
        except Exception:
            text = str(resp)

    assert text, f"{model} returned empty response"
    assert _marker_seen(text, marker), (
        f"{model} did not contain marker {marker!r}. Got: {text[:300]}"
    )


# ============================================================================
# Structured metadata preservation in native mode
# ============================================================================


@pytest.mark.asyncio
async def test_native_mode_preserves_structured_metadata():
    """End-to-end: in native mode, the agent framework merges structured
    tool fields with image blocks. Claude should see BOTH the image AND
    critical ID fields (cell_id, notebook_path, etc.) in its tool_result.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    # Simulate a notebook execute tool return shape with images + structured data.
    marker = "PANTHEON_STRUCT_4242"
    img_data_uri = _render_marker_image(marker)

    # This is the shape integrated_notebook now produces in native mode.
    tool_result_dict = {
        "success": True,
        "cell_id": "unique-cell-xyz-9",
        "notebook_path": "fake_eda.ipynb",
        "execution_count": 42,
        "outputs": [
            {"output_type": "stream", "text": "Loaded 10k rows\n"},
        ],
        "content_blocks": [
            {"type": "image_url", "image_url": {"url": img_data_uri}},
        ],
    }

    # Simulate agent.py's merge: strip content_blocks, JSON-dump the rest
    # as text, prepend to blocks.
    import json as _json

    structured = {k: v for k, v in tool_result_dict.items() if k != "content_blocks"}
    text_summary = _json.dumps(structured, indent=2)
    merged_content = [
        {"type": "text", "text": text_summary},
        *tool_result_dict["content_blocks"],
    ]

    tool_call_id = f"call_{uuid.uuid4().hex[:12]}"
    messages = [
        {
            "role": "user",
            "content": (
                "Look at the tool result. Answer TWO things:\n"
                "1. What is the cell_id?\n"
                "2. What text does the image contain?\n"
                "Answer in plain text, one item per line."
            ),
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": "notebook_execute",
                        "arguments": '{"cell_id": "unique-cell-xyz-9"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": "notebook_execute",
            "content": merged_content,
        },
    ]

    adapter = AnthropicAdapter()
    chunks = await adapter.acompletion(
        model="claude-sonnet-4-5",
        messages=messages,
        tools=_build_tools(),
        stream=True,
        num_retries=1,
        max_tokens=512,
        temperature=0.0,
    )
    text = await _collect_text_from_chunks(chunks)
    assert text, "Anthropic returned empty response"
    # Claude must be able to read BOTH the structured cell_id AND the image.
    assert "unique-cell-xyz-9" in text, (
        f"Structured cell_id lost. Got: {text!r}"
    )
    assert _marker_seen(text, marker), (
        f"Image content not read. Got: {text!r}"
    )


# ============================================================================
# Smoke: ensure the file-manager tool works end-to-end under Anthropic
# ============================================================================


@pytest.mark.asyncio
async def test_observe_images_native_path_end_to_end(tmp_path):
    """End-to-end: observe_images builds content blocks that an adapter can ship.

    Uses AnthropicAdapter directly since that's the simplest native provider.
    Skipped when ANTHROPIC_API_KEY is missing.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    # Write a recognisable image to disk.
    marker = "OBSERVE4242"
    img_path = tmp_path / "marker.png"
    img = Image.new("RGB", (640, 200), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    for y in (40, 90, 140):
        draw.text((40, y), marker, fill=(0, 0, 0))
    img.save(img_path, format="PNG")

    # Build the exact content blocks observe_images produces in native mode.
    from pantheon.utils.vision import path_to_image_url

    data_uri = path_to_image_url(str(img_path))
    content_blocks = [
        {
            "type": "text",
            "text": "Attached 1 image(s). Question: what text appears in the image?",
        },
        {"type": "image_url", "image_url": {"url": data_uri}},
    ]

    tool_call_id = f"call_{uuid.uuid4().hex[:12]}"
    messages = [
        {"role": "user", "content": "What text is in the attached image?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": "observe_images",
                        "arguments": '{"question": "what text?"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": "observe_images",
            "content": content_blocks,
        },
    ]

    adapter = AnthropicAdapter()
    chunks = await adapter.acompletion(
        model="claude-sonnet-4-5",
        messages=messages,
        tools=_build_tools(),
        stream=True,
        num_retries=1,
        max_tokens=128,
        temperature=0.0,
    )
    text = await _collect_text_from_chunks(chunks)
    assert _marker_seen(text, marker), (
        f"Anthropic did not read marker {marker!r} from observe_images content. "
        f"Got: {text[:300]}"
    )
