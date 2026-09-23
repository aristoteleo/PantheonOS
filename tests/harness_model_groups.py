"""Invoked only by TestPythonGroupCoordinatorNativeProcesses."""
import asyncio
import json
import sys
import time
from urllib.request import Request, urlopen

from pantheon.models.group_coordinator import GroupCoordinator
from pantheon.models.group_journal import GroupJournal


class Transport:
    def __init__(self, endpoint, scenario):
        self.endpoint, self.scenario = endpoint, scenario
        self.sent = []

    async def request(self, node, body=None):
        def send():
            request = Request(self.endpoint + '/' + node,
                              data=json.dumps(body).encode() if body is not None else None,
                              headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=5) as response:
                return json.load(response)
        return await asyncio.to_thread(send)

    async def status(self, node):
        return await self.request(node)

    async def submit(self, node, **request):
        self.sent.append((node, request))
        result = await self.request(node, dict(protocol=1, **request))
        if self.scenario == 'lost-reply':
            raise ConnectionError('discard acknowledgement after actual Fleet submit')
        return result


async def main(endpoint, raw_targets, path, scenario):
    transport = Transport(endpoint, scenario)
    journal = GroupJournal(path, 'test-owner')
    journal.create('test', json.loads(raw_targets))
    coordinator = GroupCoordinator(journal, transport)
    deadline = time.monotonic() + 25
    saw_ready, restarted = False, False
    while time.monotonic() < deadline:
        row = await coordinator.advance('test')
        if row['phase'] == 'committing' and not restarted:
            coordinator = GroupCoordinator(GroupJournal(path, 'test-owner'), transport)
            restarted = True
        if row['phase'] == 'ready':
            saw_ready = True
            coordinator.stop('test')
        if row['phase'] == 'stopped':
            assert saw_ready == (scenario == 'lost-reply'), row
            assert len(transport.sent) == (3 if scenario == 'failed-admission' else 6), transport.sent
            assert len({r['operation_id'] for _, r in transport.sent}) == len(transport.sent)
            print(json.dumps(dict(scenario=scenario, phase=row['phase'], saw_ready=saw_ready,
                                  submissions=len(transport.sent), restored_journal=restarted)))
            return
        await asyncio.sleep(.025)
    raise AssertionError(row)


if __name__ == '__main__':
    asyncio.run(main(*sys.argv[1:]))
