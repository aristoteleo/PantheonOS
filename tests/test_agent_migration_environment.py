"""A legacy launch dotenv must be backed up, accounted for and kept private."""
from contextlib import contextmanager
import json
from pathlib import Path

import pytest

from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.chatroom.migration import inspect_legacy, fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy, verify_backup
from pantheon.chatroom.migration_credentials import ModelCredentialConversion
from pantheon.chatroom.migration_import import import_backup
from pantheon.settings import Settings
from test_agent_migration import legacy
from test_agent_migration_credentials import vault, read_key
from test_agent_application import TEMPLATE
from test_agent_launch import prepared, snapshot
from test_agent_model_scope import endpoint
from test_model_dependency import model_dependency, model_endpoint, tls_material
from test_model_services import connector_module


def source_files(spec, tmp_path, base):
    config = prepared(tmp_path, base)
    config['values']['agent'].update({key: spec[key] for key in ('projects', 'active_project', 'default_project')})
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': [], 'model': 'openai/fixture'}]}
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(spec['home_memory']) / name
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = template
        path.write_text(json.dumps(value))
    settings = Path(spec['project_config']) / 'settings.json'
    settings.write_text(json.dumps(config['values']['agent']['settings']))
    return config, settings, settings.parent.parent / '.env'


@contextmanager
def backed_up(spec, tmp_path):
    root = tmp_path / 'data'
    with fence_legacy(spec, operation='dotenv-migration', target=root, namespace='process-app') as fence:
        backup = backup_legacy(spec, fence=fence, directory=tmp_path / 'backup')
        yield root, fence, backup


def plan(backup, fence, vault, source, endpoint, provider='openai'):
    return ModelCredentialConversion(backup['directory'], digest=backup['sha256'], fence=fence, vault=vault,
        bindings=[{'provider': provider, 'source': str(source), 'endpoint': endpoint,
                   'alias': 'provider', 'ref': 'node-secret://dotenv-openai'}])


def restore(backup, fence, conversion=None):
    return import_backup(backup['directory'], digest=backup['sha256'], fence=fence, model_credentials=conversion)


def test_external_dotenv_inventory_never_reads_secret_and_backup_retains_it(legacy, tmp_path, monkeypatch):
    _, _, env = source_files(legacy, tmp_path, 'https://unused.example')
    raw = b'OPENAI_API_KEY=private-env-value\n'
    env.write_bytes(raw)
    original = Path.open
    def no_read(path, *args, **kwargs):
        if path == env:
            raise AssertionError('Dry run read the secret file')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', no_read)
    report = inspect_legacy(**legacy)
    assert report['environment'] == {'source': str(env), 'exists': True}
    assert 'private-env-value' not in json.dumps(report)
    assert not any(item['source'] == str(env) for item in report['files'])
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        folder = Path(backup['directory'])
        manifest = json.loads((folder / 'manifest.json').read_text())
        item = next(item for item in manifest['files'] if item['source'] == str(env))
        blob = folder / item['blob']
        assert item['target'] is None and item['category'] == 'opaque-configuration'
        assert blob.read_bytes() == raw and not blob.stat().st_mode & 0o077
        assert verify_backup(folder, digest=backup['sha256']) == backup
        with pytest.raises(ValueError, match='environment requires explicit'):
            restore(backup, fence)
        assert not root.exists()


@pytest.mark.asyncio
async def test_dotenv_migrates_real_agent_and_original_connector_without_copying_secrets(
        legacy, tmp_path, endpoint, vault, model_dependency, model_endpoint, monkeypatch):
    config, settings, env = source_files(legacy, tmp_path, endpoint.url)
    user = Path(legacy['global_config']) / 'settings.json'
    user.write_text(json.dumps({'api_keys': {'OPENAI_API_KEY': 'user-key', 'OPENAI_API_BASE': 'https://user.example/v1'}}))
    values = json.loads(settings.read_text())
    values.update(env_file='.env', api_keys={'OPENAI_API_KEY': 'project-key', 'OPENAI_API_BASE': 'https://project.example/v1'})
    settings.write_text(json.dumps(values))
    env.write_text(f'# Selected launch file\nexport OPENAI_API_KEY="dotenv-key"\nOPENAI_API_BASE={endpoint.url}/dotenv/v1\n')
    # Compare the original Settings implementation with the converter. Exclude
    # ambient process overrides explicitly; their handoff is a separate step.
    original = Settings(settings.parent.parent, user_home=Path(legacy['global_config']), isolated_env=True, environment={})
    assert original.get_api_key('OPENAI_API_KEY') == 'dotenv-key'
    assert original.get_api_key('OPENAI_API_BASE') == endpoint.url + '/dotenv/v1'
    monkeypatch.setenv('OPENAI_API_KEY', 'unrelated-process-key')
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        conversion = plan(backup, fence, vault, env, endpoint.url + '/dotenv/v1')
        receipt = restore(backup, fence, conversion)
        descriptor = conversion.describe()
        for secret in ('user-key', 'project-key', 'dotenv-key', 'unrelated-process-key'):
            assert secret not in json.dumps(receipt) + json.dumps(descriptor)
        assert not (root / 'configuration/.env').exists()
        assert 'api_keys' not in (root / 'configuration/.pantheon/settings.json').read_text()
        assert 'env_file' not in (root / 'configuration/.pantheon/settings.json').read_text()
        binding = descriptor['credentials']['provider']
        key = read_key(vault, binding['ref'], binding['endpoint'])
        assert key == original.get_api_key('OPENAI_API_KEY')
        config['values']['agent']['models'] = descriptor['models']
        config['credentials'].pop('model')
        config['credentials'].update(provider={'endpoint': binding['endpoint'], 'key': key},
                                     model_services=model_dependency.credential)
        app = ConfiguredAgentApplication('agent', data_dir=root, configuration=snapshot(config),
                                        dependency_ca_file=tmp_path / 'cert.pem')
        try:
            await app.run_setup()
            assert (await app.chat(chat_id='chat-b', message=[{'role': 'user', 'content': 'Continue'}]))['success']
        finally:
            await app.cleanup()
        assert endpoint.requests and not model_endpoint.requests
        assert all(path.startswith('/dotenv/v1/') and headers['Authorization'] == 'Bearer dotenv-key'
                   for path, headers, _ in endpoint.requests)
        # The original Connector reads the same vault entry, without another
        # credential store or putting a key into its persisted configuration.
        monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(vault.executable))
        monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(vault.state_dir / 'apps/owner/model-credentials'))
        connector = connector_module.Connector(tmp_path / 'connector')
        connector.configure({'engine': 'api', 'endpoint': binding['endpoint'], 'secret_ref': binding['ref']})
        with connector.request('/chat/completions', {'model': 'fixture', 'messages': []}) as response:
            assert b'scoped reply' in response.read()
        assert endpoint.requests[-1][0] == '/dotenv/v1/chat/completions'
        assert endpoint.requests[-1][1]['Authorization'] == 'Bearer dotenv-key'
        assert 'dotenv-key' not in connector.path.read_text()


@pytest.mark.parametrize('env_value', ['', None, 'override'])
def test_layered_keys_keep_settings_precedence_and_require_effective_source(legacy, tmp_path, vault, env_value):
    _, settings, env = source_files(legacy, tmp_path, 'https://unused.example')
    user = Path(legacy['global_config']) / 'settings.json'
    user.write_text(json.dumps({'api_keys': {'OPENAI_API_KEY': 'user-key', 'OPENAI_API_BASE': 'https://models.example/v1'}}))
    values = json.loads(settings.read_text()); values['api_keys'] = {'OPENAI_API_KEY': 'project-key'}
    settings.write_text(json.dumps(values))
    if env_value is not None:
        env.write_text('OPENAI_API_KEY=' + env_value + '\n')
    expected_source = env if env_value else settings
    original = Settings(settings.parent.parent, user_home=user.parent, isolated_env=True, environment={})
    with backed_up(legacy, tmp_path) as (_, fence, backup):
        with pytest.raises(ValueError, match='does not match'):
            plan(backup, fence, vault, user, 'https://models.example/v1')
        conversion = plan(backup, fence, vault, expected_source, 'https://models.example/v1')
        restore(backup, fence, conversion)
        assert read_key(vault, 'node-secret://dotenv-openai', 'https://models.example/v1') == original.get_api_key('OPENAI_API_KEY')


@pytest.mark.parametrize('kind', ['custom', 'absent', 'empty', 'user-relative'])
def test_declared_env_file_is_resolved_from_launch_directory(legacy, tmp_path, kind):
    _, settings, env = source_files(legacy, tmp_path, 'https://unused.example')
    document = settings
    if kind in ('custom', 'user-relative'):
        env = settings.parent.parent / 'chosen.env'
        legacy['environment_file'] = str(env)
    if kind == 'user-relative':
        document = Path(legacy['global_config']) / 'settings.json'
    value = json.loads(document.read_text()) if document.exists() else {}
    value['env_file'] = env.name
    document.write_text(json.dumps(value))
    if kind != 'absent':
        env.write_text('# No runtime settings\n\n')
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        restore(backup, fence)
        assert (root / 'migration.json').exists()
        assert not (root / 'configuration/.env').exists()


@pytest.mark.parametrize('kind', ['created', 'modified', 'removed'])
def test_environment_change_after_backup_prevents_import(legacy, tmp_path, kind):
    _, _, env = source_files(legacy, tmp_path, 'https://unused.example')
    if kind != 'created': env.write_text('# old\n')
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        if kind == 'removed': env.unlink()
        else: env.write_text('# new\n')
        with pytest.raises(ValueError): restore(backup, fence)
        assert not root.exists()


@pytest.mark.parametrize('contents', [
    'OPENAI_API_KEY="secret-not-closed\n',
    'OPENAI_API_KEY=${BORROWED_SECRET}\n',
    'OPENAI_API_KEY=dotenv-key\nLLM_FORCE_PROXY=true\nPANTHEON_PLATFORM_PROXY_KEY=budget-secret\n',
    'OPENAI_API_KEY=dotenv-key\nSCRAPER_API_KEY=other-secret\n',
    'OPENAI_API_KEY=dotenv-key\nLLM_API_BASE=https://proxy.example/v1\n',
    'OPENAI_API_KEY=dotenv-key\nUNMAPPED_FLAG=\n',
])
def test_unsupported_environment_does_not_leak_or_provision(legacy, tmp_path, vault, monkeypatch, contents):
    _, _, env = source_files(legacy, tmp_path, 'https://unused.example')
    env.write_text(contents)
    monkeypatch.setenv('BORROWED_SECRET', 'should-not-use')
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        with pytest.raises(ValueError) as error:
            conversion = plan(backup, fence, vault, env, 'https://models.example/v1')
            restore(backup, fence, conversion)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()
        assert not any(secret in str(error.value) for secret in ('budget-secret', 'other-secret', 'secret-not-closed', 'should-not-use'))


def test_custom_env_file_requires_matching_inventory_and_default_interpolation_is_private(legacy, tmp_path, vault, monkeypatch):
    _, settings, env = source_files(legacy, tmp_path, 'https://unused.example')
    values = json.loads(settings.read_text()); values['env_file'] = 'private.env'
    settings.write_text(json.dumps(values))
    chosen = env.with_name('private.env')
    chosen.write_text('OPENAI_API_KEY=${SECRET:-explicit-default}\n')
    monkeypatch.setenv('SECRET', 'ambient-secret')
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        with pytest.raises(ValueError, match='env_file explicitly'): restore(backup, fence)
        assert not root.exists()
        fence.release_sources()
    legacy['environment_file'] = str(chosen)
    second = tmp_path / 'second'; second.mkdir()
    with backed_up(legacy, second) as (_, fence, backup):
        conversion = plan(backup, fence, vault, chosen, 'https://models.example/v1')
        restore(backup, fence, conversion)
        assert read_key(vault, 'node-secret://dotenv-openai', 'https://models.example/v1') == 'explicit-default'


@pytest.mark.asyncio
@pytest.mark.parametrize('provider,model', [('openai', 'openai/fixture'), ('anthropic', 'anthropic/claude-sonnet-4-6')])
async def test_migrated_root_api_keeps_native_protocol_path(
        legacy, tmp_path, endpoint, vault, model_dependency, model_endpoint, provider, model):
    config, _, env = source_files(legacy, tmp_path, endpoint.url)
    env.write_text(f'{provider.upper()}_API_KEY=native-key\n{provider.upper()}_API_BASE={endpoint.url}\n')
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(legacy['home_memory']) / name
        value = json.loads(path.read_text())
        value['extra_data']['team_template']['agents'][0]['model'] = model
        path.write_text(json.dumps(value))
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        conversion = plan(backup, fence, vault, env, endpoint.url, provider)
        restore(backup, fence, conversion)
        descriptor = conversion.describe()
        binding = descriptor['credentials']['provider']
        assert binding['endpoint'] == endpoint.url
        config['values']['agent']['models'] = descriptor['models']
        config['credentials'].pop('model')
        config['credentials'].update(provider={'endpoint': binding['endpoint'],
            'key': read_key(vault, binding['ref'], binding['endpoint'])}, model_services=model_dependency.credential)
        app = ConfiguredAgentApplication('agent', data_dir=root, configuration=snapshot(config),
                                        dependency_ca_file=tmp_path / 'cert.pem')
        try:
            await app.run_setup()
            assert (await app.chat(chat_id='chat-b', message=[{'role': 'user', 'content': 'Continue'}]))['success']
        finally:
            await app.cleanup()
        assert endpoint.requests and not model_endpoint.requests
        for path, headers, body in endpoint.requests:
            if provider == 'anthropic':
                assert path == '/v1/messages'
                assert {key.lower(): value for key, value in headers.items()}['x-api-key'] == 'native-key'
                assert not headers.get('Authorization')
            else:
                assert path in ('/responses', '/chat/completions')
                assert headers['Authorization'] == 'Bearer native-key'
