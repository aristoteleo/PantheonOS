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
        self.fences = []

    async def request(self, node, body=None, *, fence=False):
        def send():
            request = Request(self.endpoint + '/' + node,
                              data=json.dumps(body).encode() if body is not None else None,
                              headers={'Content-Type': 'application/json'},
                              method='PUT' if fence else 'POST' if body is not None else 'GET')
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

    async def fence_start(self, node, request):
        self.fences.append((node, request))
        result = await self.request(node, request, fence=True)
        if self.scenario == 'lost-before-prepare':
            raise ConnectionError('discard committed cancellation acknowledgement')
        return result


async def main(endpoint, raw_targets, path, scenario):
    transport = Transport(endpoint, scenario)
    journal = GroupJournal(path, 'test-owner')
    journal.create('test', json.loads(raw_targets))
    coordinator = GroupCoordinator(journal, transport)
    deadline = time.monotonic() + 25
    saw_ready, restarted = False, False
    delayed = None
    if scenario == 'lost-before-prepare':
        row = journal.load('test')
        row['members'][0]['prepare']['sent'] = True
        delayed = row['members'][0]['prepare']['request']
        journal.save(row)
        coordinator = GroupCoordinator(GroupJournal(path, 'test-owner'), transport)
        restarted = True
        coordinator.stop('test')
    while time.monotonic() < deadline:
        row = await coordinator.advance('test')
        if row['phase'] == 'committing' and not restarted:
            if scenario == 'lost-before-start':
                row['members'][0]['start']['sent'] = True
                delayed = row['members'][0]['start']['request']
                journal.save(row)
            coordinator = GroupCoordinator(GroupJournal(path, 'test-owner'), transport)
            restarted = True
            if scenario == 'lost-before-start':
                coordinator.stop('test')
        if row['phase'] == 'ready':
            saw_ready = True
            coordinator.stop('test')
            if scenario == 'lost-before-stop':
                row = journal.load('test')
                target = row['members'][0]['target']
                row['members'][0]['stop'] = dict(sent=True, request=dict(protocol=1, action='stop',
                    operation_id='interrupted-stop', digest=target['digest'], scope=target['scope'], generation=2))
                journal.save(row)
                coordinator = GroupCoordinator(GroupJournal(path, 'test-owner'), transport)
        if row['phase'] == 'stopped':
            assert saw_ready == (scenario in {'lost-reply', 'lost-before-stop'}), row
            expected = {'failed-admission': 3, 'lost-before-prepare': 0, 'lost-before-start': 4}.get(scenario, 6)
            assert len(transport.sent) == expected, transport.sent
            assert len({r['operation_id'] for _, r in transport.sent}) == len(transport.sent)
            if delayed:
                result = await transport.submit('node-a', **{k: v for k, v in delayed.items() if k != 'protocol'})
                assert result['state'] == 'cancelled', result
                assert len(transport.fences) == 1
            print(json.dumps(dict(scenario=scenario, phase=row['phase'], saw_ready=saw_ready,
                                  submissions=len(transport.sent), fences=len(transport.fences),
                                  delayed_request_cancelled=bool(delayed), restored_journal=restarted)))
            return
        await asyncio.sleep(.025)
    raise AssertionError(row)


if __name__ == '__main__':
    asyncio.run(main(*sys.argv[1:]))
