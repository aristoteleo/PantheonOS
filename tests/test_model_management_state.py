"""Prepared management preserves the real deployment path with per-App owners."""
import asyncio
import subprocess
import sys

import pytest

from pantheon.apps.reflect import reflect_toolset_class
from pantheon.internal.model_services_plugin import ModelServicesToolSet
from pantheon.models.management_tools import ModelManagementToolSet
from pantheon.models.management_state import ManagementState
from pantheon.models.manager import ModelServiceManager
from pantheon.models import modal_gpu, model_deploy
from test_model_deploy import Manager, cpu_node


class Controller:
    def __init__(self, key):
        self.key, self.calls = key, []

    async def request(self, path, body):
        self.calls.append((path, body))
        return {'join_token': self.key}


def state(root, key='owned'):
    root.mkdir(mode=0o700)
    return ManagementState(root, Controller(key))


def test_management_tool_contract_keeps_all_operations_except_local_team_selection():
    legacy = {t.name: t.model_dump() for t in reflect_toolset_class(ModelServicesToolSet)}
    ordinary = {t.name: t.model_dump() for t in reflect_toolset_class(ModelManagementToolSet)}
    assert ordinary == {k: v for k, v in legacy.items() if k != 'use_fleet_model'}
    assert len(ordinary) == 10  # Nine public tools plus ToolSet's hidden list_tools.
    with pytest.raises(ValueError, match='explicitly'):
        ModelManagementToolSet(None)
    # An ordinary management process must not import Agent/team/plugin code.
    code = '''
import importlib.abc, sys
class Forbid(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('pantheon.agent', 'pantheon.team', 'pantheon.internal.model_services_plugin', 'pantheon.settings')):
            raise RuntimeError('Forbidden import: ' + fullname)
sys.meta_path.insert(0, Forbid())
from pantheon.models.management_tools import ModelManagementToolSet
from pantheon.apps.reflect import reflect_toolset_class
assert len(reflect_toolset_class(ModelManagementToolSet)) == 10
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_two_real_deployment_paths_keep_plans_tasks_and_controller_separate(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Prepared management accessed global state')
    monkeypatch.setattr(model_deploy, '_plans', forbidden)
    monkeypatch.setattr(modal_gpu, '_controller', forbidden)
    globals_before = (dict(model_deploy._tasks), dict(modal_gpu._starts))
    first, second = Manager([cpu_node()]), Manager([cpu_node()])
    first.management, second.management = state(tmp_path/'first', 'one'), state(tmp_path/'second', 'two')
    for manager in (first, second):
        # Real deploy/status paths: same id and node, independent durable plans.
        result = await model_deploy.deploy(manager, {'kind': 'node', 'node_id': 'n_cpu'},
            'ollama', {'catalog_id': model_deploy.ollama_catalog()[1]['id']}, name='same')
        assert result['phase'] == 'preparing_engine'
        manager.engine_prepared = True
    dep = result['deployment_id']
    assert (tmp_path/'first'/f'{dep}.json').exists()
    assert (model_deploy._tasks, modal_gpu._starts) == globals_before
    entered, release = asyncio.Event(), asyncio.Event()
    stopped = []
    async def blocked_start(deployment_id, running):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await release.wait()
            stopped.append(deployment_id)
    first.set_running = blocked_start
    await model_deploy.status(first, dep)
    await entered.wait()
    await model_deploy.status(second, dep)
    await asyncio.sleep(0)
    assert len(first.management.engine_tasks('deploy')) == 1
    assert second.client.rows[dep]['state'] == 'ready'
    for manager, token in ((first, 'one'), (second, 'two')):
        assert await modal_gpu.controller_request(manager, '/join-tokens', {}) == {'join_token': token}
        # Exercise original bare-node lifecycle routing too, without launching a real GPU.
        await modal_gpu.start_node(manager, 'owner', gpu='none')
        assert manager.management.controller.calls[-1] == ('/join-tokens', {})
    close = asyncio.create_task(first.management.close())
    await asyncio.sleep(0)
    close.cancel()
    await asyncio.sleep(0)
    assert not close.done() and not stopped
    with pytest.raises(RuntimeError, match='closing'):
        first.management.engine_tasks('deploy')
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    await first.management.close()
    assert stopped == [dep]
    assert await model_deploy.status(second, dep)  # Other App still works.
    await second.management.close()
    reopened = ManagementState(tmp_path/'first', Controller('fresh'))
    assert reopened.load_plan(dep)['deployment_id'] == dep
    assert reopened.engine_tasks('deploy') == {}
    await reopened.close()


@pytest.mark.asyncio
async def test_prepared_manager_never_falls_back_on_falsey_connections(tmp_path, monkeypatch):
    owned = state(tmp_path/'owned')
    monkeypatch.setattr('pantheon.models.manager.get_client', lambda: pytest.fail('ambient client'))
    monkeypatch.setattr('pantheon.models.manager.AppInstanceResolver.from_env', lambda: pytest.fail('ambient resolver'))
    class Empty:
        def __bool__(self): return False
    client, resolver = Empty(), Empty()
    manager = ModelServiceManager(client, resolver, management=owned)
    assert manager.client is client and manager.resolver is resolver
    for kwargs in ({'client': client}, {'resolver': resolver}, {}):
        with pytest.raises(ValueError, match='explicit'):
            ModelServiceManager(management=owned, **kwargs)
    await owned.close()


def test_private_plans_reject_traversal_symlinks_mismatched_identity_and_large_input(tmp_path):
    owned = state(tmp_path/'owned')
    plan = {'deployment_id': 'one', 'engine': 'ollama'}
    owned.save_plan(plan)
    path = owned.root/'one.json'
    assert path.stat().st_mode & 0o777 == 0o600
    assert owned.load_plan('one') == plan
    path.write_text('{"deployment_id":"other"}')
    with pytest.raises(ValueError, match='identity'):
        owned.load_plan('one')
    path.unlink()
    outside = tmp_path/'outside'; outside.write_text('DO NOT TOUCH')
    path.symlink_to(outside)
    with pytest.raises((ValueError, OSError)):
        owned.load_plan('one')
    with pytest.raises(ValueError):
        owned.save_plan(plan)
    assert outside.read_text() == 'DO NOT TOUCH'
    for value in ('../outside', '/outside', ''):
        with pytest.raises(ValueError): owned.load_plan(value)
    with pytest.raises(ValueError, match='large'):
        owned.save_plan({'deployment_id': 'large', 'x': 'x' * (1024 * 1024)})
    assert not list(owned.root.glob('.plan-*'))
    public = tmp_path/'public'; public.mkdir(mode=0o755); public.chmod(0o755)
    with pytest.raises(ValueError, match='private'):
        ManagementState(public, Controller('key'))


@pytest.mark.asyncio
async def test_prepared_modal_launch_uses_owned_controller_and_task_table(tmp_path, monkeypatch):
    from test_model_modal_gpu import FakeManager, GPU
    def forbidden(*args, **kwargs):
        raise AssertionError('Prepared Modal launch used ambient Controller')
    monkeypatch.setattr(modal_gpu, '_controller', forbidden)
    before = dict(modal_gpu._starts)
    managers = [FakeManager([]), FakeManager([])]
    try:
        for i, manager in enumerate(managers):
            manager.management = state(tmp_path/str(i), f'token-{i}')
            assert (await modal_gpu.start(manager, 'same', gpu='H100'))['phase'] == 'starting_node'
            post = next(call for call in manager.client.hub_calls if call[0] == 'POST')
            assert post[2]['join_token'] == f'token-{i}'
            manager.nodes = [dict(node_id=f'node-{i}', labels=['modal-gpu', 'svc-same'],
                capability={'resources': {'accelerators': [dict(id=GPU['id'], backend='cuda',
                    memory={'total_bytes': 80 << 30})]}})]
            assert (await modal_gpu.advance(manager, 'same'))['phase'] == 'downloading_weights'
            manager.weights_ready = True
            assert (await modal_gpu.advance(manager, 'same'))['phase'] == 'starting_engine'
        first = managers[0].management.engine_tasks('modal')['modal-same']
        second = managers[1].management.engine_tasks('modal')['modal-same']
        assert first is not second and modal_gpu._starts == before
        await asyncio.sleep(0)
        for i, manager in enumerate(managers):
            result = await modal_gpu.advance(manager, 'same')
            assert result['phase'] == 'ready' and result['node_id'] == f'node-{i}'
            await modal_gpu.stop(manager, 'same')
            assert manager.management.controller.calls[-1] == ('/revoke', {'node_id': f'node-{i}'})
    finally:
        for manager in managers:
            if hasattr(manager, 'management'):
                await manager.management.close()
