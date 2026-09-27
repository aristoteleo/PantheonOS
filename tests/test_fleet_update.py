import asyncio
from datetime import datetime, timezone

from pantheon.apps.builtin.fleet.update import update_nodes


def record(node_id, kind='machine', self_update=True, seen=True, version='0.5.0-model.6'):
    runtimes = {'self-update': '1'} if self_update else {}
    return {'node_id': node_id, 'name': node_id, 'kind': kind, 'version': version,
            'last_seen': datetime.now(timezone.utc).isoformat() if seen else '2020-01-01T00:00:00Z',
            'capability': {'runtimes': runtimes}, 'state': {'status': 'online'}}


class Client:
    def __init__(self):
        self.sent = []

    async def self_update(self, node_id, tag):
        self.sent.append((node_id, tag))
        return {'status': 'deferred', 'reason': '1 task(s) or transfer(s) in progress'} if node_id == 'busy' else {'status': 'updated'}


class Resolver:
    def __init__(self, records):
        self.records, self._client = records, Client()

    async def _ensure_client(self):
        pass

    async def _list_nodes(self, max_age=0):
        return self.records


def test_updates_only_online_machine_nodes_that_can_update_themselves():
    resolver = Resolver([record('mac'), record('busy'), record('old', self_update=False, version='0.5.0-model.4'),
                         record('gone', seen=False), record('sandbox', kind='sandbox')])
    out = asyncio.run(update_nodes(resolver, tag='fleet-v0.5.0-model.7'))
    status = {n['node_id']: n['status'] for n in out['nodes']}
    assert status == {'mac': 'updated', 'busy': 'deferred', 'old': 'manual', 'gone': 'skipped'}
    assert resolver._client.sent == [('mac', 'fleet-v0.5.0-model.7'), ('busy', 'fleet-v0.5.0-model.7')]


def test_rejects_nodes_outside_the_fleet_and_missing_release(monkeypatch):
    resolver = Resolver([record('mac')])
    try:
        asyncio.run(update_nodes(resolver, ['other'], 'fleet-v0.5.0-model.7'))
    except ValueError:
        pass
    else:
        raise AssertionError('accepted a foreign node')
    monkeypatch.delenv('FLEET_CONTROLLER_URL', raising=False)
    try:
        asyncio.run(update_nodes(resolver))
    except RuntimeError as exc:
        assert 'No Fleet release' in str(exc)
    else:
        raise AssertionError('updated without a release')
    assert resolver._client.sent == []
