"""Playground RPCs and the ordinary App worker that owns them.

The transitional ChatRoom inherits only PlaygroundAPI. No Agent modules are
imported here. Each standalone worker has one fixed project and its own requests,
media store and shutdown boundary; credentials never cross the public RPC API.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from pantheon.settings import Settings, get_settings
from pantheon.toolset import ToolSet, tool
from . import engine


class PlaygroundAPI:
    def _playground_settings(self):
        # Legacy host keeps its existing settings/credential selection until
        # clients switch to a separately bound App deployment.
        return get_settings()

    def _playground_snapshot(self):
        settings = self._playground_settings()
        return settings, engine.routes(settings)

    def _playground_model_client(self):
        from pantheon.models.client import get_client
        return get_client()

    def _playground(self):
        if getattr(self, '_playground_stopping', False):
            raise RuntimeError('Playground is stopping. Reopen the App to start another test.')
        if not hasattr(self, '_llm_playground'):
            self._llm_playground = engine.Playground(self._playground_model_client)
        return self._llm_playground

    @asynccontextmanager
    async def _playground_operation(self):
        runner = self._playground()
        if not hasattr(self, '_playground_calls'):
            self._playground_calls = set()
        task = asyncio.current_task()
        self._playground_calls.add(task)
        try:
            yield runner
        finally:
            self._playground_calls.discard(task)

    async def _stop_playground(self):
        # Revoke admission before the first await. Accepted settings reads may
        # finish in their threads, but cannot start a late inference request.
        self._playground_stopping = True
        if not hasattr(self, '_playground_shutdown_task'):
            self._playground_shutdown_task = asyncio.create_task(self._drain_playground())
        await asyncio.shield(self._playground_shutdown_task)

    async def _drain_playground(self):
        tasks = list(getattr(self, '_playground_calls', ()))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        runner = getattr(self, '_llm_playground', None)
        if runner is not None:
            await runner.aclose()

    @tool(exclude=True)
    async def llm_playground_catalog(self) -> dict:
        """Model metadata and credential-free source availability for the Playground."""
        async with self._playground_operation():
            settings, routes = await asyncio.to_thread(self._playground_snapshot)
            return await engine.catalog(settings, routes, self._playground_model_client())

    @tool(exclude=True)
    async def llm_playground_run(
        self, request_id: str, source: str, model: str, prompt: str,
        system: str = "", max_tokens: int = 1024,
        temperature: float | None = None, reasoning_effort: str = "",
        operation: str = "text", parameters: dict | None = None,
    ) -> dict:
        """Run one isolated completion with an explicit source. No tools or chat history."""
        async with self._playground_operation() as runner:
            # Register the runner before resolving settings so an early cancel
            # leaves a tombstone even when credential I/O has not finished.
            routes = {}
            if not source.startswith(('fleet:', 'fleet-route:')):
                _, routes = await asyncio.to_thread(self._playground_snapshot)
            return await runner.run(
                request_id, source, model, prompt, system, max_tokens, temperature,
                reasoning_effort, operation, parameters, source_routes=routes,
            )

    @tool(exclude=True)
    async def llm_playground_media(self, asset_id: str, offset: int = 0) -> dict:
        """Read a bounded media chunk from this user's Playground."""
        async with self._playground_operation() as runner:
            return runner.media.read(asset_id, offset)

    @tool(exclude=True)
    async def llm_playground_upload(self, data: str, name: str, asset_id: str = "", offset: int = 0) -> dict:
        """Upload a bounded audio chunk for an isolated transcription experiment."""
        async with self._playground_operation() as runner:
            return runner.media.upload(asset_id, data, offset, name)

    @tool(exclude=True)
    async def llm_playground_status(self, request_id: str) -> dict:
        """Read progress for a Playground video job without resubmitting it."""
        async with self._playground_operation() as runner:
            return runner.status(request_id)

    @tool(exclude=True)
    async def llm_playground_cancel(self, request_id: str) -> dict:
        """Cancel an in-flight Playground request, including a start/cancel race."""
        async with self._playground_operation() as runner:
            return runner.cancel(request_id)


class PlaygroundToolSet(PlaygroundAPI, ToolSet):
    def __init__(self, name='llm-playground', workdir='.', **kwargs):
        self.workdir = Path(workdir).resolve()
        # In-place re-exec skips App cleanup and invalidates media/request handles.
        kwargs['allow_in_place_restart'] = False
        super().__init__(name=name, **kwargs)

    def _playground_settings(self):
        return Settings(self.workdir, isolated_env=True)

    def _playground_model_client(self):
        if not hasattr(self, '_owned_model_client'):
            from pantheon.models.client import ModelServices
            self._owned_model_client = ModelServices(
                hub=os.environ.get('PANTHEON_HUB_URL', ''),
                token=os.environ.get('FLEET_KEY', ''),
            )
        return self._owned_model_client

    async def _drain_playground(self):
        try:
            await super()._drain_playground()
        finally:
            client = getattr(self, '_owned_model_client', None)
            if client is not None:
                await client.aclose()

    async def run_setup(self):
        if self.worker is not None:
            self.worker.set_activity_callback(self._playground_activity)

    def _playground_activity(self):
        return {'activity_scope': 'app',
                'active_requests': len(getattr(self, '_playground_calls', ()))}

    async def cleanup(self):
        await self._stop_playground()
