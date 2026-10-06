"""Owner command admission uses the actual prepared ordinary MCP provider."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

from pantheon.chatroom.data_transition import check_mcp_launch, import_reservation, transition_state
from pantheon.chatroom.migration_profile import LocalAgentMigration
from test_agent_migration import legacy
from test_mcp_configuration_migration import captured
from test_mcp_migration_import import migration, launch_bindings


def inputs(m):
    candidate = m['candidate']
    app = candidate['provider_apps']['mcp-provider']
    request = dict(protocol=1, operation='mcp-config', app='agent', legacy=m['spec'],
        backup=str(Path(m['saved']['directory']).parent), mcp_configuration={
            'app': 'mcp-provider', 'targets': {'docs': {
                'command': [sys.executable, str(m['script'])], 'cwd': str(m['script'].parent)}},
            'environments': {'docs': {'literals': ['MODE'], 'credentials': {}}},
            'aliases': {'mcp': 'mcp-shared'}, 'enable_mcp': True})
    configuration = dict(namespace='mcp-test', dependencies=launch_bindings(m))
    prepared = dict(package=candidate['artifact']['directory'], platform='linux-amd64',
        name='mcp-provider', target={k: app[k] for k in ('node_id', 'scope', 'generation')},
        provider=m['provider'], components=deepcopy(app['components']))
    return request, dict(target=m['root'], configuration=configuration, owner='owner', node_id='agent-node',
                        request_digest='a'*64, vault=m['vault'], mcp_prepared=prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'MODE': 'read'}], indirect=True)
async def test_owner_imports_captured_mcp_into_selected_prepared_app_and_resumes(migration):
    m = migration
    request, args = inputs(m)
    m['fence'].close()
    workflow = LocalAgentMigration(request)
    result = workflow._import(**args)
    assert result['state'] == 'imported'
    assert result['receipt']['mcp_bindings'] == m['admission'].describe()
    assert result['receipt']['mcp_bindings']['protocol'] == 2
    assert workflow._import(**args) == result
    assert not (m['root']/'configuration/.pantheon/mcp.json').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'MODE': 'read'}], indirect=True)
@pytest.mark.parametrize('change', ['identity', 'values', 'platform', 'revision', 'defaults', 'schema', 'slots'])
async def test_changed_prepared_mcp_cannot_admit_agent_data(migration, change):
    m = migration
    request, args = inputs(m)
    prepared = args['mcp_prepared']
    if change == 'identity': prepared['provider'] = {**prepared['provider'], 'instance_id': 'other-instance'}
    elif change == 'revision': prepared['provider'] = {**prepared['provider'], 'revision': 'e'*64}
    elif change == 'values': prepared['components']['backend']['values']['mcp']['servers']['docs']['cwd'] = '/different'
    elif change == 'slots': prepared['components']['backend']['credentials']['unreviewed'] = {}
    elif change == 'platform': prepared['platform'] = 'darwin-arm64'
    elif change == 'defaults':
        defaults = args['configuration']['dependencies']['defaults']
        defaults['mcp_unified_precedence'] = not defaults['mcp_unified_precedence']
    else: args['configuration']['dependencies']['profiles']['mcp_servers']['mcp']['functions'][0]['description'] = 'different'
    m['fence'].close()
    with pytest.raises(ValueError): LocalAgentMigration(request)._import(**args)
    assert transition_state(m['root']) is None
    assert import_reservation(m['root'])['phase'] == 'reserved'


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'MODE': 'read'}], indirect=True)
async def test_mcp_restart_admission_keeps_scope_and_contract_but_allows_new_generation(migration):
    expected = migration['admission'].describe()
    actual = launch_bindings(migration)
    actual['profiles']['mcp_servers']['mcp']['provider']['generation'] += 3
    check_mcp_launch(expected, actual)
    # Old receipts retain their original exact-generation contract.
    with pytest.raises(ValueError): check_mcp_launch({**expected, 'protocol': 1}, actual)
    for field, value in [('generation', True), ('generation', 0), ('revision', 'e'*64),
                         ('instance_id', 'different'), ('node_id', 'other')]:
        changed = deepcopy(actual)
        changed['profiles']['mcp_servers']['mcp']['provider'][field] = value
        with pytest.raises(ValueError): check_mcp_launch(expected, changed)


@pytest.mark.asyncio
@pytest.mark.parametrize('capability', [None, {}, {'protocols': [1]}, {'protocols': [True]}, {'protocols': '2'}])
async def test_old_agent_release_is_rejected_before_mcp_initialization(legacy, tmp_path, capability):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from pantheon.apps.dependency_assembly import AssemblyError
    from pantheon.chatroom.data_transition import INITIALIZATION_CAPABILITY
    request = dict(protocol=1, operation='mcp-upgrade', app='agent', legacy=legacy,
        backup=str(tmp_path/'backup'), mcp_configuration=dict(app='mcp', targets={}, environments={},
                                                           aliases={}, enable_mcp=True))
    session = SimpleNamespace(info=SimpleNamespace(node_id='node'),
        prepared_app=AsyncMock(return_value={'identity': {'node_id': 'node', 'revision': 'a'*64}}),
        wire=SimpleNamespace(manifest=AsyncMock(return_value={'manifest': {'id': 'agent', 'caps': {
            'agentDataInitialization': INITIALIZATION_CAPABILITY, 'agentMCPMigration': capability}}})))
    with pytest.raises(AssemblyError, match='durable MCP bindings'):
        await LocalAgentMigration(request)(session)
    assert not (tmp_path/'backup').exists()
