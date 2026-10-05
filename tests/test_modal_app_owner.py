"""Modal control-plane contract tests with an explicit SDK fixture."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from pantheon.apps.modal_sandbox import ModalSandboxOwner
from pantheon.apps.agent_execution_runner import ExecutionRecoveryRequired, ToolReceiptJournal


def aio(function):
    return SimpleNamespace(aio=function)


class Modal:
    def __init__(self):
        self.calls = []
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()
        self.containers = {}
        self.lose_create = False
        self.confirm_stop = True
        self.App = SimpleNamespace(lookup=aio(self.app))
        self.Image = SimpleNamespace(from_id=lambda identity: identity)
        self.Sandbox = SimpleNamespace(create=aio(self.create), from_id=aio(self.by_id), from_name=aio(self.by_name))

    async def app(self, name, **kwargs):
        return name

    async def create(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.entered.set()
        await self.release.wait()
        container = SimpleNamespace(object_id='sb-' + str(len(self.containers)), code=None, tags=kwargs['tags'].copy(), stops=0)
        async def poll():
            return container.code
        async def terminate(*, wait):
            assert wait is True
            container.stops += 1
            if self.confirm_stop:
                container.code = 137
            return container.code
        async def tags():
            return container.tags
        container.poll, container.terminate, container.get_tags = aio(poll), aio(terminate), aio(tags)
        self.containers[kwargs['name']] = container
        if self.lose_create:
            raise OSError('Create reply lost')
        return container

    async def by_name(self, app, name):
        return self.containers[name]

    async def by_id(self, identity):
        return next(container for container in self.containers.values() if container.object_id == identity)


def owner(tmp_path, sdk, **kwargs):
    return ModalSandboxOwner(tmp_path / 'owner', operation_id='mutation-tools-1',
        app_name='agent-extraction-test', image_id='im-pinned', argv=['python', 'app_runtime.py'],
        modal_sdk=sdk, **kwargs)


@pytest.mark.asyncio
async def test_created_instance_explicit_environment_and_durable_stop(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-forward')
    sdk = Modal()
    lease = owner(tmp_path, sdk, env={'APP_TOKEN': 'private-token'})
    sandbox = await lease.start()
    assert (await lease.stop()) == {'backend_id': sandbox.object_id, 'stopped': True}
    assert sandbox.stops == 1
    _, args = sdk.calls[0]
    assert args['image'] == 'im-pinned' and args['env'] == {'APP_TOKEN': 'private-token'}
    assert args['secrets'] == [] and args['include_oidc_identity_token'] is False
    with sqlite3.connect(lease.journal.path) as db:
        record = db.execute('SELECT request,phase,backend_id,returncode FROM modal_app').fetchone()
        assert 'private-token' not in record[0] and 'must-not-forward' not in record[0]
        assert record[1:] == ('stopped', sandbox.object_id, 137)
    reopened = owner(tmp_path, sdk, env={'APP_TOKEN': 'private-token'})
    with pytest.raises(ExecutionRecoveryRequired, match='repeat creation'):
        await reopened.start()
    assert await reopened.recover_stop() == {'backend_id': sandbox.object_id, 'stopped': True}
    assert len(sdk.calls) == 1


@pytest.mark.asyncio
async def test_repeated_cancel_joins_late_create_then_terminates(tmp_path):
    sdk = Modal()
    sdk.release.clear()
    lease = owner(tmp_path, sdk)
    started = asyncio.create_task(lease.start())
    await sdk.entered.wait()
    with sqlite3.connect(lease.journal.path) as db:
        assert db.execute('SELECT phase FROM modal_app').fetchone() == ('creating',)
    started.cancel()
    await asyncio.sleep(0)
    started.cancel()
    await asyncio.sleep(0)
    assert not started.done()
    sdk.release.set()
    with pytest.raises(asyncio.CancelledError):
        await started
    assert len(sdk.calls) == 1
    assert next(iter(sdk.containers.values())).code == 137
    assert (await lease.stop())['stopped']


@pytest.mark.asyncio
async def test_lost_create_reply_recovers_by_persisted_name_and_nonce(tmp_path):
    sdk = Modal()
    sdk.lose_create = True
    lease = owner(tmp_path, sdk)
    with pytest.raises(OSError, match='reply lost'):
        await lease.start()
    original_name = lease.name
    lease.journal.close()  # Abrupt owner process exit before a backend ID was saved.
    reopened = owner(tmp_path, sdk)
    assert reopened.name != original_name
    receipt = await reopened.recover_stop()
    assert reopened.name == original_name
    assert receipt == {'backend_id': 'sb-0', 'stopped': True}
    assert len(sdk.calls) == 1


@pytest.mark.asyncio
async def test_failed_stop_keeps_owner_until_explicit_recovery(tmp_path):
    sdk = Modal()
    sdk.confirm_stop = False
    lease = owner(tmp_path, sdk)
    await lease.start()
    with pytest.raises(ExecutionRecoveryRequired, match='not confirmed'):
        await lease.stop()
    with pytest.raises(BlockingIOError):
        ToolReceiptJournal(tmp_path / 'owner', 'modal-app-owner')
    with pytest.raises(RuntimeError, match='closed'):
        await lease.start()
    sdk.confirm_stop = True
    assert (await lease.recover_stop())['stopped']
    assert len(sdk.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['wrong-tags', 'not-found'])
async def test_uncertain_creation_does_not_stop_foreign_or_recreate_missing_container(tmp_path, fault):
    sdk = Modal()
    sdk.lose_create = True
    lease = owner(tmp_path, sdk)
    with pytest.raises(OSError):
        await lease.start()
    sandbox = sdk.containers[lease.name]
    if fault == 'wrong-tags':
        sandbox.tags['pantheon_owner'] = 'some-other-owner'
    else:
        sdk.containers.clear()
    with pytest.raises(ExecutionRecoveryRequired):
        await lease.stop()
    assert sandbox.stops == 0 and len(sdk.calls) == 1
    # Restore the control-plane evidence and explicitly reconcile the same ID.
    sandbox.tags = lease.tags.copy()
    sdk.containers[lease.name] = sandbox
    assert (await lease.recover_stop())['stopped']


@pytest.mark.asyncio
async def test_stop_before_start_cannot_create_afterwards(tmp_path):
    sdk = Modal()
    lease = owner(tmp_path, sdk)
    assert await lease.stop() == {'backend_id': None, 'stopped': True}
    with pytest.raises(RuntimeError, match='closed'):
        await lease.start()
    assert not sdk.calls


def test_mutable_image_rejected_before_any_creation(tmp_path):
    with pytest.raises(ValueError, match='pin a Modal image'):
        ModalSandboxOwner(tmp_path / 'owner', operation_id='test', app_name='test',
            image_id='registry/app:latest', argv=['python'])
