"""Budget credential preparation cannot bypass live publication admission."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from pantheon.chatroom.migration_profile import LocalAgentMigration
from pantheon.chatroom.data_transition import import_reservation, transition_state
from pantheon.models.client import model_ref
from test_agent_migration import legacy
from test_agent_migration_credentials import vault, read_key
from test_agent_migration_budget import capture_budget
from test_model_dependency import model_dependency, model_endpoint, tls_material


def request_for(legacy, tmp_path, base, *, enabled=True):
    _, budget = capture_budget(legacy, tmp_path, base, enabled=enabled,
                               credentials=enabled, byok=False)
    ref = model_ref('mac', 'example:8b')
    tiers = {tier: ref for tier in ('low', 'normal', 'high')}
    request = dict(protocol=1, operation='owner-budget', app='agent', legacy=legacy,
        backup=str(tmp_path/'backup'), model_credentials={'bindings': [], 'platform_budget': budget},
        model_selection=dict(fleet_tiers=tiers, budget_choice=budget['choice'],
            source_service_id=budget['choice']['service_id'], selections=[
                dict(conversation_id=cid, config_id='member', source='openai/fixture', target=ref)
                for cid in ('chat-a', 'chat-b')]))
    configuration = dict(namespace='process-app', models={'model_services': 'model_services', 'fleet_tiers': tiers})
    return request, configuration


@pytest.mark.asyncio
async def test_budget_prepare_then_failed_review_keeps_destination_closed_and_same_request_recovers(
        legacy, tmp_path, vault, model_dependency, model_endpoint):
    request, configuration = request_for(legacy, tmp_path, model_endpoint.url)
    workflow = LocalAgentMigration(request)
    target = tmp_path/'data'
    args = dict(target=target, configuration=configuration, owner='owner', node_id='node',
                request_digest='a'*64, vault=vault)
    result = await asyncio.to_thread(workflow._import, **args, provision_only=True)
    assert result['state'] == 'credentials-prepared'
    assert transition_state(target) is None and import_reservation(target)['phase'] == 'reserved'
    connector = request['model_credentials']['platform_budget']['provisioned']['connector']
    assert read_key(vault, connector['secret_ref'], connector['endpoint']) == 'budget-virtual-key'
    loop = asyncio.get_running_loop()
    def review(selection, receipt):
        return asyncio.run_coroutine_threadsafe(selection.review_budget(
            model_dependency.control.client, receipt), loop).result()
    with pytest.raises(ValueError, match='publication review'):
        await asyncio.to_thread(workflow._import, **args)
    with pytest.raises(ValueError, match='provisioned budget Connector'):
        await asyncio.to_thread(workflow._import, **args, review_budget=review)
    assert transition_state(target) is None
    model_dependency.connector.configure(connector)
    model_dependency.deployment.update(engine='api', node_id='node',
                                      config_revision=model_dependency.connector.revision)
    model_dependency.deployment['binding']['node_id'] = 'node'
    result = await asyncio.to_thread(workflow._import, **args, review_budget=review)
    assert result['state'] == 'imported'
    assert transition_state(target)['phase'] == 'committed'
    assert await asyncio.to_thread(workflow._import, **args, review_budget=review) == result
    assert 'budget-virtual-key' not in json.dumps(result)
    audit = json.loads((target/'migration-model-selections.json').read_text())
    assert audit['budget_review']['provisioning'] == request['model_credentials']['platform_budget']['provisioned']


def test_disabled_unconfigured_budget_needs_no_live_review_or_provider_key(legacy, tmp_path, vault):
    request, configuration = request_for(legacy, tmp_path, 'https://proxy.example', enabled=False)
    workflow = LocalAgentMigration(request)
    assert not workflow.needs_budget_review
    result = workflow._import(target=tmp_path/'data', configuration=configuration, owner='owner',
                              node_id='node', request_digest='a'*64, vault=vault)
    assert result['state'] == 'imported'
    assert not (vault.state_dir/'apps').exists()


def test_abort_after_budget_key_preparation_releases_source_without_admitting_target(legacy, tmp_path, vault):
    from pantheon.chatroom.data_fence import LegacyDataLease
    from pantheon.chatroom.migration_backup import verify_backup
    request, configuration = request_for(legacy, tmp_path, 'https://proxy.example')
    target = tmp_path/'data'
    args = dict(target=target, configuration=configuration, owner='owner', node_id='node',
                request_digest='a'*64, vault=vault)
    prepared = LocalAgentMigration(request)._import(**args, provision_only=True)
    workflow = LocalAgentMigration(request, abort=True)
    assert not workflow.needs_budget_review
    assert workflow._import(**args)['state'] == 'aborted'
    assert import_reservation(target)['phase'] == 'aborted'
    assert transition_state(target) is None
    lease = LegacyDataLease()
    try:
        lease.acquire(legacy['home_memory'])
    finally:
        lease.close()
    backup = prepared['backup']
    assert verify_backup(backup['directory'], digest=backup['sha256']) == backup
    # Abort does not erase a provider credential that another explicit owner
    # operation may already use. It only releases migration source ownership.
    connector = request['model_credentials']['platform_budget']['provisioned']['connector']
    assert read_key(vault, connector['secret_ref'], connector['endpoint']) == 'budget-virtual-key'


@pytest.mark.parametrize('change', ['endpoint', 'ref', 'missing', 'duplicate', 'engine'])
def test_budget_request_must_name_exactly_one_selected_connector(legacy, tmp_path, change):
    request, _ = request_for(legacy, tmp_path, 'https://proxy.example')
    connector = request['model_credentials']['platform_budget']['provisioned']['connector']
    spec = {'model_apps': {'budget': {'app': {'components': {'backend': {'values': {
        'connector': deepcopy(connector)}}}}}}}
    LocalAgentMigration(request)._check_credential_targets(spec)
    selected = spec['model_apps']['budget']['app']['components']['backend']['values']['connector']
    if change == 'endpoint': selected['endpoint'] = 'https://other.example/v1'
    elif change == 'ref': selected['secret_ref'] = 'node-secret://other'
    elif change == 'engine': selected['engine'] = 'ollama'
    elif change == 'missing': spec['model_apps'] = {}
    else: spec['model_apps']['duplicate'] = deepcopy(spec['model_apps']['budget'])
    with pytest.raises(ValueError, match='selected Model Service Connector'):
        LocalAgentMigration(request)._check_credential_targets(spec)
