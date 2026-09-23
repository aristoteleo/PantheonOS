import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from pantheon.chatroom import llm_playground as pg


@pytest.fixture
def configured(monkeypatch):
    source = pg.Route("platform", "Platform budget", "Platform budget", "openai",
                      "https://proxy.example/litellm", "sk-private-key", True)
    own = pg.Route("openrouter", "OpenRouter BYOK", "Your API account", "openai",
                   "https://openrouter.ai/api/v1", "sk-own-key", True)
    monkeypatch.setattr(pg, "routes", lambda: {"platform": source, "openrouter": own})
    adapter = MagicMock()
    async def complete(**kwargs):
        await kwargs["process_chunk"]({"content": "hello"})
        return [
            {"model": kwargs["model"], "choices": [{"delta": {"content": "hello"}, "finish_reason": "stop"}]},
            {"usage": {"prompt_tokens": 10, "completion_tokens": 2}},
        ]
    adapter.acompletion = AsyncMock(side_effect=complete)
    monkeypatch.setattr("pantheon.utils.adapters.get_adapter", lambda sdk: adapter)
    monkeypatch.setattr("pantheon.utils.openrouter_catalog.get_model_info", lambda model: {
        "input_cost_per_million": 1, "output_cost_per_million": 2,
    })
    return adapter


@pytest.mark.asyncio
async def test_one_call_isolated_and_reports_route_usage(configured):
    result = await pg.Playground().run("test-request-01", "platform", "openrouter/acme/model", "hi", "be brief")
    call = configured.acompletion.call_args.kwargs
    assert call["model"] == "openrouter/acme/model"
    assert call["base_url"] == "https://proxy.example/litellm"
    assert call["api_key"] == "sk-private-key"
    assert call["messages"] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    assert call["tools"] is None and call["num_retries"] == 1
    assert configured.acompletion.await_count == 1
    assert result["success"] and result["output"] == "hello"
    assert result["usage"]["prompt_tokens"] == 10
    assert result["estimated_cost_usd"] == pytest.approx(.000014)
    assert result["first_token_ms"] is not None
    assert "sk-private-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_byok_does_not_inherit_force_proxy_or_change_env(configured, monkeypatch):
    monkeypatch.setenv("LLM_FORCE_PROXY", "true")
    monkeypatch.setenv("PANTHEON_PLATFORM_PROXY_BASE", "https://platform.invalid")
    await pg.Playground().run("test-request-02", "openrouter", "openrouter/acme/model", "hi")
    call = configured.acompletion.call_args.kwargs
    assert call["base_url"] == "https://openrouter.ai/api/v1"
    assert call["api_key"] == "sk-own-key"
    assert call["model"] == "acme/model"
    import os
    assert os.environ["LLM_FORCE_PROXY"] == "true"


@pytest.mark.asyncio
async def test_failed_call_is_not_retried_and_key_is_redacted(configured):
    configured.acompletion.side_effect = RuntimeError("Bad credentials sk-private-key")
    runner = pg.Playground()
    result = await runner.run("test-request-03", "platform", "openrouter/acme/model", "hi")
    assert not result["success"] and "sk-private-key" not in result["message"]
    assert configured.acompletion.await_count == 1
    with pytest.raises(ValueError, match="already"):
        await runner.run("test-request-03", "platform", "openrouter/acme/model", "hi")
    assert not runner.tasks


@pytest.mark.asyncio
async def test_cancel_propagates_to_provider(configured):
    started = asyncio.Event()
    stopped = asyncio.Event()
    async def slow(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    configured.acompletion.side_effect = slow
    runner = pg.Playground()
    task = asyncio.create_task(runner.run("test-request-04", "platform", "openrouter/acme/model", "hi"))
    await asyncio.wait_for(started.wait(), 3)
    assert runner.cancel("test-request-04")["cancelled"]
    assert (await task)["cancelled"]
    assert stopped.is_set() and not runner.tasks


@pytest.mark.asyncio
async def test_early_cancel_never_starts_a_call(configured):
    runner = pg.Playground()
    runner.cancel("test-request-05")
    with pytest.raises(ValueError, match="cancelled"):
        await runner.run("test-request-05", "platform", "openrouter/acme/model", "hi")
    configured.acompletion.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"max_tokens": 0}, {"max_tokens": 32769}, {"temperature": float("nan")},
    {"prompt": " "}, {"model": "normal"}, {"model": "anthropic/claude"}, {"source": "missing"},
])
async def test_invalid_request_never_reaches_provider(configured, kwargs):
    args = dict(request_id="test-request-06", source="platform", model="openrouter/acme/model", prompt="hi")
    args.update(kwargs)
    with pytest.raises(ValueError):
        await pg.Playground().run(**args)
    configured.acompletion.assert_not_called()


def test_public_route_strips_url_credentials_and_query():
    route = pg.Route("own", "Own", "Your account", "openai", "https://user:pass@api.example/v1?api_key=secret", "secret", True)
    public = route.public()
    assert public["endpoint"] == "https://api.example/v1"
    assert "secret" not in json.dumps(public) and "pass" not in json.dumps(public)


def test_routes_do_not_treat_detection_sentinels_as_byok(monkeypatch):
    monkeypatch.setenv("PLATFORM_MODEL_MODE", "openrouter")
    monkeypatch.delenv("PANTHEON_PLATFORM_PROXY_BASE", raising=False)
    monkeypatch.delenv("PANTHEON_PLATFORM_PROXY_KEY", raising=False)
    settings = MagicMock()
    settings.get_api_key.side_effect = lambda key: {
        "OPENAI_API_KEY": "proxy-mode-detection-only", "ANTHROPIC_API_KEY": "real-own-key",
        "LLM_API_BASE": "https://proxy.example", "LLM_API_KEY": "virtual-key",
    }.get(key)
    monkeypatch.setattr("pantheon.settings.get_settings", lambda: settings)
    monkeypatch.setattr("pantheon.utils.oauth.CodexOAuthManager.is_authenticated", lambda self: False)
    monkeypatch.setattr("pantheon.utils.oauth.GeminiCliOAuthManager.is_authenticated", lambda self: False)
    result = pg.routes()
    assert result["platform"].available
    assert not result["openai"].available
    assert result["anthropic"].available
    assert result["anthropic"].base == "https://api.anthropic.com"


@pytest.mark.asyncio
async def test_responses_only_model_uses_one_responses_call(configured, monkeypatch):
    own = pg.Route("openai", "OpenAI BYOK", "Your account", "openai", "https://api.openai.com/v1", "own", True)
    monkeypatch.setattr(pg, "routes", lambda: {"openai": own})
    configured.acompletion_responses = AsyncMock(return_value={
        "content": "response api result", "_metadata": {"_debug_usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    })
    result = await pg.Playground().run("response-request-01", "openai", "openai/gpt-5-pro", "hi", max_tokens=50)
    configured.acompletion.assert_not_called()
    call = configured.acompletion_responses.call_args.kwargs
    assert call["model"] == "gpt-5-pro" and call["max_output_tokens"] == 50
    assert result["output"] == "response api result"
    assert configured.acompletion_responses.await_count == 1


@pytest.mark.asyncio
async def test_catalog_preserves_full_list_unknown_metadata_and_local_endpoint(monkeypatch):
    monkeypatch.setattr(pg.media, "media_catalog", AsyncMock(return_value=([], [])))
    from pantheon.utils import openrouter_catalog as oc, model_selector as ms, provider_registry as registry
    provider_catalog = {"providers": {"openrouter": {"models": {}}, "ollama": {"models": {}},
                                      "openai": {"models": {"known": {"max_input_tokens": 100}}}}}
    monkeypatch.setattr(registry, "load_catalog", lambda: provider_catalog)
    source_routes = {name: pg.Route(name, name, "Account", "openai", "http://model-node:11434/v1", "private", True)
                     for name in ("platform", "openrouter", "ollama", "openai")}
    monkeypatch.setattr(pg, "routes", lambda: source_routes)
    monkeypatch.setattr(oc, "ensure_fresh", AsyncMock())
    def search(query, limit):
        assert limit >= 500
        return [{"model": f"openrouter/acme/model-{n}", "name": f"Model {n}"} for n in range(500)]
    monkeypatch.setattr(oc, "search", search)
    monkeypatch.setattr(oc, "get_model_info", lambda m: {})
    monkeypatch.setattr(oc, "get_model_card", lambda m: {})
    monkeypatch.setattr(oc, "effort_levels", lambda m: None)
    monkeypatch.setattr(oc, "catalog_status", lambda: {"live": False})
    monkeypatch.setattr(ms, "get_saved_models", lambda settings: {"openai": ["custom"]})
    probe = AsyncMock(return_value=(True, ["llama3"]))
    monkeypatch.setattr(ms, "fetch_ollama_status", probe)
    result = await pg.catalog()
    assert len(result["models"]) == 1003
    assert result["models"][0]["input_price"] is None
    assert all(v is None for v in result["models"][0]["capabilities"].values())
    assert {m["model"] for m in result["models"]} >= {"openai/custom", "ollama/llama3"}
    assert "private" not in json.dumps(result)
    probe.assert_awaited_once_with("http://model-node:11434")


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit', [False, True])
async def test_fleet_job_observer_disconnect_is_distinct_from_explicit_cancel(monkeypatch, explicit):
    from contextlib import asynccontextmanager
    from pantheon.models import client as client_module
    session = MagicMock(deployment='mac', route={'transport_policy': 'direct_only'})
    entered = asyncio.Event()
    async def submit(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    session.submit = AsyncMock(side_effect=submit)
    session.cancel = AsyncMock()
    @asynccontextmanager
    async def inference(*args):
        yield session
    monkeypatch.setattr(client_module, 'get_client', lambda: MagicMock(inference=inference))
    playground = pg.Playground()
    request_id = 'durable-job'
    playground.progress[request_id] = {}
    task = asyncio.create_task(playground._complete_fleet_job(request_id, 'fleet-model://mac/ranker', 'q', {'documents': ['a']}))
    playground.tasks[request_id] = task
    await entered.wait()
    assert playground.progress[request_id]['job_ref'] == 'fleet-job://mac/durable-job'
    if explicit:
        playground.cancel(request_id)
    else:
        task.cancel()  # Observer deadline or RPC disconnection, not Cancel.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.submit.await_count == 1
    if explicit:
        session.cancel.assert_awaited_once_with(request_id)
    else:
        session.cancel.assert_not_awaited()
