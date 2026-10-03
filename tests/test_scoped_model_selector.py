"""Model discovery and tag changes must stay inside the App's model authority."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pantheon.settings import Settings
from pantheon.utils.model_scope import ModelCallScope
from pantheon.utils.model_selector import ModelSelector, PLATFORM_PROXY_PROVIDERS


def pair(root, environment=None, **kwargs):
    settings = Settings(root, user_home=root / 'user', isolated_env=True,
                        environment=environment or {})
    scope = ModelCallScope(settings, **kwargs)
    selector = ModelSelector(settings, scope=scope)
    scope.resolve_models = lambda spec: selector.resolve_model(spec or 'normal')
    return scope, selector


@pytest.fixture
def no_ambient(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('An App tried to read ambient model configuration')
    monkeypatch.setattr('pantheon.settings.get_settings', forbidden)
    monkeypatch.setattr('pantheon.utils.oauth.CodexOAuthManager', forbidden)
    monkeypatch.setattr('pantheon.utils.oauth.GeminiCliOAuthManager', forbidden)
    monkeypatch.setattr('pantheon.utils.model_selector.get_ollama_cached_state', forbidden)
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-key')
    monkeypatch.setenv('LLM_FORCE_PROXY', 'true')
    monkeypatch.setenv('PLATFORM_MODEL_MODE', 'openrouter')


def test_discovery_never_borrows_other_apps_or_ambient_credentials(tmp_path, no_ambient):
    _, a = pair(tmp_path / 'a', {'OPENAI_API_KEY': 'key-a'})
    _, b = pair(tmp_path / 'b', {'ANTHROPIC_API_KEY': 'key-b'})
    _, empty = pair(tmp_path / 'empty')
    assert a.get_provider_info()['available_providers'] == ['openai']
    assert b.get_provider_info()['available_providers'] == ['anthropic']
    assert all(m.startswith('openai/') for m in a.resolve_model('normal'))
    assert all(m.startswith('anthropic/') for m in b.resolve_model('normal'))
    assert empty.list_available_models()['models_by_provider'] == {}
    with pytest.raises(ValueError, match='no available model provider'):
        empty.resolve_model('normal')
    with pytest.raises(ValueError, match='image generation'):
        empty.resolve_image_gen_model()
    # An old current-provider hint cannot resurrect a missing credential.
    assert all(m.startswith('anthropic/') for m in b.resolve_model_for_provider('low', 'openai'))


def test_scoped_oauth_logout_and_ollama_updates_are_visible_without_global_reset(tmp_path, no_ambient):
    auth = SimpleNamespace(is_authenticated=Mock(return_value=True))
    catalog = [True, ['local-a']]
    _, oauth = pair(tmp_path / 'oauth', oauth_managers={'codex': auth})
    _, local = pair(tmp_path / 'local', ollama_state=lambda: catalog)
    assert oauth.detect_available_provider() == 'codex'
    assert local.resolve_model('normal') == ['ollama/local-a']
    auth.is_authenticated.return_value = False
    catalog[1] = ['local-b']
    assert oauth.get_provider_info()['detected_provider'] is None
    assert oauth.list_available_models()['available_providers'] == []
    assert local.resolve_model('normal') == ['ollama/local-b']
    catalog[0] = False
    with pytest.raises(ValueError, match='no available model provider'):
        local.resolve_model('normal')


def test_platform_budget_and_catalog_metadata_use_own_settings(tmp_path, no_ambient, monkeypatch):
    budget, selector = pair(tmp_path / 'budget', {
        'LLM_FORCE_PROXY': 'true', 'PLATFORM_MODEL_MODE': 'openrouter',
        'PANTHEON_PLATFORM_PROXY_BASE': 'https://budget.example/v1',
        'PANTHEON_PLATFORM_PROXY_KEY': 'budget-key',
    })
    seen = []
    def metadata(model, *, settings):
        assert settings is budget.settings
        seen.append(model)
        return {'supports_reasoning': True, 'supports_vision': True}
    monkeypatch.setattr('pantheon.utils.provider_registry.get_model_info', metadata)
    result = selector.list_available_models()
    assert set(result['available_providers']) == {*PLATFORM_PROXY_PROVIDERS, 'openrouter'}
    assert result['current_provider'] == 'openrouter'
    assert result['reasoning_models'] and seen
    assert all(m.startswith('openrouter/') for m in selector.resolve_model('normal,vision'))


def test_scope_requires_same_settings(tmp_path):
    a, _ = pair(tmp_path / 'a')
    b, _ = pair(tmp_path / 'b')
    with pytest.raises(ValueError, match='same settings'):
        ModelSelector(a.settings, scope=b)


@pytest.mark.asyncio
async def test_runtime_model_change_resolves_with_target_apps_scope(tmp_path, monkeypatch):
    from test_agent_application import application, TEMPLATE
    from pantheon.factory.bindings import AgentToolBindings
    from pantheon.factory.instances import AgentInstanceBinding
    from pantheon.utils.model_selector import DEFAULT_PROVIDER_MODELS
    class NoTools:
        async def bind(self, intent):
            return AgentInstanceBinding(**intent.identity(), tools=AgentToolBindings())
    app = application(tmp_path / 'app', tmp_path / 'workspace', NoTools())
    scope = app.instance_factory.model_scope
    scope.settings._environment['OPENAI_API_KEY'] = 'app-key'
    scope.resolve_models = ModelSelector(scope.settings, scope=scope).resolve_model
    def forbidden(*args, **kwargs):
        raise AssertionError('runtime model change used the global tag resolver')
    monkeypatch.setattr('pantheon.agent._resolve_model_tag', forbidden)
    from pathlib import Path
    package = Path(app.template_manager.system_templates_dir) / 'teams' / 'default.md'
    original = package.read_bytes()
    try:
        await app.run_setup()
        template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': []}]}
        created = await app.create_chat('Model switch', project_name='Shared', template_obj=template)
        assert created['success'], created
        agent = (await app.get_team_for_chat(created['chat_id'])).team_agents[0]
        result = await app.set_agent_model(created['chat_id'], agent.name, 'low+think:medium')
        assert result['success'], result
        assert agent.models == DEFAULT_PROVIDER_MODELS['openai']['low']
        assert agent.model_params['thinking'] == 'medium'
        assert package.read_bytes() == original
        saved = await app.get_chat_template(created['chat_id'])
        assert saved['template']['agents'][0]['model'] == 'low+think:medium'
    finally:
        await app.cleanup()
