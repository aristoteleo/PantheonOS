"""Production Python client to real Go durable enrollment; no kernel/GPU work."""
import asyncio
import json
import sys
from urllib.request import Request, urlopen

from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.models.group_network import PeerTopology


class Remote(FleetLifecycle):
    async def _request(self, node, method, **data):
        def request():
            req = Request(sys.argv[1] + '/' + node, data=json.dumps(dict(method=method, **data)).encode(),
                          headers={'Content-Type': 'application/json'})
            with urlopen(req, timeout=5) as response:
                result = json.load(response)
            if result.get('error'):
                raise RuntimeError(result['error'])
            return result
        return await asyncio.to_thread(request)


async def main():
    client = Remote(None)
    topology = dict(protocol=1, owner='f_'+'a'*16, group_id='original',
        model_sha256='b'*64, launch_sha256='c'*64,
        members=[dict(rank=i, node_id=f'node-{i}', generation=2, address=f'10.20.0.{i+1}', port=18400)
                 for i in range(2)])
    replies = []
    for rank in range(2):
        args = dict(topology=topology, rank=rank, ca_sha256='d'*64, address=f'192.168.20.{rank+10}:18441')
        result = await client.group_overlay(f'node-{rank}', 'prepare', **args)
        assert await client.group_overlay(f'node-{rank}', 'prepare', **args) == result
        replies.append(result)
    roster = [r['endpoint'] for r in replies]
    assert len({e['public_key'] for e in roster}) == 2
    for rank in range(2):
        result = await client.group_overlay(f'node-{rank}', 'pin', topology=topology, endpoints=roster)
        assert result['endpoints'] == roster and result['topology_sha256'] == PeerTopology(topology).fingerprint
        assert await client.group_overlay(f'node-{rank}', 'status', topology=topology) == result
        changed = [dict(e) for e in roster]
        changed[1-rank]['address'] = '192.168.20.99:18441'
        try:
            await client.group_overlay(f'node-{rank}', 'pin', topology=topology, endpoints=changed)
        except RuntimeError:
            pass
        else:
            raise AssertionError('node allowed a changed original roster')
        assert (await client.group_overlay(f'node-{rank}', 'close', topology=topology))['state'] == 'closed'
    cancelled = {**topology, 'group_id': 'cancelled'}
    await client.group_overlay('node-0', 'close', topology=cancelled)
    result = await client.group_overlay('node-0', 'prepare', topology=cancelled, rank=0,
        ca_sha256='d'*64, address='192.168.20.10:18441')
    assert result['state'] == 'closed' and 'endpoint' not in result
    print('Python/Go overlay enrollment: original keys, exact roster and close-before-prepare verified')


if __name__ == '__main__':
    asyncio.run(main())
