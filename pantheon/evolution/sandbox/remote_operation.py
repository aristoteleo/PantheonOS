"""Bind Evolution to an ordinary owned placement and external Agent execution."""
import asyncio
import hashlib

from pantheon.apps.agent_execution_runner import _encode
from .agent_execution import SandboxAgentExecution
from ..lifetime import EvolutionCleanupError, join_cleanup


class RemoteSandboxOperation:
    def __init__(self, binding, root):
        self.binding, self.root = binding, root
        self.identity = 'evo-' + hashlib.sha256(str(root).encode()).hexdigest()[:40]
        # The factory is synchronous: ownership exists before any remote effect.
        # start journals creation; close confirms the backend stopped.
        self.placement = binding.sandbox_factory(root / 'placement', operation_id=self.identity)
        self.execution = self.task = self.closing = None
        self.request = None
        self.closed = False

    async def run(self, *, configuration, instructions='', model=None, timeout=600, evaluation_only=False):
        if self.closed:
            raise RuntimeError('Sandbox operation is closing')
        import json
        request = _encode({'configuration': configuration, 'instructions': instructions,
            'model': model, 'timeout': timeout, 'evaluation_only': evaluation_only}, 16 * 1024 * 1024)
        async def execute():
            try:
                data = json.loads(request)
                backend = await self.placement.start()
                self.execution = SandboxAgentExecution(self.binding.client, self.root / 'execution',
                    binding_id=self.binding.binding_id, execution_id=self.identity,
                    backend_id=backend.backend_id, invoke=backend.invoke, terminate=self.placement.close)
                if data.pop('evaluation_only'):
                    return await self.execution.evaluate(configuration=data['configuration'])
                return await self.execution.run(**data)
            except Exception as exc:
                raise EvolutionCleanupError([exc]) from exc
        if self.task is None:
            self.request = request
            self.task = asyncio.create_task(execute())
            self.task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        elif request != self.request:
            raise ValueError('Sandbox operation already has another request')
        return await asyncio.shield(self.task)

    async def close(self):
        self.closed = True
        async def dispose():
            if self.task is not None and not self.task.done():
                self.task.cancel()
            try:
                if self.execution is not None:
                    await self.execution.close()
                else:
                    await self.placement.close()
            except Exception as exc:
                raise EvolutionCleanupError([exc]) from exc
            finally:
                if self.task is not None:
                    await asyncio.gather(self.task, return_exceptions=True)
            # The team retains our idempotent close callback until search end.
            # Do not retain every iteration's source snapshot, tool frames and
            # result task in memory after confirmed cleanup. Durable receipts
            # stay on disk; failed cleanup keeps these owners reachable.
            self.task = self.execution = self.placement = self.request = None
        if self.closing is None:
            self.closing = asyncio.create_task(dispose())
        await join_cleanup(self.closing)
