import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from pantheon.apps.agent_execution_client import AgentExecutionClient
from pantheon.apps.dependency_client import DependencyClient, DependencyCallError
from pantheon.apps.runtime_config import RuntimeCredential


def client():
    return AgentExecutionClient(DependencyClient(RuntimeCredential('https://bound.example/rpc', 'a' * 64)))


@pytest.mark.asyncio
async def test_sdk_preserves_ids_does_not_choose_owner_and_reassembles_result(monkeypatch):
    raw = json.dumps({'content': '数据' * 40000}).encode()
    calls = []
    def invoke(self, method, args, **options):
        calls.append((method, args, options))
        assert 'consumer_id' not in args
        if method == 'agent_execution_read_result':
            offset = args['offset']
            part = raw[offset:offset + 32768]
            value = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'offset': offset,
                     'next_offset': offset + len(part), 'data': base64.b64encode(part).decode()}
        else:
            value = {'accepted': True}
        return {'success': True, 'result': value}
    monkeypatch.setattr(DependencyClient, 'invoke', invoke)
    sdk = client()
    await sdk.submit('stable-run', {'prompt': 'test'})
    await sdk.claim('stable-run', 'call', 'stable-worker')
    await sdk.reply('stable-run', 'call', 'stable-worker', {'ok': True, 'value': 3})
    assert await sdk.read_result('stable-run') == {'content': '数据' * 40000}
    assert calls[0][1]['execution_id'] == 'stable-run'
    assert calls[1][1]['worker_id'] == calls[2][1]['worker_id'] == 'stable-worker'
    await sdk.close()
    with pytest.raises(RuntimeError, match='closed'): await sdk.poll('stable-run')


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['digest', 'offset', 'empty', 'size', 'changed'])
async def test_corrupt_or_changed_result_is_not_returned(monkeypatch, corruption):
    raw = b'x' * 33000
    def invoke(self, method, args, **kwargs):
        offset = args['offset']
        part = raw[offset:offset + 32768]
        value = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'offset': offset,
                 'next_offset': offset + len(part), 'data': base64.b64encode(part).decode()}
        if corruption == 'digest': value['sha256'] = '0' * 64
        if corruption == 'offset': value['offset'] = 1
        if corruption == 'empty': value['data'] = ''
        if corruption == 'size': value['size'] = 20 * 1024 * 1024
        if corruption == 'changed' and offset: value['sha256'] = '0' * 64
        return {'success': True, 'result': value}
    monkeypatch.setattr(DependencyClient, 'invoke', invoke)
    sdk = client()
    try:
        with pytest.raises(ValueError, match='malformed|changed'): await sdk.read_result('run')
    finally:
        await sdk.close()


@pytest.mark.asyncio
async def test_cancelled_observer_joins_transport_and_never_retries(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def invoke(self, *args, **kwargs):
        calls.append(args); entered.set()
        assert release.wait(5)
        return {'success': True, 'result': {'state': 'running'}}
    monkeypatch.setattr(DependencyClient, 'invoke', invoke)
    sdk = client()
    observer = asyncio.create_task(sdk.submit('run', {}))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        observer.cancel()
        await asyncio.sleep(.02)
        assert not observer.done()
        shutdown = asyncio.create_task(sdk.close())
        release.set()
        with pytest.raises(asyncio.CancelledError): await observer
        await shutdown
        assert len(calls) == 1
    finally:
        release.set()
        await sdk.close()


@pytest.mark.asyncio
async def test_ambiguous_submit_is_not_replayed(monkeypatch):
    calls = []
    def invoke(self, *args, **kwargs):
        calls.append(args)
        raise DependencyCallError('lost response', outcome_unknown=True)
    monkeypatch.setattr(DependencyClient, 'invoke', invoke)
    sdk = client()
    with pytest.raises(DependencyCallError) as error:
        await sdk.submit('durable-id', {})
    assert error.value.outcome_unknown and len(calls) == 1
    await sdk.close()


def test_consumer_imports_no_agent_settings_model_or_platform_runtime(tmp_path):
    code = '''import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if name.startswith(('pantheon.agent','pantheon.chatroom','pantheon.settings','pantheon.models','pantheon.platform','litellm','openai')):
   raise AssertionError('Execution consumer imported runtime authority: '+name)
sys.meta_path.insert(0,Guard())
from pantheon.apps.agent_execution_client import AgentExecutionClient, METHODS
from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.runtime_config import RuntimeCredential
assert len(METHODS)==7
AgentExecutionClient(DependencyClient(RuntimeCredential('https://bound.example/rpc','a'*64)))
'''
    root = Path(__file__).resolve().parents[1]
    process = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
        env={**os.environ, 'PYTHONPATH': str(root)}, capture_output=True, text=True, timeout=10)
    assert process.returncode == 0, process.stderr


def test_execution_grant_projects_the_actual_rpc_contract_and_binds_its_owner():
    from pantheon.apps.agent_execution_client import METHODS, execution_method_rules
    from pantheon.apps.dependency_assembly import _methods
    from pantheon.apps.reflect import reflect_toolset_class
    from pantheon.chatroom.native import NativeAgentApplication
    signatures = [s.model_dump() for s in reflect_toolset_class(NativeAgentApplication) if s.name in METHODS]
    provider = {'provides': {'tools': signatures, 'interfaces': [
        {'name': 'agent-execution', 'version': 1, 'tools': list(METHODS)}]}}
    rules = execution_method_rules('logical-worker')
    assert _methods({'uses': ['agent-execution@1']}, provider, rules) == rules
    for rule in rules.values():
        assert rule['bound'] == {'consumer_id': 'logical-worker'}
        assert 'consumer_id' not in rule['arguments']
