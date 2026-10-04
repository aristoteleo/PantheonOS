"""Global proxy migration stays provider-side; Agent consumes Model Services."""
import json
from pathlib import Path

import pytest

from pantheon.chatroom.migration_credentials import ModelCredentialConversion
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_import import import_backup
from pantheon.chatroom.migration_models import ModelSelectionConversion
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.models.client import model_ref
from pantheon.settings import Settings
from pantheon.utils.llm_providers import get_openai_effective_config, get_provider_api_key, resolve_provider_base_url
from test_agent_migration import legacy
from test_agent_migration_credentials import vault, read_key
from test_agent_migration_environment import source_files, backed_up
from test_agent_migration_handoff import attach
from test_agent_migration_budget import endpoint, choice
from test_agent_launch import prepared, snapshot
from test_model_dependency import model_dependency, model_endpoint, tls_material
from test_model_services import connector_module
from test_agent_release import release, release_process, ready
from test_agent_native_process import request as rpc_request


def source(spec, tmp_path, values, *, location='runtime'):
    _, path, dotenv = source_files(spec, tmp_path, 'https://unused.example')
    original = Settings(path.parent.parent, user_home=Path(spec['global_config']),
                        isolated_env=True, environment=values if location == 'runtime' else {})
    if location == 'settings':
        config = json.loads(path.read_text()); config['api_keys'] = values
        path.write_text(json.dumps(config))
        return original, path
    if location == 'dotenv':
        dotenv.write_text(''.join(f'{name}={value}\n' for name, value in values.items()))
        return original, dotenv
    handoff, _ = attach(spec, original)
    return original, handoff


def fallback(origin, endpoint):
    return {'credential': dict(source=str(origin), alias='global-proxy',
        endpoint=endpoint, ref='node-secret://global-proxy')}


def binding(origin, endpoint, provider='openai'):
    return dict(source=str(origin), provider=provider, alias=provider,
                endpoint=endpoint, ref='node-secret://' + provider)


def convert(backup, fence, vault, bindings, global_fallback, **kwargs):
    return ModelCredentialConversion(backup['directory'], digest=backup['sha256'], fence=fence,
                                    vault=vault, bindings=bindings, global_fallback=global_fallback, **kwargs)


def selection(spec, backup, fence, **kwargs):
    member = json.loads((Path(spec['home_memory']) / 'chat-b.json').read_text())['extra_data']['team_template']['agents'][0]
    reference = model_ref('mac', 'example:8b')
    return ModelSelectionConversion(backup['directory'], digest=backup['sha256'], fence=fence,
        owner='owner', node_id='node', selections=[dict(conversation_id=cid, config_id=member['id'],
            source=member['model'], target=reference) for cid in ('chat-a', 'chat-b')],
        fleet_tiers={tier: reference for tier in ('normal', 'high', 'low')}, **kwargs)


def restore(spec, backup, fence, conversion):
    selected = selection(spec, backup, fence)
    receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                            model_credentials=conversion, model_selection=selected)
    assert receipt['model_bindings']['credentials'] == {}
    return receipt, selected


@pytest.mark.parametrize('location', ['runtime', 'settings', 'dotenv'])
@pytest.mark.parametrize('override', ['none', 'key', 'base', 'both', 'alias', 'sentinel'])
def test_fieldwise_precedence_matches_legacy_and_keeps_all_keys_in_vault(
        legacy, tmp_path, vault, location, override, monkeypatch):
    base = 'https://proxy.example/v1'
    values = dict(LLM_API_BASE=base, LLM_API_KEY='fallback-private')
    if override in ('key', 'both'): values['OPENAI_API_KEY'] = 'provider-private'
    if override in ('base', 'both'): values['OPENAI_API_BASE'] = 'https://provider.example/v1'
    if override == 'alias': values['CUSTOM_OPENAI_API_KEY'] = 'alias-private'
    if override == 'sentinel': values['OPENAI_API_KEY'] = 'proxy-mode-detection'
    original, origin = source(legacy, tmp_path, values, location=location)
    expected_base, expected_key = get_openai_effective_config(settings=original)
    monkeypatch.setenv('LLM_API_KEY', 'unrelated-process-private')
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        plan = convert(backup, fence, vault, [binding(origin, expected_base)], fallback(origin, base))
        assert not root.exists() and not (vault.state_dir / 'apps').exists()
        receipt, _ = restore(legacy, backup, fence, plan)
        assert read_key(vault, 'node-secret://openai', expected_base) == expected_key
        assert read_key(vault, 'node-secret://global-proxy', base) == 'fallback-private'
        serialized = json.dumps(receipt)
        for key in ('fallback-private', 'provider-private', 'alias-private', 'unrelated-process-private', 'proxy-mode-detection'):
            assert key not in serialized
        assert not (root / 'configuration/.env').exists()
        assert 'api_keys' not in (root / 'configuration/.pantheon/settings.json').read_text()


@pytest.mark.parametrize('own_key,openai_key', [(True, True), (False, True), (False, False)])
def test_non_openai_legacy_key_fallback_and_independent_base(legacy, tmp_path, vault, own_key, openai_key):
    values = dict(LLM_API_BASE='https://proxy.example/v1', LLM_API_KEY='global-private',
                  ANTHROPIC_API_BASE='https://vendor.example/v1', ANTHROPIC_API_KEY='proxy-mode-detection')
    if own_key: values['ANTHROPIC_API_KEY'] = 'anthropic-private'
    if openai_key: values['OPENAI_API_KEY'] = 'openai-private'
    original, origin = source(legacy, tmp_path, values)
    expected_base = resolve_provider_base_url('anthropic', settings=original)
    expected_key = get_provider_api_key('anthropic', settings=original) or get_openai_effective_config(settings=original)[1]
    bindings = [binding(origin, expected_base, 'anthropic')]
    if openai_key: bindings.append(binding(origin, values['LLM_API_BASE']))
    with backed_up(legacy, tmp_path) as (_, fence, backup):
        plan = convert(backup, fence, vault, bindings, fallback(origin, values['LLM_API_BASE']))
        restore(legacy, backup, fence, plan)
        assert read_key(vault, 'node-secret://anthropic', expected_base) == expected_key


@pytest.mark.parametrize('partial', ['base-only', 'key-only'])
def test_partial_fallback_does_not_invent_missing_credential_or_endpoint(legacy, tmp_path, vault, partial):
    base = 'https://proxy.example/v1'
    values = (dict(LLM_API_BASE=base, OPENAI_API_KEY='provider-private') if partial == 'base-only'
              else dict(LLM_API_KEY='fallback-private', OPENAI_API_BASE=base))
    _, origin = source(legacy, tmp_path, values)
    paired = {'credential': None} if partial == 'base-only' else fallback(origin, base)
    with backed_up(legacy, tmp_path) as (_, fence, backup):
        plan = convert(backup, fence, vault, [binding(origin, base)], paired)
        receipt, _ = restore(legacy, backup, fence, plan)
        stored = receipt['model_bindings']['provisioning']['global_fallback']
        assert stored['base'] == (base if partial == 'base-only' else None)
        assert (stored['credential'] is None) == (partial == 'base-only')
        assert read_key(vault, 'node-secret://openai', base) == ('provider-private' if partial == 'base-only' else 'fallback-private')


@pytest.mark.parametrize('change', ['wrong-source', 'wrong-base', 'wrong-provider-base', 'duplicate-ref', 'duplicate-alias',
                                   'missing-key-binding', 'extra', 'bad-port', 'bad-key'])
def test_invalid_pairing_has_no_side_effects_or_secret_errors(legacy, tmp_path, vault, change):
    base = 'https://proxy.example/v1'
    values = dict(LLM_API_BASE=base, LLM_API_KEY='fallback-private')
    if change == 'bad-key': values['LLM_API_KEY'] = ['private-invalid']
    _, origin = source(legacy, tmp_path, values, location='settings')
    paired, bindings = fallback(origin, base), [binding(origin, base)]
    if change == 'wrong-source': paired['credential']['source'] = '/not-the-source'
    elif change == 'wrong-base': paired['credential']['endpoint'] = 'https://other.example/v1'
    elif change == 'wrong-provider-base': bindings[0]['endpoint'] = 'https://other.example/v1'
    elif change == 'duplicate-ref': bindings[0]['ref'] = paired['credential']['ref']
    elif change == 'duplicate-alias': bindings[0]['alias'] = paired['credential']['alias']
    elif change == 'missing-key-binding': paired['credential'] = None
    elif change == 'extra': paired['unreviewed'] = True
    elif change == 'bad-port': paired['credential']['endpoint'] = 'https://proxy.example:private-invalid/v1'
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        with pytest.raises(ValueError) as error: convert(backup, fence, vault, bindings, paired)
        assert 'private-invalid' not in str(error.value) and 'fallback-private' not in str(error.value)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()


def test_global_conversion_cannot_deliver_ambient_fallback_to_agent(legacy, tmp_path, vault):
    base = 'https://proxy.example/v1'
    _, origin = source(legacy, tmp_path, dict(LLM_API_BASE=base, LLM_API_KEY='fallback-private'))
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        plan = convert(backup, fence, vault, [], fallback(origin, base))
        with pytest.raises(ValueError, match='explicit Model Service'):
            import_backup(backup['directory'], digest=backup['sha256'], fence=fence, model_credentials=plan)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()


def test_effective_key_source_can_differ_from_base_source(legacy, tmp_path, vault):
    base = 'https://runtime.example/v1'
    _, path, dotenv = source_files(legacy, tmp_path, base)
    values = json.loads(path.read_text())
    values['api_keys'] = dict(LLM_API_KEY='settings-private', LLM_API_BASE='https://old.example/v1')
    path.write_text(json.dumps(values))
    dotenv.write_text('LLM_API_KEY=stale-file-private\n')
    original = Settings(path.parent.parent, user_home=Path(legacy['global_config']), isolated_env=True,
                        environment={'LLM_API_BASE': base})
    original.get_api_key('LLM_API_KEY')
    original._environment.pop('LLM_API_KEY')  # Old runtime has removed its dotenv override.
    attach(legacy, original)
    with backed_up(legacy, tmp_path) as (_, fence, backup):
        plan = convert(backup, fence, vault, [binding(path, base)], fallback(path, base))
        restore(legacy, backup, fence, plan)
        assert read_key(vault, 'node-secret://global-proxy', base) == 'settings-private'


@pytest.mark.asyncio
@pytest.mark.parametrize('execution', ['source', 'package'])
@pytest.mark.parametrize('budget_enabled', [False, True])
async def test_global_proxy_saved_conversation_runs_through_original_model_service(
        legacy, tmp_path, vault, endpoint, model_dependency, model_endpoint, monkeypatch, request, execution, budget_enabled):
    package = request.getfixturevalue('release') if execution == 'package' else None
    base = endpoint.url + '/global/v1'
    environment = dict(LLM_API_BASE=base, LLM_API_KEY='fallback-private')
    connector = dict(engine='api', endpoint=base, secret_ref='node-secret://global-proxy')
    budget = None
    if budget_enabled:
        environment.update(LLM_FORCE_PROXY='true', PLATFORM_MODEL_MODE='direct',
            PANTHEON_PLATFORM_PROXY_BASE=endpoint.url + '/budget', PANTHEON_PLATFORM_PROXY_KEY='budget-private')
        connector = dict(engine='api', endpoint=endpoint.url + '/budget/v1', secret_ref='node-secret://budget')
        budget = dict(choice=choice(), provisioned=dict(protocol=1, owner='owner', node_id='node',
            source='platform-budget', model_mode='direct', connector=connector))
    _, origin = source(legacy, tmp_path, environment)
    model_dependency.connector.configure(connector)
    model_dependency.deployment.update(engine='api', node_id='node', config_revision=model_dependency.connector.revision)
    model_dependency.deployment['binding']['node_id'] = 'node'
    model_dependency.control.policies['agent']['deployments']['mac']['node_id'] = 'node'
    root = tmp_path / 'data/agent'
    with fence_legacy(legacy, operation='fallback-agent', target=root, namespace='process-app') as fence:
        backup = backup_legacy(legacy, fence=fence, directory=tmp_path / 'backup')
        plan = convert(backup, fence, vault, [], fallback(origin, base), platform_budget=budget)
        selected = selection(legacy, backup, fence,
            **({'budget_choice': choice(), 'source_service_id': choice()['service_id']} if budget_enabled else {}))
        if budget_enabled:
            await selected.review_budget(model_dependency.control.client, budget['provisioned'])
        receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                                model_credentials=plan, model_selection=selected)
        assert receipt['model_bindings']['credentials'] == {}
        assert read_key(vault, 'node-secret://global-proxy', base) == 'fallback-private'
        config = prepared(tmp_path, endpoint.url)
        config['values']['agent'].update({k: legacy[k] for k in ('projects', 'active_project', 'default_project')})
        config['values']['agent']['models'] = selected.describe()['models']
        config['credentials'].pop('model')
        config['credentials']['model_services'] = model_dependency.credential
        monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(vault.executable))
        monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(vault.state_dir / 'apps/owner/model-credentials'))
        monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
        if package is None:
            app = ConfiguredAgentApplication('agent', data_dir=root, configuration=snapshot(config),
                                             dependency_ca_file=tmp_path / 'cert.pem')
            try:
                await app.run_setup()
                assert (await app.chat(chat_id='chat-b', message=[{'role': 'user', 'content': 'Continue'}]))['success']
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
            assert 'fallback-private' not in (tmp_path / 'release.log').read_text()
        assert len(endpoint.requests) == 1 and not model_endpoint.requests
        path, headers, body = endpoint.requests[0]
        assert path == ('/budget/v1/chat/completions' if budget_enabled else '/global/v1/chat/completions')
        assert headers['Authorization'] == 'Bearer ' + ('budget-private' if budget_enabled else 'fallback-private')
        assert body['model'] == 'example:8b'
        assert 'fallback-private' not in json.dumps(receipt) + json.dumps(config) + model_dependency.connector.path.read_text()
        assert 'budget-private' not in json.dumps(receipt) + json.dumps(config) + model_dependency.connector.path.read_text()
