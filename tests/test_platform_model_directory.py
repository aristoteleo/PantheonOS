"""Model settings and metadata must work without Agent state or cross-project env changes."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pantheon.platform.service import PlatformService
from pantheon.settings import Settings


@pytest.fixture
def projects(tmp_path, monkeypatch):
    home, a, b = [tmp_path / name for name in ('home', 'a', 'b')]
    for path in (home, a, b):
        path.mkdir()
    monkeypatch.setenv('HOME', str(home))
    for key in ('OPENAI_API_KEY', 'CUSTOM_OPENAI_API_KEY', 'OPENAI_API_BASE', 'CUSTOM_OPENAI_API_BASE', 'LLM_API_BASE'):
        monkeypatch.delenv(key, raising=False)
    (a / '.env').write_text('OPENAI_API_KEY=alpha-secret\nOPENAI_API_BASE=https://a.test/v1\n')
    (b / '.env').write_text('OPENAI_API_KEY=bravo-secret\nOPENAI_API_BASE=https://b.test/v1\n')
    return a, b


def test_isolated_settings_and_reload_never_mutate_process_environment(projects):
    a, b = projects
    before = dict(os.environ)
    sa, sb = Settings(a, isolated_env=True), Settings(b, isolated_env=True)
    assert sa.get_api_key('OPENAI_API_KEY') == 'alpha-secret'
    assert sb.get_api_key('OPENAI_API_KEY') == 'bravo-secret'
    (a / '.env').write_text('OPENAI_API_KEY=changed-secret\n')
    sa.reload()
    assert sa.get_api_key('OPENAI_API_KEY') == 'changed-secret'
    assert sa.get_api_key('OPENAI_API_BASE') is None
    assert sb.get_api_key('OPENAI_API_KEY') == 'bravo-secret'
    assert dict(os.environ) == before


def test_explicit_environment_precedence_is_preserved(projects, monkeypatch):
    a, _ = projects
    monkeypatch.setenv('OPENAI_API_KEY', 'deployment-key')
    normal = Settings(a, isolated_env=True)
    override = Settings(a, isolated_env=True, env_override=True)
    assert normal.get_api_key('OPENAI_API_KEY') == 'deployment-key'
    assert override.get_api_key('OPENAI_API_KEY') == 'alpha-secret'
    assert os.environ['OPENAI_API_KEY'] == 'deployment-key'


@pytest.mark.parametrize('override', [False, True])
def test_dotenv_expansion_respects_deployment_precedence(projects, monkeypatch, override):
    a, _ = projects
    monkeypatch.setenv('MODEL_HOST', 'deployment.test')
    (a / '.env').write_text('MODEL_HOST=project.test\nOPENAI_API_BASE=https://${MODEL_HOST}/v1\n')
    settings = Settings(a, isolated_env=True, env_override=override)
    expected = 'project.test' if override else 'deployment.test'
    assert settings.get_api_key('OPENAI_API_BASE') == f'https://{expected}/v1'
    assert os.environ['MODEL_HOST'] == 'deployment.test'


def test_slow_settings_io_does_not_block_platform_or_change_write_owner(projects, monkeypatch):
    import threading
    a, b = projects
    started, release = threading.Event(), threading.Event()
    original = Settings.persist_project_value
    def slow_write(settings, key, value):
        started.set()
        assert release.wait(5), 'settings write blocked platform event loop'
        return original(settings, key, value)
    monkeypatch.setattr(Settings, 'persist_project_value', slow_write)
    async def scenario():
        host = PlatformService(workspace_path=a)
        pending = asyncio.create_task(host.saved_models({'openai': ['alpha']}))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert (await asyncio.wait_for(host.platform_info(), .5))['api_version'] == 1
            assert (await host.set_active_project(str(b)))['success']
        finally:
            release.set()
            await pending
            await host.cleanup()
        assert json.loads((a / '.pantheon/settings.json').read_text())['models']['saved_models']['openai'] == ['alpha']
        assert not (b / '.pantheon/settings.json').exists()
    asyncio.run(scenario())


def test_concurrent_settings_writers_preserve_distinct_keys(projects):
    from concurrent.futures import ThreadPoolExecutor
    a, _ = projects
    def write(index):
        Settings(a, isolated_env=True).persist_project_value(f'custom.key{index}', index)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(write, range(24)))
    assert json.loads((a / '.pantheon/settings.json').read_text())['custom'] == {
        f'key{i}': i for i in range(24)
    }


def test_selector_uses_its_given_settings_instead_of_the_global_singleton(projects, monkeypatch):
    from pantheon.utils.model_selector import ModelSelector
    from pantheon.utils import oauth
    a, b = projects
    (b / '.env').write_text('ANTHROPIC_API_KEY=bravo\n')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setattr(oauth.CodexOAuthManager, 'is_authenticated', lambda self: False)
    monkeypatch.setattr(oauth.GeminiCliOAuthManager, 'is_authenticated', lambda self: False)
    monkeypatch.setattr('pantheon.settings.get_settings', lambda: (_ for _ in ()).throw(AssertionError('global settings')))
    assert 'openai' in ModelSelector(Settings(a, isolated_env=True))._get_available_providers()
    providers = ModelSelector(Settings(b, isolated_env=True))._get_available_providers()
    assert 'anthropic' in providers and 'openai' not in providers


def test_status_saved_models_and_discovery_follow_selected_project(projects, monkeypatch):
    from pantheon.utils import model_discovery
    a, b = projects
    probe = AsyncMock(return_value={'success': True, 'models': ['test']})
    monkeypatch.setattr(model_discovery, 'discover_provider_models', probe)
    async def scenario():
        host = PlatformService(workspace_path=a)
        try:
            await host.saved_models({'openai': ['openai/alpha']})
            status = await host.check_api_keys()
            assert status['keys']['OPENAI_API_KEY'] == {'configured': True, 'source': 'env', 'masked': 'alpha-***'}
            assert 'alpha-secret' not in json.dumps(status)
            await host.discover_provider_models('openai')
            probe.assert_awaited_with('openai', 'alpha-secret', 'https://a.test/v1')
            await host.set_active_project(str(b))
            await host.saved_models({'openai': ['openai/bravo']})
            await host.discover_provider_models('openai')
            probe.assert_awaited_with('openai', 'bravo-secret', 'https://b.test/v1')
            await host.set_active_project(str(a))
            assert (await host.saved_models())['saved_models']['openai'] == ['alpha']
            assert json.loads((b / '.pantheon/settings.json').read_text())['models']['saved_models']['openai'] == ['bravo']
            assert 'OPENAI_API_KEY' not in os.environ
        finally:
            await host.cleanup()
    asyncio.run(scenario())


def test_persist_settings_preserves_other_values_and_fails_on_corruption(projects):
    a, _ = projects
    path = a / '.pantheon/settings.json'
    path.parent.mkdir()
    path.write_text('{"theme":"dark", // comment\n "models": {"priority": ["a"]}}')
    settings = Settings(a, isolated_env=True)
    settings.persist_project_value('models.saved_models', {'openai': ['x']})
    data = json.loads(path.read_text())
    assert data['theme'] == 'dark' and data['models']['priority'] == ['a']
    path.write_text('{broken')
    with pytest.raises(ValueError):
        settings.persist_project_value('models.saved_models', {})
    assert path.read_text() == '{broken'


def test_atomic_settings_failure_does_not_destroy_old_file(projects, monkeypatch):
    a, _ = projects
    settings = Settings(a, isolated_env=True)
    path = settings.persist_project_value('theme', 'dark')
    original = path.read_bytes()
    def fail(*args):
        raise OSError('storage failed')
    monkeypatch.setattr('pantheon.settings.os.replace', fail)
    with pytest.raises(OSError):
        settings.persist_project_value('models.saved_models', {})
    assert path.read_bytes() == original
    assert not list(path.parent.glob('*.tmp'))


def test_directory_keeps_platform_and_fleet_models_separate_from_byok(projects, monkeypatch):
    from pantheon.utils import openrouter_catalog as catalog, model_selector as selectors
    from pantheon.models import client
    a, _ = projects
    monkeypatch.setattr(catalog, 'ensure_fresh', AsyncMock())
    monkeypatch.setattr(catalog, 'platform_model_mode', lambda: 'openrouter')
    monkeypatch.setattr(catalog, 'catalog_status', lambda: {'ready': True, 'live': False, 'source': 'disk'})
    monkeypatch.setattr(catalog, 'by_vendor', lambda: {'Vendor': ['openrouter/vendor/model']})
    monkeypatch.setattr(catalog, 'reorder_and_filter', lambda groups: groups)
    monkeypatch.setattr(catalog, 'effort_levels', lambda model: {'levels': ['low', 'high']})
    monkeypatch.setattr(selectors.ModelSelector, 'list_available_models', lambda self: {'success': True, 'models_by_provider': {'openai': ['openai/direct']}})
    monkeypatch.setattr(selectors.ModelSelector, 'resolve_model', lambda self, tag: ['openrouter/vendor/model'])
    monkeypatch.setattr(selectors, 'refresh_ollama_cache', AsyncMock())
    fleet = SimpleNamespace(catalog=AsyncMock(return_value=(
        [{'id': 'local', 'label': 'Local', 'available': True}],
        [{'source': 'local', 'model': 'fleet-model://local/small', 'operations': ['text']}],
    )))
    monkeypatch.setattr(client, 'get_client', lambda: fleet)
    async def scenario():
        host = PlatformService(workspace_path=a)
        try:
            result = await host.list_available_models()
            assert result['platform_catalog_live'] is False
            assert result['platform_catalog_source'] == 'disk'
            assert result['models_by_provider']['openai'] == ['openai/direct']
            assert result['platform_models_by_provider']['Vendor'] == ['openrouter/vendor/model']
            assert result['platform_models_by_provider']['Fleet · Local'] == ['fleet-model://local/small']
            assert result['models_by_provider']['Fleet · Local'] == ['fleet-model://local/small']
            assert result['tag_models']['normal'] == 'openrouter/vendor/model'
            fleet.catalog.side_effect = ConnectionError('offline')
            assert (await host.list_available_models())['platform_models_by_provider']['Vendor']
        finally:
            await host.cleanup()
    asyncio.run(scenario())


def test_model_settings_work_with_agent_imports_forbidden(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = '''
import asyncio, importlib.abc, sys
class NoAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if any(fullname == p or fullname.startswith(p+'.') for p in ('pantheon.agent', 'pantheon.chatroom', 'pantheon.team', 'pantheon.factory', 'pantheon.internal.learning_system', 'pantheon.internal.memory')):
            raise AssertionError('Imported Agent: '+fullname)
sys.meta_path.insert(0, NoAgent())
from pantheon.platform.service import PlatformService
async def run():
    host = PlatformService()
    try:
        assert (await host.saved_models({'openai':['openai/example']}))['success']
        assert (await host.saved_models())['saved_models']['openai'] == ['example']
        assert 'keys' in await host.check_api_keys()
    finally:
        await host.cleanup()
asyncio.run(run())
'''
    env = {k:v for k,v in os.environ.items() if not k.startswith(('PANTHEON_', 'FLEET_', 'NATS_'))}
    env.update(HOME=str(tmp_path), PYTHONPATH=str(root))
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
