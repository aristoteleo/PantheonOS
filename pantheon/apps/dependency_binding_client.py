"""Consumer-side live binding capability over the ordinary dependency gateway.

Stdlib + the small dependency SDK only. No Agent/platform/Fleet discovery,
management credentials, policy upload, retries, or provider placement. A runtime
credential is delivered by the same prepared-start path as any App dependency.
The composition owns shutdown; closing this client does not retire resources.
"""
import asyncio
import re

from pantheon.apps.dependency_client import DependencyClient, DependencyCallError


async def _drain(task):
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


class RemoteDependencyBindings:
    def __init__(self, client: DependencyClient, *, max_inflight=8, timeout_seconds=60):
        if (not isinstance(client, DependencyClient) or type(max_inflight) is not int
                or not 1 <= max_inflight <= 64 or type(timeout_seconds) is not int
                or not 1 <= timeout_seconds <= 600):
            raise ValueError('Supply an explicit dependency allocation transport')
        self._client, self._timeout = client, timeout_seconds
        self._slots, self._pending, self._closed = asyncio.Semaphore(max_inflight), set(), False

    def _check_open(self):
        if self._closed:
            raise RuntimeError('Dependency allocation client is stopping')

    async def bind(self, *, owner_ref, operation_id, aliases):
        self._check_open()
        if (not isinstance(owner_ref, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', owner_ref)
                or not isinstance(operation_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', operation_id)
                or not isinstance(aliases, list) or not 1 <= len(aliases) <= 16
                or not all(isinstance(a, str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', a) for a in aliases)
                or len(set(aliases)) != len(aliases)):
            raise ValueError('Use stable allocation IDs and approved dependency aliases')
        args = dict(owner_ref=owner_ref, operation_id=operation_id, aliases=sorted(aliases))
        async with self._slots:
            self._check_open()
            task = asyncio.create_task(asyncio.to_thread(
                self._client.invoke, 'bind_dependencies', args, timeout_seconds=self._timeout))
            self._pending.add(task)
            try:
                response = await _drain(task)
                if (not isinstance(response, dict) or response.get('success') is not True
                        or not isinstance(response.get('result'), dict)):
                    raise DependencyCallError('Dependency allocation failed; retry only the original operation',
                                              outcome_unknown=True)
                value = response['result']
                if (set(value) != {'protocol', 'owner_ref', 'operation_id', 'consumer', 'bindings'}
                        or type(value['protocol']) is not int or value['protocol'] != 1
                        or value['owner_ref'] != owner_ref or value['operation_id'] != operation_id
                        or not isinstance(value['bindings'], dict) or set(value['bindings']) != set(aliases)):
                    raise DependencyCallError('Dependency allocation returned a mismatched receipt', outcome_unknown=True)
                # The receiving App additionally verifies its exact consumer
                # identity and every returned grant before constructing tools.
                return value
            finally:
                self._pending.discard(task)

    async def shutdown(self):
        self._closed = True
        async def finish():
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
        await _drain(asyncio.create_task(finish()))

    async def retire(self, *, owner_ref):
        self._check_open()
        if not isinstance(owner_ref, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', owner_ref):
            raise ValueError('Use the original logical owner identity')
        async with self._slots:
            self._check_open()
            task = asyncio.create_task(asyncio.to_thread(self._client.invoke, 'retire_dependencies',
                {'owner_ref': owner_ref}, timeout_seconds=self._timeout))
            self._pending.add(task)
            try:
                response = await _drain(task)
                value = response.get('result') if isinstance(response, dict) and response.get('success') is True else None
                if (not isinstance(value, dict)
                        or set(value) != {'protocol', 'consumer', 'owner_ref', 'state', 'resources'}
                        or type(value['protocol']) is not int or value['protocol'] != 1
                        or value['owner_ref'] != owner_ref or not isinstance(value['consumer'], dict)
                        or not isinstance(value['state'], str) or value['state'] not in {'retiring', 'retired'}
                        or not isinstance(value['resources'], dict)
                        or len(value['resources']) > 4096
                        or any(not isinstance(k, str) or not isinstance(v, str)
                               or v not in {'active', 'closing', 'unknown', 'unallocated', 'released', 'expired', 'lost', 'failed'}
                               for k, v in value['resources'].items())
                        or value['state'] == 'retired' and any(v in {'active', 'closing', 'unknown'}
                                                             for v in value['resources'].values())):
                    raise DependencyCallError('Dependency retirement returned no valid receipt; retry the same owner',
                                              outcome_unknown=True)
                return value
            finally:
                self._pending.discard(task)
