"""set_llm_proxy carries the deployment's platform model mode to the local backend.

The Desktop backend runs locally, so it never sees the deployment's
``PLATFORM_MODEL_MODE`` env — without it, ``list_models`` cannot offer the
platform's OpenRouter catalog (``platform_models_by_provider``) and
``openrouter/<vendor>/<model>`` picks fail provider validation. The frontend now
forwards the mode with the proxy creds; the RPC mirrors it into this process.
"""

import os

import pytest

from pantheon.chatroom.room import ChatRoom

PROXY_ENVS = (
    "LLM_FORCE_PROXY",
    "PANTHEON_PLATFORM_PROXY_BASE",
    "PANTHEON_PLATFORM_PROXY_KEY",
    "PLATFORM_MODEL_MODE",
)


@pytest.fixture()
def chatroom(monkeypatch: pytest.MonkeyPatch) -> ChatRoom:
    for env in PROXY_ENVS:
        monkeypatch.delenv(env, raising=False)
    return ChatRoom.__new__(ChatRoom)


@pytest.mark.asyncio
async def test_enable_with_openrouter_mode_sets_env(chatroom: ChatRoom):
    resp = await chatroom.set_llm_proxy(
        True, "https://proxy.example/v1", "sk-virtual", model_mode="openrouter"
    )
    assert resp["success"] is True
    assert os.environ["LLM_FORCE_PROXY"] == "true"
    assert os.environ["PLATFORM_MODEL_MODE"] == "openrouter"


@pytest.mark.asyncio
async def test_enable_without_mode_leaves_env_untouched(
    chatroom: ChatRoom, monkeypatch: pytest.MonkeyPatch
):
    # Older frontends omit model_mode — a pre-set deployment env must survive.
    monkeypatch.setenv("PLATFORM_MODEL_MODE", "openrouter")
    resp = await chatroom.set_llm_proxy(True, "https://proxy.example/v1", "sk-virtual")
    assert resp["success"] is True
    assert os.environ["PLATFORM_MODEL_MODE"] == "openrouter"


@pytest.mark.asyncio
async def test_enable_with_direct_mode_clears_env(
    chatroom: ChatRoom, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PLATFORM_MODEL_MODE", "openrouter")
    resp = await chatroom.set_llm_proxy(
        True, "https://proxy.example/v1", "sk-virtual", model_mode="direct"
    )
    assert resp["success"] is True
    assert "PLATFORM_MODEL_MODE" not in os.environ


@pytest.mark.asyncio
async def test_disable_clears_all_proxy_env(chatroom: ChatRoom):
    await chatroom.set_llm_proxy(
        True, "https://proxy.example/v1", "sk-virtual", model_mode="openrouter"
    )
    resp = await chatroom.set_llm_proxy(False)
    assert resp["success"] is True
    for env in PROXY_ENVS:
        assert env not in os.environ
