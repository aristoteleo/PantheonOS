"""Restricted allocation RPC, using fault-injected owner services and transport.

The separate Go controller integration verifies actual gateway authentication,
bound-policy injection, and native App processes over authenticated NATS/TLS.
"""
import asyncio
import threading
from unittest.mock import patch

import pytest

from pantheon.apps.dependency_binding_client import RemoteDependencyBindings
from pantheon.apps.dependency_binding_service import DependencyBindingService
from pantheon.apps.dependency_client import DependencyClient, DependencyCallError
from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.runtime_config import RuntimeCredential
from test_live_dependency_bindings import fixture, request


def client(**kwargs):
    return RemoteDependencyBindings(DependencyClient(RuntimeCredential('https://broker.apps.test/rpc', 'a'*64)), **kwargs)


def receipt(**overrides):
    return {'protocol': 1, 'owner_ref': 'instance-one', 'operation_id': 'revision-one',
            'consumer': {}, 'bindings': {'shell': {}, 'files': {}}, **overrides}


@pytest.mark.asyncio
async def test_service_pins_consumer_and_policy_not_caller_configuration(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    policies = {'deployment-one': {'consumer': f.consumer, 'bindings': f.bindings}}
    service = DependencyBindingService(f.owner, policies=policies)
    policies['deployment-one']['bindings']['files']['methods']['read']['bound']['workspace'] = 'changed'
    result = await service.bind_dependencies(policy_id='deployment-one', **request())
    assert result['consumer'] == f.consumer
    assert f.authority.issue.await_args_list[0].args[0]['methods']['read']['bound']['workspace'] == 'project-a'
    before = len(f.issued)
    for args in [dict(policy_id='unknown'), dict(policy_id=['deployment-one']),
                 dict(policy_id='deployment-one', aliases=['other'])]:
        with pytest.raises(AssemblyError):
            await service.bind_dependencies(**{**request(operation='second'), **args})
    with pytest.raises(TypeError):
        await service.bind_dependencies(policy_id='deployment-one', consumer=f.consumer, **request())
    assert len(f.issued) == before


@pytest.mark.asyncio
async def test_service_lost_reply_reuses_owner_operation_and_hides_upstream_secrets(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    service = DependencyBindingService(f.owner, policies={'deployment': {'consumer': f.consumer, 'bindings': f.bindings}})
    original = f.authority.issue.side_effect
    async def lost(body):
        original(body)
        f.authority.issue.side_effect = original
        raise RuntimeError('https://owner-secret:secret-token@management.test')
    f.authority.issue.side_effect = lost
    with pytest.raises(AssemblyError) as error:
        await service.bind_dependencies(policy_id='deployment', **request())
    assert 'secret' not in str(error.value) and 'management.test' not in str(error.value)
    value = await service.bind_dependencies(policy_id='deployment', **request())
    assert (await service.bind_dependencies(policy_id='deployment', **request())) == value
    assert len(f.receipts) == 1 and len(f.issued) == 2


@pytest.mark.asyncio
async def test_remote_request_cannot_send_policy_or_placement():
    cap = client()
    def invoke(_client, method, args, *, timeout_seconds):
        assert method == 'bind_dependencies'
        assert args == {**request(), 'aliases': ['files', 'shell']}
        assert timeout_seconds == 60
        return {'success': True, 'result': receipt()}
    with patch.object(DependencyClient, 'invoke', invoke):
        assert await cap.bind(**request()) == receipt()
        with pytest.raises(TypeError):
            await cap.bind(**request(), policy_id='other')
    await cap.shutdown()
    with pytest.raises(RuntimeError, match='stopping'):
        await cap.bind(**request())


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(aliases=[]), dict(aliases=['a', 'a']),
    dict(aliases=['bad/name']), dict(aliases=[{}]), dict(aliases=['a']*17),
    dict(owner_ref='../escape'), dict(owner_ref=None), dict(operation_id='bad/operation')])
async def test_remote_rejects_malformed_request_before_transport(changes):
    with patch.object(DependencyClient, 'invoke', side_effect=AssertionError('transport must not run')):
        with pytest.raises(ValueError):
            await client().bind(**{**request(), **changes})


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [None, {'success': False, 'error': 'secret-token'},
    {'success': True, 'result': receipt(owner_ref='other')},
    {'success': True, 'result': receipt(operation_id='other')},
    {'success': True, 'result': receipt(protocol=True)},
    {'success': True, 'result': receipt(bindings={'shell': {}})},
    {'success': True, 'result': receipt(extra='secret-token')}])
async def test_remote_failed_or_mismatched_receipt_is_unknown_and_not_retried(response):
    with patch.object(DependencyClient, 'invoke', return_value=response) as invoke:
        with pytest.raises(DependencyCallError) as error:
            await client().bind(**request())
        assert invoke.call_count == 1 and error.value.outcome_unknown
        assert 'secret-token' not in str(error.value)


@pytest.mark.asyncio
async def test_cancellation_and_shutdown_drain_accepted_transport_and_reject_queued_calls():
    entered, release = threading.Event(), threading.Event()
    cap = client(max_inflight=1)
    def invoke(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {'success': True, 'result': receipt()}
    with patch.object(DependencyClient, 'invoke', invoke):
        active = asyncio.create_task(cap.bind(**request()))
        assert await asyncio.to_thread(entered.wait, 5)
        waiting = asyncio.create_task(cap.bind(**request(operation='queued')))
        await asyncio.sleep(0)
        active.cancel()
        stopping = asyncio.create_task(cap.shutdown())
        await asyncio.sleep(.02)
        active.cancel()
        assert not active.done() and not stopping.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await active
        await stopping
        with pytest.raises(RuntimeError, match='stopping'):
            await waiting
