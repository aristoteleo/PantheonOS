"""Live model environment is explicit private migration input, not ambient state."""
import json
from pathlib import Path

import pytest

from pantheon.settings import Settings
from pantheon.chatroom.migration import inspect_legacy, fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_handoff import export_model_handoff, environment_fields
from pantheon.chatroom.migration_import import import_backup
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.chatroom.migration_models import ModelSelectionConversion
from pantheon.models.client import model_ref
from pantheon.chatroom.room import ChatRoom
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.platform.service import PlatformService
from test_agent_migration import legacy
from test_agent_migration_environment import source_files, backed_up, plan, restore
from test_agent_migration_credentials import vault, read_key
from test_agent_model_scope import endpoint
from test_model_services import connector_module
from test_agent_launch import prepared, snapshot
from test_model_dependency import model_dependency, model_endpoint, tls_material
from test_agent_release import release, release_process, ready
from test_agent_native_process import request as rpc_request


def capture(spec, tmp_path, environment=None, *, base='https://models.example/v1'):
    _, settings, dotenv = source_files(spec, tmp_path, base)
    value = json.loads(settings.read_text())
    value['api_keys'] = {'OPENAI_API_KEY': 'settings-key', 'OPENAI_API_BASE': base}
    settings.write_text(json.dumps(value))
    dotenv.write_text('OPENAI_API_KEY=file-key\nOPENAI_API_BASE=https://old-file.example/v1\n')
    original = Settings(settings.parent.parent, user_home=Path(spec['global_config']),
                        isolated_env=True, environment=environment or {})
    return original, settings, dotenv


def attach(spec, settings, operation='runtime-handoff'):
    descriptor = export_model_handoff(settings, operation_id=operation)
    spec['model_environment_file'] = descriptor['source']
    return Path(descriptor['source']), descriptor


@pytest.mark.asyncio
async def test_legacy_rpc_captures_actual_environment_without_returning_secrets(legacy, tmp_path, monkeypatch):
    isolated, _, _ = capture(legacy, tmp_path)
    for field in environment_fields(): monkeypatch.delenv(field, raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'runtime-only-key')
    monkeypatch.setenv('OPENAI_API_BASE', 'https://runtime.example/v1')
    settings = Settings(isolated.work_dir, user_home=isolated.user_home)
    room = object.__new__(ChatRoom)
    room._settings = lambda: settings
    result = await room.export_model_migration_handoff('owner-export')
    assert result['success'] and result['requires_writer_fence']
    assert 'runtime-only-key' not in json.dumps(result)
    assert 'runtime.example' not in json.dumps(result)
    source = Path(result['source'])
    assert not source.stat().st_mode & 0o077
    values = json.loads(source.read_text())['values']
    assert values['OPENAI_API_KEY'] == settings.get_api_key('OPENAI_API_KEY') == 'runtime-only-key'
    assert values['OPENAI_API_BASE'] == 'https://runtime.example/v1'
    assert (await room.export_model_migration_handoff('owner-export')) == result
    monkeypatch.setenv('OPENAI_API_KEY', 'changed-after-export')
    failed = await room.export_model_migration_handoff('owner-export')
    assert failed['success'] is False and 'changed-after-export' not in json.dumps(failed)
    assert json.loads(source.read_text())['values'] == values
    assert ChatRoom.export_model_migration_handoff._is_tool
    assert not hasattr(AgentRuntime, 'export_model_migration_handoff')
    assert not hasattr(PlatformService, 'export_model_migration_handoff')


def test_inventory_does_not_read_handoff_and_private_backup_does_not_import_it(legacy, tmp_path, monkeypatch):
    settings, _, _ = capture(legacy, tmp_path, {'OPENAI_API_KEY': 'handoff-private'})
    source, descriptor = attach(legacy, settings)
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path == source: raise AssertionError('Dry run read private handoff')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    inventory = inspect_legacy(**legacy)
    assert inventory['model_environment'] == {'source': str(source), 'exists': True}
    assert not any(item['source'] == str(source) for item in inventory['files'])
    assert 'handoff-private' not in json.dumps(inventory) + json.dumps(descriptor)
    monkeypatch.setattr(Path, 'open', original)
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        manifest = json.loads((Path(backup['directory']) / 'manifest.json').read_text())
        item = next(item for item in manifest['files'] if item['source'] == str(source))
        assert item['target'] is None and item['category'] == 'opaque-configuration'
        with pytest.raises(ValueError, match='environment requires explicit'):
            restore(backup, fence)
        assert not root.exists()


@pytest.mark.parametrize('removed', [None, ''])
def test_absent_live_keys_do_not_resurrect_dotenv_values(legacy, tmp_path, vault, removed):
    settings, settings_path, _ = capture(legacy, tmp_path)
    settings.get_api_key('OPENAI_API_KEY')  # Load dotenv in the source runtime.
    for name in ('OPENAI_API_KEY', 'OPENAI_API_BASE'):
        if removed is None: settings._environment.pop(name)
        else: settings._environment[name] = removed
    source, _ = attach(legacy, settings)
    assert settings.get_api_key('OPENAI_API_KEY') == 'settings-key'
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        conversion = plan(backup, fence, vault, settings_path, 'https://models.example/v1')
        receipt = restore(backup, fence, conversion)
        assert read_key(vault, 'node-secret://dotenv-openai', 'https://models.example/v1') == 'settings-key'
        assert not any(path.name == source.name for path in root.rglob('*'))
        assert 'file-key' not in json.dumps(receipt) and 'settings-key' not in json.dumps(receipt)


@pytest.mark.parametrize('legacy_alias', [False, True])
def test_runtime_key_and_endpoint_reach_original_connector_via_existing_vault(
        legacy, tmp_path, vault, endpoint, monkeypatch, legacy_alias):
    env = {'OPENAI_API_KEY': 'active-runtime-key', 'OPENAI_API_BASE': endpoint.url + '/runtime/v1'}
    settings, _, _ = capture(legacy, tmp_path, env)
    settings.get_api_key('OPENAI_API_KEY')
    if legacy_alias:
        # Explicitly remove canonical fields after dotenv load. Settings' real
        # compatibility order must match the converter, not a guessed overlay.
        for field, value in env.items():
            settings._environment.pop(field)
            settings._environment['CUSTOM_' + field] = value
    source, _ = attach(legacy, settings)
    monkeypatch.setenv('OPENAI_API_KEY', 'migrator-must-not-use')
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        conversion = plan(backup, fence, vault, source, endpoint.url + '/runtime/v1')
        receipt = restore(backup, fence, conversion)
        descriptor = conversion.describe()
        assert 'active-runtime-key' not in json.dumps(receipt) + json.dumps(descriptor)
        binding = descriptor['credentials']['provider']
        assert read_key(vault, binding['ref'], binding['endpoint']) == settings.get_api_key('OPENAI_API_KEY')
        monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(vault.executable))
        monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(vault.state_dir / 'apps/owner/model-credentials'))
        connector = connector_module.Connector(tmp_path / 'connector')
        connector.configure({'engine': 'api', 'endpoint': binding['endpoint'], 'secret_ref': binding['ref']})
        with connector.request('/chat/completions', {'model': 'fixture', 'messages': []}) as response:
            assert b'scoped reply' in response.read()
        path, headers, _ = endpoint.requests[-1]
        assert path == '/runtime/v1/chat/completions' and headers['Authorization'] == 'Bearer active-runtime-key'
        assert 'active-runtime-key' not in connector.path.read_text()
        assert not (root / 'configuration/.env').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('execution', ['source', 'package'])
async def test_handoff_migrates_saved_agent_through_model_service_without_provider_key(
        legacy, tmp_path, vault, endpoint, model_dependency, model_endpoint, monkeypatch, request, execution):
    package = request.getfixturevalue('release') if execution == 'package' else None
    base = endpoint.url + '/live-runtime/v1'
    settings, _, _ = capture(legacy, tmp_path, {'OPENAI_API_KEY': 'runtime-model-key', 'OPENAI_API_BASE': base})
    source, _ = attach(legacy, settings)
    saved_path = Path(legacy['home_memory']) / 'chat-b.json'
    saved_before = saved_path.read_bytes()
    member = json.loads(saved_before)['extra_data']['team_template']['agents'][0]
    reference = model_ref('mac', 'example:8b')
    monkeypatch.setenv('OPENAI_API_KEY', 'unrelated-migrator-key')
    root = tmp_path / 'data/agent'
    with fence_legacy(legacy, operation='handoff-agent', target=root, namespace='process-app') as fence:
        backup = backup_legacy(legacy, fence=fence, directory=tmp_path / 'backup')
        keys = plan(backup, fence, vault, source, base)
        selection = ModelSelectionConversion(backup['directory'], digest=backup['sha256'], fence=fence,
            owner='owner', node_id='node', selections=[
                {'conversation_id': cid, 'config_id': member['id'], 'source': member['model'], 'target': reference}
                for cid in ('chat-a', 'chat-b')],
            fleet_tiers={tier: reference for tier in ('normal', 'high', 'low')})
        receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                                model_credentials=keys, model_selection=selection)
        assert receipt['model_bindings']['credentials'] == {}
        binding = keys.describe()['credentials']['provider']
        monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(vault.executable))
        monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(vault.state_dir / 'apps/owner/model-credentials'))
        connector = model_dependency.connector
        connector.configure({'engine': 'api', 'endpoint': binding['endpoint'], 'secret_ref': binding['ref']})
        model_dependency.deployment.update(engine='api', config_revision=connector.revision)
        config = prepared(tmp_path, endpoint.url)
        config['values']['agent'].update({key: legacy[key] for key in ('projects', 'active_project', 'default_project')})
        config['values']['agent']['models'] = selection.describe()['models']
        config['credentials'].pop('model')
        config['credentials']['model_services'] = model_dependency.credential
        assert 'runtime-model-key' not in json.dumps(config) + json.dumps(receipt) + connector.path.read_text()
        monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
        if package is None:
            app = ConfiguredAgentApplication('agent', data_dir=root, configuration=snapshot(config),
                                             dependency_ca_file=tmp_path / 'cert.pem')
            try:
                await app.run_setup()
                assert app.app_models.resolve('normal') == [reference]
                result = await app.chat(chat_id='chat-b', message=[{'role': 'user', 'content': 'Continue'}])
                assert result['success'], result
            finally:
                await app.cleanup()
        else:
            with release_process(tmp_path, package, config) as (process, address):
                await ready(process, address, tmp_path)
                result = await rpc_request(address, '/rpc', {'method': 'chat', 'args': {
                    'chat_id': 'chat-b', 'message': [{'role': 'user', 'content': 'Continue'}]}})
                assert result['success'] and result['result']['success'], result
                assert (await rpc_request(address, '/_fleet/drain', {}))['safe_to_stop']
            assert process.returncode == 0
            assert 'runtime-model-key' not in (tmp_path / 'release.log').read_text()
        assert len(endpoint.requests) == 1 and not model_endpoint.requests
        path, headers, body = endpoint.requests[0]
        assert path == '/live-runtime/v1/chat/completions'
        assert headers['Authorization'] == 'Bearer runtime-model-key'
        assert body['model'] == 'example:8b'
        assert model_dependency.data_calls == ['/v1/chat/completions']
        assert saved_path.read_bytes() == saved_before
        assert not (root / 'configuration/.env').exists()


@pytest.mark.parametrize('problem', ['project', 'missing-field', 'extra-field', 'bad-value', 'protocol'])
def test_malformed_handoff_never_provisions_or_creates_destination(legacy, tmp_path, vault, problem):
    settings, _, _ = capture(legacy, tmp_path, {'OPENAI_API_KEY': 'private-runtime'})
    source, _ = attach(legacy, settings)
    data = json.loads(source.read_text())
    if problem == 'project': data['project_config'] += '-different'
    elif problem == 'missing-field': data['values'].pop('OPENAI_API_KEY')
    elif problem == 'extra-field': data['values']['UNMAPPED_SECRET'] = 'private-extra'
    elif problem == 'bad-value': data['values']['OPENAI_API_KEY'] = {'value': 'private-runtime'}
    elif problem == 'protocol': data['protocol'] = True
    source.write_text(json.dumps(data))
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        with pytest.raises(ValueError, match='handoff') as error:
            plan(backup, fence, vault, source, 'https://old-file.example/v1')
        assert 'private-' not in str(error.value)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()


@pytest.mark.parametrize('field', ['LLM_API_KEY', 'LLM_FORCE_PROXY', 'PANTHEON_PLATFORM_PROXY_KEY', 'OLLAMA_API_BASE'])
def test_unconverted_runtime_fallback_or_budget_blocks_before_provision(legacy, tmp_path, vault, field):
    settings, _, _ = capture(legacy, tmp_path, {'OPENAI_API_KEY': 'private-runtime', field: 'private-unconverted'})
    source, _ = attach(legacy, settings)
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        conversion = plan(backup, fence, vault, source, 'https://old-file.example/v1')
        with pytest.raises(ValueError, match='explicit conversion') as error:
            restore(backup, fence, conversion)
        assert 'private-' not in str(error.value)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()


def test_handoff_change_after_backup_blocks_import(legacy, tmp_path, vault):
    settings, _, _ = capture(legacy, tmp_path, {'OPENAI_API_KEY': 'private-runtime'})
    source, _ = attach(legacy, settings)
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        conversion = plan(backup, fence, vault, source, 'https://old-file.example/v1')
        source.write_text(source.read_text().replace('private-runtime', 'changed-runtime'))
        with pytest.raises(ValueError, match='changed after backup'):
            restore(backup, fence, conversion)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()


@pytest.mark.parametrize('problem', ['public', 'link', 'in-agent-data', 'in-history'])
def test_handoff_requires_private_regular_location_outside_agent_data(legacy, tmp_path, problem, monkeypatch):
    settings, _, _ = capture(legacy, tmp_path)
    source, _ = attach(legacy, settings)
    if problem == 'public': source.chmod(0o644)
    elif problem == 'link':
        link = tmp_path / 'linked.json'; link.symlink_to(source); legacy['model_environment_file'] = str(link)
    else:
        path = (Path(legacy['home_memory']) / 'private.json' if problem == 'in-history'
                else Path(legacy['project_config']) / 'agents/secret.md')
        source.rename(path); legacy['model_environment_file'] = str(path)
        # Reject before inventory opens or hashes a secret as conversation data.
        import os
        real_open = os.open
        def guarded(name, *args, **kwargs):
            if Path(name) == path: raise AssertionError('Inventoried private handoff bytes')
            return real_open(name, *args, **kwargs)
        monkeypatch.setattr(os, 'open', guarded)
    with pytest.raises(ValueError): inspect_legacy(**legacy)
