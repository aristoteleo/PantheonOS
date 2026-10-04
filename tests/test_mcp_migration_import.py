"""Fenced MCP import, startup admission and actual migrated stdio tool calls.

The allocator/gateway transport is a deterministic in-process fixture. Captured
configuration, backup/import, configured Agent and MCP child are production code.
"""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

import pytest

from pantheon.apps.builtin.mcp.scoped import ScopedMCP
from pantheon.apps.dependency_client import DependencyClient
from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.chatroom.migration_import import import_backup
from pantheon.chatroom.migration_mcp_configuration import MCPConfigurationConversion
from pantheon.chatroom.migration_models import ModelSelectionConversion
from pantheon.models.client import model_ref
from test_agent_migration import legacy
from test_agent_migration_backup import user_tree
from test_mcp_configuration_migration import captured, backup
from test_mcp_environment_credentials import local_vault
from test_scoped_mcp_app import config
from test_agent_launch import prepared, snapshot
from test_agent_instance_factory import RECIPE
from test_agent_model_scope import endpoint as byok_endpoint
from test_model_dependency import model_dependency, model_endpoint, tls_material


@pytest.fixture
async def migration(legacy, captured, tmp_path, request):
    _, before, script = captured
    problem = getattr(request, 'param', None)
    recipe = {**RECIPE, 'toolsets': ['mcp:docs'], 'mcp_servers': ['mcp']}
    if problem == 'missing-member-provider':
        recipe.update(toolsets=[], mcp_servers=['missing'])
    memory = Path(legacy['home_memory'])
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = memory / name
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = {
            'id': 'existing-team', 'name': 'Saved team', 'agents': [{'id': 'original-member', **recipe}]}
        path.write_text(json.dumps(value))
    template = Path(legacy['project_config'])/'agents/mcp.md'
    template.write_text('---\nid: mcp-agent\nname: MCP Agent\nmodel: openai/fixture\n'
                        'toolsets: [mcp:docs]\nmcp_servers: [' + ('missing' if problem == 'missing-template-provider' else 'mcp') + ']\n'
                        '---\nUse mcp:docs as instructed.\n')
    fence, saved = backup(legacy, tmp_path)
    try:
        vault = local_vault(tmp_path)
        conversion = MCPConfigurationConversion(saved['directory'], digest=saved['sha256'], fence=fence,
            vault=vault, targets={'docs': {'command': [sys.executable, str(script)], 'cwd': str(script.parent)}},
            environments={'docs': {'literals': ['MODE'], 'credentials': {}}})
        candidate = conversion.prepare_deployment(tmp_path/'mcp-app', 'linux-amd64', name='mcp-provider',
            target={'node_id': vault.node_id, 'scope': 'migrated-mcp', 'generation': 0}, aliases={'mcp': 'mcp-shared'})
        provider = {'node_id': vault.node_id, 'revision': candidate['artifact']['revision'], 'generation': 2,
            'instance_id': sha256('\0'.join((vault.owner, vault.node_id, candidate['artifact']['revision'],
                                             'migrated-mcp')).encode()).hexdigest()[:32],
            'component': 'backend', 'port': 'http'}
        admission = conversion.prepare_import(candidate, provider=provider, agent_node_id='agent-node')
        yield dict(spec=legacy, fence=fence, saved=saved, conversion=conversion, candidate=candidate,
                   provider=provider, admission=admission, before=before, recipe=recipe,
                   root=tmp_path/'agent-target', template=template, vault=vault, script=script)
    finally:
        fence.close()


def restore(m, **kwargs):
    return import_backup(m['saved']['directory'], digest=m['saved']['sha256'], fence=m['fence'],
                         mcp_configuration=m['admission'], **kwargs)


def launch_bindings(m):
    pinned = m['admission'].describe()
    return {**pinned, 'profiles': {'toolsets': {}, 'mcp_servers': pinned['profiles']}}


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'MODE': 'read'}], indirect=True)
@pytest.mark.parametrize('with_models', [False, True], ids=['legacy-model', 'model-services'])
async def test_migrated_configured_agent_reopens_history_and_calls_pinned_mcp(
        migration, byok_endpoint, model_dependency, model_endpoint, tmp_path, monkeypatch, with_models):
    m = migration
    original = user_tree(Path(m['spec']['project_config']))
    selection = None
    if with_models:
        reference = model_ref('mac', 'example:8b')
        selection = ModelSelectionConversion(m['saved']['directory'], digest=m['saved']['sha256'], fence=m['fence'],
            owner=m['admission'].describe()['owner'], node_id='agent-node',
            selections=[{'conversation_id': cid, 'config_id': 'original-member', 'source': RECIPE['model'],
                         'target': reference} for cid in ('chat-a', 'chat-b')],
            templates=[{'path': str(m['template']), 'config_id': 'mcp-agent', 'source': 'openai/fixture', 'target': reference}],
            fleet_tiers={tier: reference for tier in ('normal', 'high', 'low')})
    receipt = restore(m, model_selection=selection)
    assert restore(m, model_selection=selection) == receipt
    assert receipt['mcp_bindings'] == m['admission'].describe()
    assert user_tree(Path(m['spec']['project_config'])) == original
    assert not (m['root']/'configuration/.pantheon/mcp.json').exists()
    template = (m['root']/'configuration/.pantheon/agents/mcp.md').read_text()
    assert 'toolsets: [mcp:docs]' in template and 'Use mcp:docs as instructed.' in template
    if not with_models:
        assert template == m['template'].read_text()
    cfg = prepared(m['root'], byok_endpoint.url)
    pinned = launch_bindings(m)
    cfg.update(owner=pinned['owner'], node_id=pinned['node_id'])
    agent = cfg['values']['agent']
    agent.update(namespace='mcp-test', projects=m['spec']['projects'],
                 active_project=m['spec']['active_project'], default_project=m['spec']['default_project'])
    agent['dependencies'].update(profiles=pinned['profiles'], defaults=pinned['defaults'])
    if with_models:
        agent['models'] = selection.describe()['models']
        cfg['credentials'].pop('model')
        cfg['credentials']['model_services'] = model_dependency.credential
        monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path/'cert.pem'))
    host = ScopedMCP(m['conversion'].describe()['contract']['exports'],
                     config(m['conversion'].describe()['values']['mcp']['servers']))
    await host.start()
    loop, allocations, bad = asyncio.get_running_loop(), [], False
    original_invoke = DependencyClient.invoke
    def invoke(client, method, args=None, *, timeout_seconds=60):
        if method == 'model_services_control':
            return original_invoke(client, method, args, timeout_seconds=timeout_seconds)
        if method == 'bind_dependencies':
            allocations.append(deepcopy(args))
            provider = {**m['provider'], 'fleet_id': cfg['owner']}
            if bad:
                provider['instance_id'] = 'wrong-provider'
            consumer = {key: cfg[key] for key in ('node_id','instance_id','revision','generation')}
            prefix = sha256(f"{provider['instance_id']}:backend:http:{provider['generation']}".encode()).hexdigest()[:32]
            grant = {'consumer': {**consumer, 'fleet_id': cfg['owner']}, 'provider': provider,
                'endpoint': f'https://{prefix}.apps.test/rpc', 'access_token': 'a'*64,
                'grant_id': sha256(('a'*64).encode()).hexdigest(), 'expires': int(time.time())+300}
            return {'success': True, 'result': {
                'protocol': 1, 'owner_ref': args['owner_ref'], 'operation_id': args['operation_id'],
                'consumer': consumer, 'bindings': {'mcp-shared': grant}}}
        assert method == 'docs_check'
        return {'success': True, 'result': asyncio.run_coroutine_threadsafe(host.call(method, args), loop).result(10)}
    monkeypatch.setattr(DependencyClient, 'invoke', invoke)
    identities = []
    try:
        for iteration in range(3):
            bad = iteration == 2
            app = ConfiguredAgentApplication('agent', data_dir=m['root'], configuration=snapshot(cfg),
                                             dependency_ca_file=tmp_path/'cert.pem')
            try:
                await app.run_setup()
                if bad:
                    with pytest.raises(ValueError, match='approved provider'):
                        await app.get_agents('chat-a')
                    assert not app.chat_teams.get('chat-a')
                    continue
                result = await app.get_agents('chat-a')
                assert result['success'], result
                instance = app.chat_teams['chat-a'].team_agents[0]
                identities.append(str(instance.id))
                assert await instance.call_tool('mcp__docs_check', {}, {}) == {**m['before'], 'owner_present': False}
                if with_models and iteration == 0:
                    assert (await instance.run('Continue once')).content == 'scoped reply'
                saved = app.memory_manager.get_memory('chat-a').extra_data['team_template']['agents'][0]
                assert saved['toolsets'] == m['recipe']['toolsets'] and saved['mcp_servers'] == ['mcp']
            finally:
                await app.cleanup()
        assert identities[0] == identities[1]
        assert allocations[0] == allocations[1] == allocations[2]
        if with_models:
            assert model_dependency.data_calls == ['/v1/chat/completions']
            assert not byok_endpoint.requests
            assert any(body.get('model') == 'example:8b' for _, _, body in model_endpoint.requests)
    finally:
        await host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'MODE': 'read'}], indirect=True)
@pytest.mark.parametrize('change', ['missing', 'owner', 'node_id', 'provider', 'schema', 'defaults', 'record', 'symlink'])
async def test_startup_rejects_changed_mcp_bindings_before_instance_store(migration, change):
    m = migration
    restore(m)
    value = launch_bindings(m)
    if change == 'missing': value = None
    elif change in ('owner', 'node_id'): value[change] = 'different'
    elif change == 'provider': value['profiles']['mcp_servers']['mcp']['provider']['instance_id'] = 'different'
    elif change == 'schema': value['profiles']['mcp_servers']['mcp']['functions'][0]['description'] = 'changed'
    elif change == 'defaults': value['defaults']['mcp_unified_precedence'] = False
    else:
        path = m['root']/'migration-mcp-bindings.json'
        if change == 'record': path.write_text('{}')
        else:
            original = path.with_suffix('.saved')
            path.rename(original)
            path.symlink_to(original)
    with pytest.raises(ValueError, match='MCP bindings'):
        AgentAppData(m['root'], namespace='mcp-test', projects=AppProjects(m['spec']['projects']),
                     dependency_configuration=value)


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'MODE': 'read'}], indirect=True)
@pytest.mark.parametrize('migration', ['missing-member-provider', 'missing-template-provider'], indirect=True)
async def test_unconverted_member_or_template_mcp_prevents_import(migration):
    with pytest.raises(ValueError, match='MCP provider'):
        restore(migration)
    assert not migration['root'].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'MODE': 'read'}], indirect=True)
async def test_candidate_and_generation_changes_do_not_admit_import(migration):
    m = migration
    candidate = deepcopy(m['candidate'])
    candidate['defaults']['mcp_servers'] = ['mcp']
    with pytest.raises(ValueError, match='unchanged candidate'):
        m['conversion'].prepare_import(candidate, provider=m['provider'], agent_node_id='agent-node')
    for key, value in [('node_id','wrong'), ('revision','f'*64), ('generation',4), ('instance_id','other-scope')]:
        with pytest.raises(ValueError):
            m['conversion'].prepare_import(m['candidate'], provider={**m['provider'], key:value}, agent_node_id='agent-node')
    (Path(m['candidate']['artifact']['directory'])/'changed.txt').write_text('changed')
    with pytest.raises(ValueError, match='artifact changed'):
        restore(m)
    assert not m['root'].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'MODE': 'read'}], indirect=True)
async def test_failed_mcp_provision_keeps_import_closed_and_resumes_same_binding(migration, monkeypatch):
    m = migration
    def unavailable():
        raise RuntimeError('vault temporarily unavailable')
    monkeypatch.setattr(m['admission'], 'provision', unavailable)
    with pytest.raises(RuntimeError, match='vault temporarily unavailable'):
        restore(m)
    assert json.loads((m['root']/'migration.json').read_text())['phase'] == 'importing'
    with pytest.raises(ValueError, match='has not committed'):
        AgentAppData(m['root'], namespace='mcp-test', projects=AppProjects(m['spec']['projects']),
                     dependency_configuration=launch_bindings(m))
    # Reconstruct the owner-side plan from disk, as after a process exit. The
    # old conversion object and its in-memory candidate registry are not reused.
    fresh = MCPConfigurationConversion(m['saved']['directory'], digest=m['saved']['sha256'], fence=m['fence'],
        vault=m['vault'], targets={'docs': {'command': [sys.executable, str(m['script'])],
                                           'cwd': str(m['script'].parent)}},
        environments={'docs': {'literals': ['MODE'], 'credentials': {}}})
    candidate = fresh.prepare_deployment(m['root'].parent/'recovered-candidate', 'linux-amd64', name='mcp-provider',
        target={'node_id': m['vault'].node_id, 'scope': 'migrated-mcp', 'generation': 0}, aliases={'mcp': 'mcp-shared'})
    m['admission'] = fresh.prepare_import(candidate, provider=m['provider'], agent_node_id='agent-node')
    receipt = restore(m)
    assert receipt['mcp_bindings'] == m['admission'].describe()
    data = AgentAppData(m['root'], namespace='mcp-test', projects=AppProjects(m['spec']['projects']),
                       dependency_configuration=launch_bindings(m))
    data.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [
    {'environment': {'MODE': 'read'}, 'enable_mcp_tools': False, 'mcp_fields': {'sampling_model': 'normal'}},
    {'environment': {'MODE': 'read'}, 'enable_mcp_tools': False, 'mcp_fields': {'auto_start': ['inactive']}},
], indirect=True, ids=['sampling-not-converted', 'inactive-not-captured'])
async def test_unconverted_gateway_features_cannot_silently_disappear(legacy, captured, tmp_path):
    _, _, script = captured
    fence, saved = backup(legacy, tmp_path)
    try:
        vault = local_vault(tmp_path)
        conversion = MCPConfigurationConversion(saved['directory'], digest=saved['sha256'], fence=fence,
            vault=vault, targets={'docs': {'command': [sys.executable, str(script)], 'cwd': str(script.parent)}},
            environments={'docs': {'literals': ['MODE'], 'credentials': {}}})
        candidate = conversion.prepare_deployment(tmp_path/'candidate', 'linux-amd64', name='mcp',
            target={'node_id': vault.node_id, 'scope': 'mcp', 'generation': 0}, aliases={'mcp': 'mcp'})
        revision = candidate['artifact']['revision']
        provider = {'node_id': vault.node_id, 'revision': revision, 'generation': 2,
            'instance_id': sha256('\0'.join((vault.owner, vault.node_id, revision, 'mcp')).encode()).hexdigest()[:32],
            'component': 'backend', 'port': 'http'}
        with pytest.raises(ValueError, match='gateway features'):
            conversion.prepare_import(candidate, provider=provider, agent_node_id='node')
        assert not (tmp_path/'agent-target').exists()
    finally:
        fence.close()
