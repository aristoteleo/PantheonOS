"""Consumer side of agent-execution@1, without Agent or provider SDK imports.

The prepared dependency grant binds consumer_id for every method. This client
cannot choose another owner, discover an Agent, or fall back to local inference.
Callers persist execution/worker identities and tool receipts before effects.
There are no automatic retries, tool execution, claim stealing or run replay.
"""
import asyncio
import base64
import hashlib
import json
import re

from .dependency_client import DependencyClient, DependencyCallError


_ARGUMENTS = {
    'submit': ['execution_id', 'specification'],
    'poll': ['execution_id'],
    'claim': ['execution_id', 'call_id', 'worker_id'],
    'reply': ['execution_id', 'call_id', 'worker_id', 'response'],
    'cancel': ['execution_id'],
    'read_result': ['execution_id', 'offset'],
    'release': ['execution_id'],
}
METHODS = tuple('agent_execution_' + name for name in _ARGUMENTS)


def execution_method_rules(consumer_id):
    """Owner-side recipe for the existing generic dependency grant issuer.

    Use the consumer's durable logical identity, never a caller-supplied label.
    This pure projection issues no credentials or runtime-management authority.
    """
    if not isinstance(consumer_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', consumer_id):
        raise ValueError('Supply the execution consumer owner')
    return {'agent_execution_' + method: {'arguments': list(arguments), 'bound': {'consumer_id': consumer_id}}
            for method, arguments in _ARGUMENTS.items()}


class AgentExecutionClient:
    def __init__(self, client: DependencyClient):
        if not isinstance(client, DependencyClient):
            raise ValueError('Supply a prepared Agent execution dependency')
        self.client = client
        self._pending = set()
        self._closed = False

    async def _call(self, method, execution_id, **args):
        if self._closed:
            raise RuntimeError('Agent execution client is closed')
        if not isinstance(execution_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', execution_id):
            raise ValueError('Supply a durable execution identity')
        arguments = json.loads(json.dumps({'execution_id': execution_id, **args}, allow_nan=False))
        task = asyncio.create_task(asyncio.to_thread(self.client.invoke, 'agent_execution_' + method,
            arguments, timeout_seconds=30))
        self._pending.add(task)
        cancelled = False
        try:
            # Do not leave the transport thread using a retired dependency.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            response = task.result()
            if cancelled:
                raise asyncio.CancelledError
            if not isinstance(response, dict) or response.get('success') is not True or 'result' not in response:
                raise DependencyCallError('Agent execution call failed; observe its saved identity before retrying',
                                          outcome_unknown=True)
            return response['result']
        finally:
            self._pending.discard(task)

    async def submit(self, execution_id, specification):
        return await self._call('submit', execution_id, specification=specification)

    async def poll(self, execution_id):
        return await self._call('poll', execution_id)

    async def claim(self, execution_id, call_id, worker_id):
        """Recovered claims require the caller's ledger; never blindly rerun them."""
        return await self._call('claim', execution_id, call_id=call_id, worker_id=worker_id)

    async def reply(self, execution_id, call_id, worker_id, response):
        return await self._call('reply', execution_id, call_id=call_id, worker_id=worker_id, response=response)

    async def cancel(self, execution_id):
        """Cancels Agent inference only; settle all claimed caller tools separately."""
        return await self._call('cancel', execution_id)

    async def read_result(self, execution_id):
        parts, offset, metadata = [], 0, None
        while True:
            page = await self._call('read_result', execution_id, offset=offset)
            try:
                current = page['size'], page['sha256']
                if (type(current[0]) is not int or not 1 <= current[0] <= 16 * 1024 * 1024
                        or not isinstance(current[1], str) or not re.fullmatch('[a-f0-9]{64}', current[1])
                        or metadata is not None and current != metadata
                        or type(page['offset']) is not int or page['offset'] != offset):
                    raise ValueError
                metadata = current
                raw = base64.b64decode(page['data'], validate=True)
                if (not 1 <= len(raw) <= 32768 or type(page['next_offset']) is not int
                        or page['next_offset'] != offset + len(raw) or offset + len(raw) > current[0]):
                    raise ValueError
                parts.append(raw)
                offset += len(raw)
                if offset == current[0]:
                    combined = b''.join(parts)
                    if hashlib.sha256(combined).hexdigest() != current[1]:
                        raise ValueError
                    return json.loads(combined)
            except (KeyError, TypeError, ValueError):
                raise ValueError('Agent execution result is malformed or changed during delivery') from None

    async def release(self, execution_id):
        return await self._call('release', execution_id)

    async def close(self):
        # Closing a transport is not cancellation of its remote executions.
        self._closed = True
        while self._pending:
            await asyncio.shield(asyncio.gather(*tuple(self._pending), return_exceptions=True))
