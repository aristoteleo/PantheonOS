import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from pantheon.apps.agent_execution_runner import (
    AgentExecutionRunner, ExecutionEnded, ExecutionRecoveryRequired, ToolReceiptJournal,
)
from pantheon.chatroom.execution_service import AgentExecutions, ExecutionJournal
from test_agent_executions import SPEC, observe, result


class BoundClient:
    """Local test grant binding; production uses AgentExecutionClient."""
    def __init__(self, service):
        self.service = service

    async def read_result(self, identity):
        return await result(self.service, identity=identity)

    def __getattr__(self, name):
        async def call(*args):
            return await getattr(self.service, name)('consumer', *args)
        return call


def runner(tmp_path, client, handler, **kwargs):
    return AgentExecutionRunner(client, tmp_path / 'caller', binding_id='owned-agent-and-evolution',
                                tool_handler=handler, poll_interval=.001, **kwargs)


@pytest.mark.asyncio
async def test_parallel_tools_real_files_and_observer_loss_never_repeat(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    edits = []
    async def engine(spec, invoke):
        return {'content': await asyncio.gather(invoke('files', 'edit', {'text': 'first'}),
                                               invoke('files', 'edit', {'text': 'second'}))}
    async def handler(provider, name, args):
        edits.append(args['text'])
        if len(edits) == 2: entered.set()
        await release.wait()
        (tmp_path / args['text']).write_text(args['text'])
        return {'ok': True, 'value': args['text']}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = runner(tmp_path, BoundClient(service), handler)
    try:
        observer = asyncio.create_task(owned.run('run', SPEC))
        await asyncio.wait_for(entered.wait(), 5)
        observer.cancel()
        with pytest.raises(asyncio.CancelledError): await observer
        reattached = asyncio.create_task(owned.run('run', SPEC))
        with pytest.raises(ValueError): await owned.run('run', {**SPEC, 'prompt': 'different'})
        release.set()
        assert (await asyncio.wait_for(reattached, 5))['content'] == ['first', 'second']
        assert edits == ['first', 'second']
        assert (tmp_path / 'first').read_text() == 'first'
        await owned.close()
        reopened = runner(tmp_path, BoundClient(service), handler)
        assert (await reopened.run('run', SPEC))['content'] == ['first', 'second']
        assert len(edits) == 2
        await reopened.close()
    finally:
        release.set()
        await owned.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted', [False, True])
async def test_lost_reply_reopen_resends_receipt_not_effect(tmp_path, accepted):
    edits = []
    async def engine(spec, invoke):
        return {'content': await invoke('files', 'edit', {'text': 'saved'})}
    async def handler(*args):
        edits.append('write')
        (tmp_path / 'saved.py').write_text('x=7')
        return {'ok': True, 'value': 7}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    class LosingClient(BoundClient):
        async def reply(self, *args):
            if accepted:
                await self.service.reply('consumer', *args)
            raise OSError('lost acknowledgement')
    owned = runner(tmp_path, LosingClient(service), handler)
    try:
        with pytest.raises(ExecutionRecoveryRequired): await owned.run('run', SPEC)
        with pytest.raises(ExecutionRecoveryRequired): await owned.run('replacement', SPEC)
        with pytest.raises(ExecutionRecoveryRequired): await owned.close()
        reopened = runner(tmp_path, BoundClient(service), handler)
        with pytest.raises(ExecutionRecoveryRequired): await reopened.run('replacement', SPEC)
        try:
            await reopened.run('run', SPEC)
        except ExecutionEnded as exc:
            assert exc.state == 'cancelled'
        assert edits == ['write']
        assert not (await service.poll('consumer', 'run'))['pending_tools']
        await reopened.close()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_before_restore', [True, False])
async def test_lost_claim_before_effect_recovers_only_prepared_intent(tmp_path, stop_before_restore):
    calls = []
    async def engine(spec, invoke):
        return await invoke('files', 'edit', {'text': 'x'})
    async def handler(*args):
        calls.append('effect')
        return {'ok': True, 'value': 'done'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    await service.submit('consumer', 'run', SPEC)
    status = await observe(service, predicate=lambda s: s['call'])
    # Crash after the Agent accepted a claim, before the caller crossed its
    # executing fence. The subsequent Agent stop must settle it WITHOUT effects.
    journal = ToolReceiptJournal(tmp_path / 'caller', 'owned-agent-and-evolution')
    spec = {**SPEC, 'max_turns': 40, 'timeout_seconds': 600}
    journal.prepare('run', json.dumps(spec, sort_keys=True, separators=(',', ':')))
    worker, _, _ = journal.call('run', status['call'])
    await service.claim('consumer', 'run', status['call']['id'], worker)
    if stop_before_restore:
        await service.cancel('consumer', 'run')
        await observe(service)
    journal.close()
    owned = runner(tmp_path, BoundClient(service), handler)
    try:
        if stop_before_restore:
            with pytest.raises(ExecutionEnded): await asyncio.wait_for(owned.run('run', SPEC), 5)
        else:
            assert await asyncio.wait_for(owned.run('run', SPEC), 5) == 'done'
        assert calls == ([] if stop_before_restore else ['effect'])
        assert not (await service.poll('consumer', 'run'))['pending_tools']
    finally:
        await owned.close()
        await service.close()


@pytest.mark.asyncio
async def test_close_drains_disk_tool_and_cancels_owned_async_tool(tmp_path):
    entered, release, cancelled, reaped = (asyncio.Event() for _ in range(4))
    async def engine(spec, invoke):
        return await asyncio.gather(invoke('files', 'edit', {'text': 'disk'}),
                                    invoke('files', 'stop', {}))
    async def handler(provider, name, args):
        if name == 'edit':
            entered.set()
            await release.wait()
            (tmp_path / 'done').write_text('accepted disk write')
            return {'ok': True, 'value': 'written'}
        try:
            cancelled.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(.03)
            reaped.set()
    spec = {**SPEC, 'tools': {'files': [*SPEC['tools']['files'],
            {'name': 'stop', 'description': 'owned async work',
             'parameters': {'type': 'object', 'properties': {}}}]}}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = runner(tmp_path, BoundClient(service), handler, cancel_tools={('files', 'stop')})
    observer = asyncio.create_task(owned.run('run', spec))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.wait_for(cancelled.wait(), 5)
        closing = asyncio.create_task(owned.close())
        await asyncio.wait_for(reaped.wait(), 5)
        closing.cancel()
        await asyncio.sleep(.01)
        assert not closing.done()
        with pytest.raises((BlockingIOError, OSError)):
            runner(tmp_path, BoundClient(service), handler)
        release.set()
        with pytest.raises(asyncio.CancelledError): await closing
        with pytest.raises(ExecutionEnded): await observer
        assert (tmp_path / 'done').read_text() == 'accepted disk write'
        assert not (await service.poll('consumer', 'run'))['pending_tools']
    finally:
        release.set()
        await owned.close()
        await service.close()


@pytest.mark.asyncio
async def test_tool_failure_is_not_falsely_replied_or_replayed(tmp_path):
    edits = []
    async def engine(spec, invoke):
        return await invoke('files', 'edit', {'text': 'x'})
    async def handler(*args):
        edits.append('partial write')
        raise RuntimeError('executor teardown failed')
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = runner(tmp_path, BoundClient(service), handler)
    try:
        with pytest.raises(ExecutionRecoveryRequired): await owned.run('run', SPEC)
        with pytest.raises(ExecutionRecoveryRequired): await owned.close()
        assert (await service.poll('consumer', 'run'))['pending_tools'] == 1
        reopened = runner(tmp_path, BoundClient(service), handler)
        with pytest.raises(ExecutionRecoveryRequired): await reopened.run('run', SPEC)
        with pytest.raises(ExecutionRecoveryRequired): await reopened.close()
        assert edits == ['partial write']
    finally:
        await service.close()


def test_crash_after_effect_keeps_unknown_receipt_and_fences_other_bindings(tmp_path):
    code = '''import os, sys
from pathlib import Path
from pantheon.apps.agent_execution_runner import ToolReceiptJournal
root=Path(sys.argv[1])
j=ToolReceiptJournal(root/'caller','binding')
j.prepare('run','{}')
j.call('run',{'id':'call','provider':'files','name':'edit','args':{}})
j.update_call('run','call','executing')
(root/'effect').write_text('written before crash')
os._exit(17)
'''
    process = subprocess.run([sys.executable, '-c', code, str(tmp_path)], cwd=Path(__file__).resolve().parents[1])
    assert process.returncode == 17
    assert (tmp_path / 'effect').read_text() == 'written before crash'
    with pytest.raises(ValueError, match='another execution binding'):
        ToolReceiptJournal(tmp_path / 'caller', 'other-binding')
    journal = ToolReceiptJournal(tmp_path / 'caller', 'binding')
    try:
        with pytest.raises(ExecutionRecoveryRequired): journal.prepare('run', '{}')
        if os.name == 'posix': assert journal.path.stat().st_mode & 0o777 == 0o600
    finally:
        journal.close()


def test_import_does_not_pull_agent_or_platform_authority(tmp_path):
    code = '''import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if name.startswith(('pantheon.agent','pantheon.chatroom','pantheon.settings','pantheon.models','pantheon.platform','litellm','openai')):
   raise AssertionError(name)
sys.meta_path.insert(0,Guard())
from pantheon.apps.agent_execution_runner import ToolReceiptJournal, AgentExecutionRunner
j=ToolReceiptJournal(sys.argv[1],'binding'); j.close()
'''
    subprocess.run([sys.executable, '-c', code, str(tmp_path / 'caller')], check=True,
                   cwd=Path(__file__).resolve().parents[1])


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_state', ['executing', 'completed'])
async def test_persistence_failure_prevents_unsafe_work_or_success(tmp_path, monkeypatch, fail_state):
    effects = []
    async def engine(spec, invoke):
        return await invoke('files', 'edit', {'text': 'x'})
    async def handler(*args):
        effects.append('edited')
        return {'ok': True, 'value': 'saved'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = runner(tmp_path, BoundClient(service), handler)
    update = owned.journal.update_call
    def fail(run, identity, state, response=None):
        if state == fail_state: raise OSError('disk unavailable')
        return update(run, identity, state, response)
    monkeypatch.setattr(owned.journal, 'update_call', fail)
    try:
        with pytest.raises(ExecutionRecoveryRequired): await owned.run('run', SPEC)
        assert effects == ([] if fail_state == 'executing' else ['edited'])
        assert (await service.poll('consumer', 'run'))['pending_tools'] == 1
        with pytest.raises(ExecutionRecoveryRequired): await owned.close()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_declared_tool_failure_is_a_settled_reply(tmp_path):
    async def engine(spec, invoke):
        with pytest.raises(RuntimeError, match='invalid code'):
            await invoke('files', 'edit', {'text': 'x'})
        return {'content': 'Handled failure'}
    async def handler(*args):
        return {'ok': False, 'error': 'invalid code'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = runner(tmp_path, BoundClient(service), handler)
    try:
        assert await owned.run('run', SPEC) == {'content': 'Handled failure'}
        assert not (await service.poll('consumer', 'run'))['pending_tools']
    finally:
        await owned.close()
        await service.close()


@pytest.mark.asyncio
async def test_release_discards_retained_bodies_but_never_allows_replay(tmp_path):
    calls = []
    async def engine(spec, invoke):
        calls.append('model')
        return {'content': 'x' * 100000}
    async def handler(*args): raise AssertionError('No tool expected')
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = runner(tmp_path, BoundClient(service), handler)
    try:
        await owned.run('run', SPEC)
        await owned.release('run')
        assert not owned._runs and not owned._tools and not owned._callbacks
        with pytest.raises(ExecutionEnded): await owned.run('run', SPEC)
        await owned.close()
        reopened = runner(tmp_path, BoundClient(service), handler)
        with pytest.raises(ExecutionEnded) as error: await reopened.run('run', SPEC)
        assert error.value.state == 'released'
        await reopened.close()
        assert calls == ['model']
        assert (await service.poll('consumer', 'run'))['state'] == 'released'
    finally:
        await owned.close()
        await service.close()


@pytest.mark.asyncio
async def test_withdrawn_unclaimed_tool_can_be_released(tmp_path):
    async def engine(spec, invoke):
        return await invoke('files', 'edit', {'text': 'x'})
    async def handler(*args): raise AssertionError('Withdrawn tool must not execute')
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    class StoppingClient(BoundClient):
        async def claim(self, *args):
            await service.cancel('consumer', args[0])
            await observe(service)
            return await service.claim('consumer', *args)
    owned = runner(tmp_path, StoppingClient(service), handler)
    try:
        with pytest.raises(ExecutionEnded): await owned.run('run', SPEC)
        await owned.release('run')
    finally:
        await owned.close()
        await service.close()


@pytest.mark.asyncio
async def test_real_disk_thread_remains_owned_until_close_finishes(tmp_path):
    from pantheon.utils.owned_io import run_owned_io
    entered, release = threading.Event(), threading.Event()
    async def engine(spec, invoke):
        return await invoke('files', 'edit', {'text': 'x'})
    def write():
        entered.set()
        assert release.wait(5)
        (tmp_path / 'written').write_text('completed')
        return 'saved'
    async def handler(*args):
        return {'ok': True, 'value': await run_owned_io(write)}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = runner(tmp_path, BoundClient(service), handler)
    task = asyncio.create_task(owned.run('run', SPEC))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        closing = asyncio.create_task(owned.close())
        await asyncio.sleep(.03)
        assert not closing.done() and not (tmp_path / 'written').exists()
        release.set()
        await closing
        with pytest.raises(ExecutionEnded): await task
        assert (tmp_path / 'written').read_text() == 'completed'
    finally:
        release.set()
        await owned.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['missing-receipt', 'wrong-worker'])
async def test_unconfirmed_claim_never_authorizes_a_tool(tmp_path, corruption):
    effects = []
    async def engine(spec, invoke):
        return await invoke('files', 'edit', {'text': 'x'})
    async def handler(*args):
        effects.append('bad')
        return {'ok': True, 'value': 'bad'}
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    class CorruptClient(BoundClient):
        async def claim(self, *args):
            response = await service.claim('consumer', *args)
            if corruption == 'missing-receipt': return {}
            response['call']['worker_id'] = 'other-worker'
            return response
    owned = runner(tmp_path, CorruptClient(service), handler)
    try:
        with pytest.raises(ExecutionRecoveryRequired): await owned.run('run', SPEC)
        with pytest.raises(ExecutionRecoveryRequired): await owned.close()
        assert not effects
        assert (await service.poll('consumer', 'run'))['pending_tools'] == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_unexpected_callback_cancellation_does_not_claim_effects_stopped(tmp_path):
    async def engine(spec, invoke):
        return await invoke('files', 'edit', {'text': 'x'})
    async def handler(*args):
        raise asyncio.CancelledError
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    owned = runner(tmp_path, BoundClient(service), handler)
    try:
        with pytest.raises(ExecutionRecoveryRequired): await owned.run('run', SPEC)
        with pytest.raises(ExecutionRecoveryRequired): await owned.close()
        assert (await service.poll('consumer', 'run'))['pending_tools'] == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_release_observer_loss_is_joined_by_close(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def engine(spec, invoke): return {'content': 'archived'}
    async def handler(*args): raise AssertionError('No tool expected')
    service = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    class SlowClient(BoundClient):
        async def release(self, run):
            entered.set()
            await release.wait()
            return await service.release('consumer', run)
    owned = runner(tmp_path, SlowClient(service), handler)
    try:
        await owned.run('run', SPEC)
        observer = asyncio.create_task(owned.release('run'))
        await entered.wait()
        observer.cancel()
        with pytest.raises(asyncio.CancelledError): await observer
        closing = asyncio.create_task(owned.close())
        await asyncio.sleep(.01)
        assert not closing.done()
        release.set()
        await closing
        assert not owned._runs
        assert (await service.poll('consumer', 'run'))['state'] == 'released'
    finally:
        release.set()
        await owned.close()
        await service.close()
