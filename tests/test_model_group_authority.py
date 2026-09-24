"""Authority RPC routing and exact public response validation.

Real CA, durable recovery, permission, and TLS coverage lives in Fleet Go tests.
"""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.models.group_network import PeerTopology


def topology():
    return dict(protocol=1, owner='f_' + 'a' * 16, group_id='test',
                model_sha256='b' * 64, launch_sha256='c' * 64,
                members=[dict(rank=i, node_id=f'node-{i}', generation=2,
                              address=f'10.20.0.{i+1}', port=18400) for i in range(2)])


@pytest.mark.asyncio
async def test_pinned_leader_prepare_and_terminal_status(monkeypatch):
    peers = PeerTopology(topology())
    service = FleetLifecycle(None)
    reply = dict(protocol=1, owner=topology()['owner'], node_id='node-0',
                 group_id='test', topology_sha256=peers.fingerprint, state='open')
    rpc = AsyncMock(return_value=reply)
    monkeypatch.setattr(service, '_request', rpc)
    assert await service.group_authority('node-0', 'prepare', topology=topology()) == reply
    rpc.assert_awaited_once_with('node-0', 'group_authority_prepare', group_topology=peers.document())
    for kwargs in [dict(topology=topology(), group_id='test'), dict(topology=topology(), claim={})]:
        with pytest.raises(ValueError):
            await service.group_authority('node-0', 'prepare', **kwargs)
    with pytest.raises(ValueError, match='rank-zero'):
        await service.group_authority('node-1', 'prepare', topology=topology())
    assert rpc.await_count == 1
    args = dict(group_id='test', topology_sha256=peers.fingerprint)
    rpc.return_value = {**reply, 'state': 'closed'}
    for action in ('close', 'status'):
        assert (await service.group_authority('node-0', action, **args))['state'] == 'closed'
        rpc.assert_awaited_with('node-0', 'group_authority_' + action, **args)
    for field, value in [('protocol', True), ('group_id', 'other'), ('topology_sha256', 'd' * 64),
                         ('node_id', 'node-1'), ('state', 'ready'), ('owner', 'f_' + 'b' * 16)]:
        rpc.return_value = {**reply, field: value}
        with pytest.raises(RuntimeError):
            await service.group_authority('node-0', 'prepare', topology=topology())


@pytest.mark.asyncio
async def test_issue_preserves_binding_and_rejects_wrong_certificate_response(monkeypatch):
    service = FleetLifecycle(None)
    args = dict(group_id='test', topology_sha256=PeerTopology(topology()).fingerprint)
    claim = dict(rank=1, node_id='node-1', instance_id='d' * 32, revision='e' * 64,
                 scope='group', generation=2, preparation_id='prepared', csr_pem='public CSR')
    reply = dict(protocol=1, **args, **{k: claim[k] for k in
                 ('rank', 'node_id', 'instance_id', 'revision', 'generation')},
                 certificate_pem='public leaf', ca_pem='public root')
    rpc = AsyncMock(return_value=reply)
    monkeypatch.setattr(service, '_request', rpc)
    assert await service.group_authority('node-0', 'issue', claim=claim, **args) == reply
    rpc.assert_awaited_once_with('node-0', 'group_authority_issue', group_claim=claim, **args)
    for field, value in [('rank', True), ('rank', 2), ('node_id', 'node-2'), ('generation', 3),
                         ('instance_id', 'f' * 32), ('revision', 'a' * 64),
                         ('certificate_pem', ''), ('ca_pem', 'x' * 16385)]:
        rpc.return_value = {**reply, field: value}
        with pytest.raises(RuntimeError):
            await service.group_authority('node-0', 'issue', claim=claim, **args)
    count = rpc.await_count
    for field, value in [('rank', True), ('rank', 16), ('generation', True), ('generation', 2**63),
                         ('scope', '../key'), ('preparation_id', ''), ('revision', None),
                         ('csr_pem', ''), ('csr_pem', 'x' * 16385)]:
        with pytest.raises(ValueError):
            await service.group_authority('node-0', 'issue', claim={**claim, field: value}, **args)
    with pytest.raises(ValueError):
        await service.group_authority('node-0', 'issue', claim={**claim, 'key_pem': 'forbidden'}, **args)
    for action in ('status', 'close', 'rotate'):
        with pytest.raises(ValueError):
            await service.group_authority('node-0', action, claim=deepcopy(claim), **args)
    assert rpc.await_count == count
