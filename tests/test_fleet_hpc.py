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
