"""Catalog fetch resilience.

The bug these cover: a cold sandbox's first outbound connection fails, the single-shot
fetch gave up, the cache stayed empty, and the picker rendered the BYOK default list
(4 models) as if it were the platform's. Every test here is about the cache ending up
POPULATED — and about callers being able to tell live data from a fallback.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from pantheon.utils import openrouter_catalog as oc


PAYLOAD = {
    "data": [
        {
            "id": "anthropic/claude-opus-4.8",
            "name": "Claude Opus 4.8",
            "created": 1_760_000_000,
            "context_length": 200_000,
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": ["tools", "reasoning"],
            "pricing": {"prompt": "0.000015", "completion": "0.000075"},
            "top_provider": {"context_length": 200_000, "max_completion_tokens": 64_000},
            "reasoning": {"supported_efforts": ["high", "medium", "low"], "mandatory": False},
        },
        {
            "id": "x-ai/grok-4.5",
            "name": "Grok 4.5",
            "created": 1_770_000_000,
            "context_length": 256_000,
            "architecture": {"input_modalities": ["text"]},
            "supported_parameters": ["tools", "reasoning"],
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            "top_provider": {"context_length": 256_000, "max_completion_tokens": 32_000},
            "reasoning": {"supported_efforts": ["high", "low"], "mandatory": True},
        },
    ]
}


@pytest.fixture(autouse=True)
def _reset_catalog(tmp_path, monkeypatch):
    """Module-level cache + on-disk paths are global; isolate every test."""
    oc._CACHE.clear()
    monkeypatch.setattr(oc, "_FETCHED_AT", 0.0, raising=False)
    monkeypatch.setattr(oc, "_ATTEMPTED_AT", 0.0, raising=False)
    monkeypatch.setattr(oc, "_SOURCE", "", raising=False)
    monkeypatch.setattr(oc, "_DISK_CACHE", tmp_path / "disk.json", raising=False)
    monkeypatch.setattr(oc, "_SNAPSHOT", tmp_path / "snapshot.json", raising=False)
    monkeypatch.delenv("PANTHEON_HUB_URL", raising=False)
    # Keep the retry tests fast — the backoff schedule is the thing under test, not the wait.
    monkeypatch.setattr(oc, "_RETRY_BACKOFF", (0.0, 0.0), raising=False)
    yield
    oc._CACHE.clear()


def _client_factory(behaviour):
    """behaviour(url) -> payload dict, or raises to simulate a failure."""
    calls: list[str] = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kwargs):
            calls.append(url)
            payload = behaviour(url)

            class _Resp:
                def raise_for_status(self):
                    return None

                def json(self):
                    return payload

            return _Resp()

    return _Client, calls


def test_retries_then_succeeds_on_a_late_attempt():
    """The cold-egress window: the first connection dies, a later one works. The old
    single-attempt fetch turned this into an empty cache."""
    attempts = {"n": 0}

    def behaviour(url):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("egress not ready")
        return PAYLOAD

    client, _ = _client_factory(behaviour)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", client)
        asyncio.run(oc.ensure_fresh())

    assert attempts["n"] == 3
    status = oc.catalog_status()
    assert status["ready"] is True
    assert status["live"] is True
    assert status["source"] == "openrouter"
    assert status["model_count"] == 2


def test_falls_back_to_snapshot_when_the_network_is_gone(tmp_path):
    """No network at all → a real list from the bundled snapshot, NOT an empty cache
    (which is what made the picker show the wrong, BYOK-shaped list)."""
    oc._SNAPSHOT.write_text(json.dumps(PAYLOAD))

    def behaviour(url):
        raise httpx.ConnectError("no egress")

    client, calls = _client_factory(behaviour)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", client)
        asyncio.run(oc.ensure_fresh())

    assert len(calls) == 3  # 1 + 2 retries, openrouter only (no hub configured)
    status = oc.catalog_status()
    assert status["ready"] is True
    assert status["live"] is False, "a snapshot must never be reported as live"
    assert status["source"] == "snapshot"
    assert oc.is_loaded() is True


def test_disk_cache_wins_over_snapshot():
    """The last good payload is fresher than whatever shipped in the image."""
    oc._SNAPSHOT.write_text(json.dumps({"data": []}))
    oc._DISK_CACHE.write_text(json.dumps(PAYLOAD))

    def behaviour(url):
        raise httpx.ConnectError("no egress")

    client, _ = _client_factory(behaviour)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", client)
        asyncio.run(oc.ensure_fresh())

    assert oc.catalog_status()["source"] == "disk"


def test_hub_mirror_is_preferred_and_persisted(monkeypatch):
    """L5: the Hub's warm mirror is tried before openrouter.ai, and a successful fetch
    is written to disk so the NEXT cold start starts warm."""
    monkeypatch.setenv("PANTHEON_HUB_URL", "https://hub.example.com/")

    client, calls = _client_factory(lambda url: PAYLOAD)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", client)
        asyncio.run(oc.ensure_fresh())

    assert calls == ["https://hub.example.com/api/model-catalog"]
    assert oc.catalog_status()["source"] == "hub"
    assert json.loads(oc._DISK_CACHE.read_text()) == PAYLOAD


def test_hub_failure_falls_through_to_openrouter(monkeypatch):
    monkeypatch.setenv("PANTHEON_HUB_URL", "https://hub.example.com")

    def behaviour(url):
        if "hub.example.com" in url:
            raise httpx.ConnectError("hub down")
        return PAYLOAD

    client, calls = _client_factory(behaviour)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", client)
        asyncio.run(oc.ensure_fresh())

    assert calls[-1] == oc._MODELS_URL
    assert oc.catalog_status()["source"] == "openrouter"


def test_a_live_cache_is_not_refetched_within_the_ttl():
    client, calls = _client_factory(lambda url: PAYLOAD)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", client)
        asyncio.run(oc.ensure_fresh())
        asyncio.run(oc.ensure_fresh())

    assert len(calls) == 1


def test_a_fallback_cache_keeps_retrying_the_network(monkeypatch):
    """A snapshot load must NOT satisfy the TTL — otherwise one cold start would pin the
    sandbox to a stale list for an hour. Throttled by _STALE_RETRY_SECONDS, not the TTL."""
    oc._SNAPSHOT.write_text(json.dumps(PAYLOAD))
    monkeypatch.setattr(oc, "_STALE_RETRY_SECONDS", 0.0, raising=False)

    state = {"online": False}

    def behaviour(url):
        if not state["online"]:
            raise httpx.ConnectError("still cold")
        return PAYLOAD

    client, _ = _client_factory(behaviour)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", client)
        asyncio.run(oc.ensure_fresh())
        assert oc.catalog_status()["source"] == "snapshot"

        state["online"] = True
        asyncio.run(oc.ensure_fresh())

    status = oc.catalog_status()
    assert status["source"] == "openrouter"
    assert status["live"] is True


def test_by_vendor_groups_from_a_fallback_catalog():
    """The picker's vendor view must work off the fallback too — that's the whole point."""
    oc._SNAPSHOT.write_text(json.dumps(PAYLOAD))

    def behaviour(url):
        raise httpx.ConnectError("no egress")

    client, _ = _client_factory(behaviour)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", client)
        asyncio.run(oc.ensure_fresh())

    groups = oc.by_vendor()
    assert groups["anthropic"] == ["openrouter/anthropic/claude-opus-4.8"]
    assert groups["grok"] == ["openrouter/x-ai/grok-4.5"]


def test_empty_payload_never_clobbers_a_good_cache():
    """An upstream blip returning {"data": []} must not empty the cache."""
    responses = [PAYLOAD, {"data": []}]

    def behaviour(url):
        return responses.pop(0) if responses else {"data": []}

    client, _ = _client_factory(behaviour)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx, "AsyncClient", client)
        asyncio.run(oc.ensure_fresh())
        assert oc.catalog_status()["model_count"] == 2
        asyncio.run(oc.ensure_fresh(force=True))

    assert oc.catalog_status()["model_count"] == 2
