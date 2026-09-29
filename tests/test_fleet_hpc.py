import asyncio
from datetime import datetime, timezone

import pytest

from pantheon.apps.builtin.fleet import hpc


def record(node_id, launcher=True, online=True):
    return {'node_id': node_id, 'name': node_id, 'kind': 'machine',
            'last_seen': datetime.now(timezone.utc).isoformat() if online else '2020-01-01T00:00:00Z',
            'capability': {'runtimes': {'slurm-launcher': '1'} if launcher else {}}, 'state': {'status': 'online'}}


class Client:
    def __init__(self):
        self.sent = []

    async def hpc(self, node_id, method, data=None):
        self.sent.append((node_id, method, data))
        if method == 'submit':
            return {'job': {'job_id': str(len(self.sent)), 'node_name': data['request']['name']}}
        return {'jobs': []}


class Resolver:
    def __init__(self, records):
        self.records, self._client = records, Client()

    async def _ensure_client(self):
        pass

    async def _list_nodes(self, max_age=0):
        return self.records


def test_launch_mints_one_token_per_job(monkeypatch):
    tokens = iter(['jt_a', 'jt_b'])

    async def mint(ttl_minutes=hpc.TOKEN_TTL_MINUTES):
        assert ttl_minutes == 7 * 24 * 60
        return next(tokens)
    monkeypatch.setattr(hpc, 'mint_join_token', mint)
    resolver = Resolver([record('login')])
    out = asyncio.run(hpc.launch(resolver, 'login', partition='gpu', gpus=1, count=2))
    assert [j['job_id'] for j in out['jobs']] == ['1', '2']
    sent = [d['request'] for _, m, d in resolver._client.sent]
    assert {r['join_token'] for r in sent} == {'jt_a', 'jt_b'} and sent[0]['name'] == 'gpu-gpu'


@pytest.mark.parametrize('rec,msg', [(record('login', launcher=False), 'cannot start HPC'),
                                     (record('login', online=False), 'offline')])
def test_refuses_nodes_that_cannot_launch(rec, msg):
    with pytest.raises(RuntimeError, match=msg):
        asyncio.run(hpc.call(Resolver([rec]), 'login', 'jobs'))


def test_delegated_inventory_does_not_offer_unsupported_terminal_or_apps():
    from apps.fleet.inventory import node_inventory
    rec = record('hpc_123')
    rec['delegation'] = {'connector_id': 'mac', 'cluster_id': 'sherlock', 'job_id': '123', 'state': 'ready'}
    rec['capability'] = {'os': 'linux', 'caps': ['proc'], 'runtimes': {'hpc-files': '1'}}
    node = node_inventory([rec])['nodes'][0]
    assert node['delegation']['connector_id'] == 'mac'
    assert not node['can_start_pty']
    assert not node['has_files']  # The full Files app adapter is a later milestone.


def test_hpc_file_operation_cannot_be_overridden_by_extra_payload():
    from pantheon.apps.client import AppClient
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    import json
    nc = SimpleNamespace(request=AsyncMock(return_value=SimpleNamespace(data=b'{"entries":[]}')))
    asyncio.run(AppClient(nc, 'f').hpc_file('hpc_123', 'list', path='.'))
    subject, payload = nc.request.call_args.args
    assert subject == 'fleet.f.node.hpc_123.cmd'
    assert json.loads(payload) == {'type': 'hpc_file', 'file': {'operation': 'list', 'path': '.'}}
    with pytest.raises(ValueError):
        asyncio.run(AppClient(nc, 'f').hpc_file('hpc_123', 'task'))

@pytest.mark.parametrize('node_id,wait', [('hpc_123', 36), ('n_mac', 6)])
def test_task_rpc_allows_compute_step_startup(node_id, wait):
    from apps.fleet.fleet import FleetToolSet
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    tool = FleetToolSet.__new__(FleetToolSet)
    tool._ensure_connected = AsyncMock()
    tool._fleet_id = 'f'
    tool._nc = SimpleNamespace(request=AsyncMock(return_value=SimpleNamespace(data=b'{"exit_code":0}')))
    result = asyncio.run(tool.run_on_node(node_id, 'true', timeout=1))
    assert result['success']
    assert tool._nc.request.call_args.kwargs['timeout'] == wait
