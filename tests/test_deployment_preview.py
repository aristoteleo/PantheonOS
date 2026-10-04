"""Installed-target review shares start contracts, without lifecycle side effects."""
import asyncio
from copy import deepcopy
import json
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.dependency_assembly import AssemblyError, DependencyStarter
from pantheon.apps.deployment import AppDeployment
from pantheon.apps.deployment_preview import preview_deployment
from test_app_deployment import Nodes, Authority, apps


def installed():
    nodes = Nodes()
    for app in apps().values():
        nodes.states[app['node_id']]['installations'][app['revision']] = {'state': 'installed'}
    nodes.status = AsyncMock(wraps=nodes.status)
    nodes.manifest = AsyncMock(wraps=nodes.manifest)
    return nodes


@pytest.mark.asyncio
async def test_review_matches_start_contract_without_touching_owner_or_nodes(tmp_path):
    nodes, proposed = installed(), apps()
    original = deepcopy(proposed)
    state = deepcopy(nodes.states)
    result = await preview_deployment(nodes, owner='owner', operation_id='review-one', apps=proposed)
    assert result['state'] == 'reviewed' and result['observation'] == 'read-only-snapshot'
    assert result['order'] == ['allocator', 'agent']
    assert [item['app_id'] for item in result['apps']] == ['dependency-binding', 'test-agent']
    assert result['apps'][1]['dependencies'] == ['allocator']
    assert nodes.status.await_count == nodes.manifest.await_count == 2
    assert proposed == original and nodes.states == state and not nodes.calls
    assert 'private-vault' not in json.dumps(result) and 'policies' not in json.dumps(result)
    # A successful review is not a reservation: a subsequent owner race must
    # still fail the normal configured start, before issuing any dependency.
    nodes.manifests['a'*64]['manifest']['version'] = '2.0.0'
    authority = Authority(nodes)
    runner = AppDeployment(DependencyStarter(nodes, tmp_path/'starts', authority), tmp_path/'deploy')
    with pytest.raises(AssemblyError, match='Provider version'):
        for _ in range(20):
            await runner.advance(owner='owner', operation_id='review-one', apps=proposed)
            nodes.finish()
    assert not authority.grants


@pytest.mark.asyncio
@pytest.mark.parametrize('change, message', [
    ('foreign-owner', 'another owner'), ('old-node', 'Fleet update'),
    ('missing-release', 'Install the exact'), ('missing-credential', 'credential input'),
    ('missing-value', 'configuration inputs'), ('wrong-version', 'Provider version'),
    ('wrong-method', 'dependency interface'), ('running-target', 'cutover'),
    ('generation-changed', 'generation changed'), ('already-submitted', 'already been submitted'),
])
async def test_invalid_review_has_no_mutation(change, message):
    nodes, proposed = installed(), apps()
    allocator = nodes.states['platform']
    if change == 'foreign-owner': allocator['owner'] = 'other'
    elif change == 'old-node': allocator['dependency_config_protocol'] = True
    elif change == 'missing-release': allocator['installations'].clear()
    elif change == 'missing-credential': proposed['allocator']['components']['backend']['credentials'].pop('hub')
    elif change == 'missing-value': proposed['agent']['components']['backend']['values'].clear()
    elif change == 'wrong-version': nodes.manifests['a'*64]['manifest']['version'] = '2.0.0'
    elif change == 'wrong-method': proposed['agent']['bindings']['allocator']['methods'] = {'erase': {'arguments': [], 'bound': {}}}
    elif change == 'running-target': allocator['instances']['in-use'] = dict(digest='a'*64, scope='deploy-allocator', state='ready', generation=2)
    elif change == 'generation-changed': proposed['allocator']['generation'] = 3
    else:
        recipe = dict(owner='owner', operation_id='review-one', apps=proposed)
        allocator['operations'][AppDeployment.operation_id(recipe, 'allocator', 'prepare_start')] = {}
    before = deepcopy(nodes.states)
    with pytest.raises(AssemblyError, match=message):
        await preview_deployment(nodes, owner='owner', operation_id='review-one', apps=proposed)
    assert nodes.states == before and not nodes.calls and not nodes.configurations


@pytest.mark.asyncio
async def test_external_provider_generation_and_stopped_consumer_are_explicit():
    nodes, proposed = installed(), apps()
    target = proposed.pop('allocator')
    provider = dict(node_id='platform', instance_id='existing', revision='a'*64, generation=4, component='backend', port='http')
    proposed['agent']['bindings']['allocator']['provider'] = provider
    nodes.states['platform']['instances']['existing'] = dict(digest='a'*64, scope=target['scope'], state='ready', generation=4, ready_generation=4)
    nodes.states['worker']['instances']['old-agent'] = dict(digest='b'*64, scope='deploy-agent', state='stopped', generation=6)
    proposed['agent']['generation'] = 6
    assert (await preview_deployment(nodes, owner='owner', operation_id='review-one', apps=proposed))['order'] == ['agent']
    nodes.states['platform']['instances']['existing']['generation'] = 5
    with pytest.raises(AssemblyError, match='selected ready generation'):
        await preview_deployment(nodes, owner='owner', operation_id='review-one', apps=proposed)
    assert not nodes.calls


@pytest.mark.asyncio
async def test_transport_failure_does_not_disclose_body_and_cancellation_propagates():
    nodes = installed()
    nodes.manifest.side_effect = RuntimeError('private upstream body')
    with pytest.raises(AssemblyError, match='review is unavailable') as error:
        await preview_deployment(nodes, owner='owner', operation_id='review-one', apps=apps())
    assert 'private upstream' not in str(error.value)
    nodes.manifest.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await preview_deployment(nodes, owner='owner', operation_id='review-one', apps=apps())
    assert not nodes.calls


@pytest.mark.asyncio
async def test_platform_review_does_not_start_maintenance_or_create_journal(tmp_path):
    from pantheon.platform.service import PlatformService
    platform = PlatformService(workspace_path=str(tmp_path))
    nodes = installed()
    starter = DependencyStarter(nodes, tmp_path/'starts', Authority(nodes))
    platform._app_deployments = lambda: AppDeployment(starter, tmp_path/'deploy')
    platform._start_dependency_maintenance = lambda: pytest.fail('review started maintenance')
    try:
        result = await platform.fleet_app_deploy(owner='owner', operation_id='review-one', action='preview', apps=apps())
        assert result['success'] and result['state'] == 'reviewed'
        assert not (tmp_path/'deploy').exists() and not (tmp_path/'starts').exists()
        assert not nodes.calls
    finally:
        await platform.cleanup()

def test_review_platform_runs_without_agent_execution_imports(tmp_path):
    nodes = installed()
    payload = tmp_path/'nodes.json'
    payload.write_text(json.dumps({'states': nodes.states, 'manifests': nodes.manifests, 'apps': apps()}))
    script = r"""
import asyncio, importlib.abc, json, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == 'pantheon.agent' or fullname.startswith(('pantheon.chatroom', 'pantheon.repl', 'pantheon.team', 'pantheon.factory')):
            raise AssertionError('Agent import attempted: ' + fullname)
sys.meta_path.insert(0, Block())
from pantheon.platform.service import PlatformService
async def run():
    data = json.loads(Path(sys.argv[1]).read_text())
    lifecycle = SimpleNamespace(
        status=AsyncMock(side_effect=lambda node: data['states'][node]),
        manifest=AsyncMock(side_effect=lambda node, revision: data['manifests'][revision]))
    platform = PlatformService(workspace_path=str(Path(sys.argv[1]).parent))
    platform._app_deployments = lambda: SimpleNamespace(lifecycle=lifecycle)
    try:
        result = await platform.fleet_app_deploy(owner='owner', operation_id='no-agent', action='preview', apps=data['apps'])
        assert result['success'] and result['order'] == ['allocator', 'agent'], result
    finally:
        await platform.cleanup()
asyncio.run(run())
"""
    result = subprocess.run([sys.executable, '-c', script, str(payload)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
