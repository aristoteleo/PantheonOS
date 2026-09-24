"""Actual Python staging/client/coordinator → Go Manager/NativeDriver acceptance.

Only the authenticated transport is substituted with loopback HTTP. Artifacts,
staging, installation, resource preparation, processes and stop are real.
"""
import asyncio
from copy import deepcopy
import inspect
import json
import os
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.models.group_coordinator import GroupCoordinator
from pantheon.models.group_journal import GroupJournal
from pantheon.models.group_package import GroupPackageStore


class Remote(FleetLifecycle):
    def __init__(self, endpoint, scenario):
        super().__init__(None)
        self.endpoint, self.scenario = endpoint, scenario
        self.missing = scenario == 'install-before-delivery'
        self.calls = []

    async def _client(self, node):
        return self

    async def lifecycle(self, node, method, **data):
        def send():
            verb = {'status': 'GET', 'stage': 'PATCH', 'submit': 'POST', 'fence_start': 'PUT'}[method]
            body = None if method == 'status' else json.dumps(data if method == 'stage' else data['request']).encode()
            request = Request(self.endpoint+'/'+node, data=body, method=verb,
                              headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=5) as response:
                result = json.load(response)
            return {'operation': result} if method in {'submit', 'fence_start'} else result
        return await asyncio.to_thread(send)

    async def submit(self, node, **request):
        self.calls.append((node, deepcopy(request)))
        if node == 'node-a' and request['action'] == 'install' and self.missing:
            self.missing = False
            raise ConnectionError('dropped before delivery')
        result = await super().submit(node, **request)
        if self.scenario == 'install-lost-reply':
            raise ConnectionError('dropped after durable acceptance')
        return result


async def main(endpoint, raw_targets, path, scenario, journal_factory=None):
    packages = GroupPackageStore(Path(path).parent/'packages')
    remote = Remote(endpoint, scenario)

    async def reopen():
        return await journal_factory() if journal_factory else GroupJournal(path, 'test-owner')

    async def store(method, *args, **kwargs):
        result = getattr(journal, method)(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    journal = await reopen()
    await store('create', 'test', json.loads(raw_targets), install=True)
    coordinator = GroupCoordinator(journal, remote, packages=packages)
    row = await coordinator.advance('test')
    assert all(m['install']['staged'] and not m['install']['sent'] for m in row['members'])
    assert not remote.calls
    original = [deepcopy(m['install']['request']) for m in row['members']]
    delayed = []
    if scenario == 'install-cancel':
        for member in row['members']:
            member['install']['sent'] = True
            delayed.append((member['target']['node_id'], deepcopy(member['install']['request'])))
        await store('save', row)
    journal = await reopen()
    coordinator = GroupCoordinator(journal, remote, packages=packages)
    if delayed:
        await coordinator.stop('test')
    deadline, saw_ready = time.monotonic()+25, False
    while time.monotonic() < deadline:
        row = await coordinator.advance('test')
        if row['phase'] == 'ready':
            saw_ready = True
            await coordinator.stop('test')
        if row['phase'] == 'stopped':
            assert saw_ready == (not delayed)
            assert [m['install']['request'] for m in row['members']] == original
            for node, request in delayed:
                result = await remote.submit(node, **{k: v for k, v in request.items() if k != 'protocol'})
                assert result['state'] == 'cancelled', result
            installs = [r for _, r in remote.calls if r['action'] == 'install']
            assert len(installs) == (3 if scenario == 'install-before-delivery' else 2)
            assert len({r['operation_id'] for r in installs}) == 2
            print(json.dumps(dict(scenario=scenario, phase=row['phase'], real_processes_started=saw_ready,
                install_deliveries=len(installs), original_install_ids=2, restored_journal=True)))
            return
        await asyncio.sleep(.025)
    raise AssertionError(row)


if __name__ == '__main__':
    if os.environ.get('PANTHEON_GROUP_HUB_SOURCE'):
        from harness_model_groups_hub import run
        asyncio.run(run(*sys.argv[1:], driver=main))
    else:
        asyncio.run(main(*sys.argv[1:]))
