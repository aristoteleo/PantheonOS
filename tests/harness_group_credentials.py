"""Real owner-scoped NATS/Fleet credential barrier, invoked by the Go fixture.

Only the Go fixture's temporary two-Runner state and CPU processes are used.
Actual CA/CSR/leaf data pass over authenticated RPC; none are printed or journaled.
"""
import asyncio
import inspect
import json
import os
from pathlib import Path
import sys
import time

import nats

from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.models.group_coordinator import GroupCoordinator
from pantheon.models.group_journal import GroupJournal


async def run(url, credentials, path, raw):
    config = json.loads(raw)
    nc = await nats.connect(url, user_credentials=credentials,
                            inbox_prefix=('_INBOX_' + config['owner']).encode())

    class Transport(FleetLifecycle):
        def __init__(self):
            super().__init__(None)
            self.dropped = set()
            self.starts = 0
            self.installed = set()

        async def _request(self, node, method, **data):
            message = await nc.request(f'fleet.{config["owner"]}.node.{node}.cmd',
                json.dumps(dict(protocol=1, type='app_lifecycle', method=method, **data)).encode(), timeout=5)
            result = json.loads(message.data)
            if result.get('error'):
                raise RuntimeError(result['error'])
            if method == 'group_peer_install':
                self.installed.add(node)
            if method == 'submit' and data['request']['action'] == 'start':
                assert len(self.installed) == 2, 'start crossed an incomplete certificate barrier'
                self.starts += 1
            # Discard one real issue/install/close acknowledgement per binding.
            key = (node, method, data.get('group_claim', {}).get('rank'))
            if method in {'group_authority_issue', 'group_peer_install', 'group_authority_close'} and key not in self.dropped:
                self.dropped.add(key)
                raise ConnectionError('discarded real persisted acknowledgement')
            return result

    transport = Transport()

    async def exercise(_endpoint, _targets, journal_path, _scenario, journal_factory=None):
        async def reopen():
            return await journal_factory() if journal_factory else GroupJournal(journal_path, config['owner'])

        async def store(method, *args, **kwargs):
            value = getattr(journal, method)(*args, **kwargs)
            return await value if inspect.isawaitable(value) else value

        journal = await reopen()
        await store('create', 'test', config['targets'], peer_security=config['peer_security'])
        coordinator = GroupCoordinator(journal, transport, rpc_timeout=5)
        restarted = ready = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            row = await coordinator.advance('test')
            if row['phase'] == 'committing' and not restarted:
                assert row['peer_security']['ready'] and transport.starts == 0
                journal = await reopen()
                coordinator = GroupCoordinator(journal, transport, rpc_timeout=5)
                restarted = True
            if row['phase'] == 'ready':
                ready = True
                assert transport.starts == 2
                assert all(m['observation']['state'] == 'ready' for m in row['members'])
                resources = []
                for target in config['targets']:
                    snapshot = await transport.status(target['node_id'])
                    for instance in snapshot['instances'].values():
                        resources.extend(instance.get('resources') or [])
                assert len(resources) == 2 and all(r.get('pid', 0) > 0 for r in resources)
                Path(path + '.resources.json').write_text(json.dumps(resources))
                await coordinator.stop('test')
            if row['phase'] == 'stopped':
                assert ready and restarted and row['peer_security']['closed']
                assert all(m['observation']['clean'] for m in row['members'])
                assert 'csr_pem' not in json.dumps(row) and 'certificate_pem' not in json.dumps(row)
                print(json.dumps(dict(phase='stopped', real_nats=True, real_node_issuance=True,
                    native_processes=transport.starts, dropped_acknowledgements=len(transport.dropped),
                    coordinator_restarted=restarted, credential_barrier=True,
                    authority_closed=True, resources_released=True)))
                return
            await asyncio.sleep(.025)
        raise AssertionError('credential coordinator deadline expired: ' + row['phase'])

    try:
        if os.environ.get('PANTHEON_GROUP_HUB_SOURCE'):
            from harness_model_groups_hub import run as with_hub
            await with_hub(url, '', path, 'credentials', driver=exercise)
        else:
            await exercise(url, '', path, 'credentials')
    finally:
        await nc.close()


if __name__ == '__main__':
    asyncio.run(run(*sys.argv[1:]))
