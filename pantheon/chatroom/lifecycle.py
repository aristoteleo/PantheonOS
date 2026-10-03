"""Agent-owned work, separate from the process host's RPC/transport lifetime.

Stop closes admission to internal chat producers as well as remote callers.
Already admitted runs and background tool mutations drain before observers,
plugins and event connections are disposed. The supervisor owns the hard stop
deadline; exceeding it is not a clean drain and never authorizes tool replay.
"""

import asyncio
from contextvars import ContextVar
from functools import wraps

from pantheon.apps.host_lifecycle import AppShutdownError
from pantheon.utils.log import logger

_accepted_continuation = ContextVar("agent_accepted_continuation", default=None)


def admitted_chat(method):
    @wraps(method)
    async def call(self, *args, **kwargs):
        continuation = _accepted_continuation.get() is self
        if getattr(self, "_agent_stopping", False) and not continuation:
            return {"success": False, "message": "Agent is stopping"}
        # Consume the local-only permission. Descendant tasks cannot inherit it
        # and admit unrelated work after stop; it never comes from RPC arguments.
        permission = _accepted_continuation.set(None)
        calls = getattr(self, "_agent_calls", None)
        if calls is None:
            calls = self._agent_calls = {}
        task = asyncio.current_task()
        # Count nested embedded calls without losing ownership of the outer run.
        calls[task] = calls.get(task, 0) + 1
        try:
            return await method(self, *args, **kwargs)
        finally:
            _accepted_continuation.reset(permission)
            calls[task] -= 1
            if not calls[task]:
                del calls[task]
    return call


class AgentLifetime:
    def _continue_accepted_chat(self, thread, chat_id, messages):
        """Finish user steer messages accepted before stop, after the prior save."""
        async def run():
            await thread._done.wait()
            if getattr(self, "_agent_save_error", None) is not None:
                # A failed drain is explicit; never execute more tools after a
                # persistence failure. The supervisor must not report clean stop.
                return
            permission = _accepted_continuation.set(self)
            try:
                await self.chat(chat_id=chat_id, message=messages)
            finally:
                _accepted_continuation.reset(permission)

        pending = getattr(self, "_agent_continuations", None)
        if pending is None:
            pending = self._agent_continuations = set()
        task = self._track_background(asyncio.create_task(run()))
        # Register before the coroutine gets CPU: cleanup must not cancel an
        # accepted turn in the gap before admitted_chat records its ownership.
        pending.add(task)
        task.add_done_callback(pending.discard)

    def _track_background(self, task):
        tasks = getattr(self, "_background_tasks", None)
        if tasks is None:
            tasks = self._background_tasks = set()
        tasks.add(task)

        def finished(done):
            tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                logger.warning("Agent background observer failed: {}", done.exception())

        task.add_done_callback(finished)
        return task

    async def begin_shutdown(self):
        # No await before the admission barrier: auto-notifications, claw and
        # queued steer turns also enter chat(), without going through RPC.
        self._agent_stopping = True
        gateway = getattr(self, "_gateway_channel_manager", None)
        if gateway is not None:
            gateway.begin_shutdown()

    async def _stop_auxiliary_services(self):
        """Extension point for resources owned only by a combined legacy host."""

    async def cleanup(self):
        # The generic host owns calling cleanup; the legacy entrypoint may call
        # it again. Share the outcome, including failures, rather than run hooks
        # twice. Shield it from an embedded caller cancelling its wait.
        task = getattr(self, "_agent_cleanup_task", None)
        if task is None:
            self._agent_stopping = True
            task = self._agent_cleanup_task = asyncio.create_task(self._cleanup_agent())
        await asyncio.shield(task)

    async def _cleanup_agent(self):
        errors = []

        async def finish(operation):
            try:
                await operation()
            except Exception as exc:
                errors.append(exc)

        await finish(self.begin_shutdown)

        # Remote calls have already drained in apphost. Embedded/internal chat
        # calls do not belong to that worker and must also finish their saves.
        while calls := set(getattr(self, "_agent_calls", {})) | set(getattr(self, "_agent_continuations", ())):
            await asyncio.gather(*calls, return_exceptions=True)
        if failure := getattr(self, "_agent_save_error", None):
            errors.append(failure)

        # A tool adopted into the background may outlive chat(). Do not cancel
        # it and then claim that its side effects have stopped. New chat turns
        # triggered by its completion are rejected by the admission barrier.
        seen = set()
        teams = list(getattr(self, "chat_teams", {}).values())
        if default := getattr(self, "_default_team", None):
            teams.append(default)
        for team in teams:
            for agent in team.agents.values():
                manager = getattr(agent, "_bg_manager", None)
                if manager is None or id(manager) in seen:
                    continue
                seen.add(id(manager))
                while pending := [t.asyncio_task for t in manager.list_tasks()
                                  if t.asyncio_task is not None and not t.asyncio_task.done()]:
                    await asyncio.gather(*pending, return_exceptions=True)

        # These are observer/warmup/notification tasks, not tool executions.
        tasks = list(getattr(self, "_background_tasks", ()))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        gateway = getattr(self, "_gateway_channel_manager", None)
        if gateway is not None:
            await finish(gateway.close)

        await finish(self._stop_auxiliary_services)

        routing = getattr(self, "_memory_routing_thread", None)
        if routing is not None:
            await finish(lambda: asyncio.to_thread(routing.join))

        # Initialization may outlive a cancelled startup/RPC observer. It owns
        # partial construction and rollback until settled; never clear/dispose
        # the published plugin list while that task can still replace it.
        initialization = getattr(self, '_plugin_initialization', None)
        if initialization is not None:
            await finish(lambda: asyncio.shield(initialization))

        plugins = getattr(self, "_plugins", [])
        seen.clear()
        for plugin in list(plugins):
            if id(plugin) not in seen:
                seen.add(id(plugin))
                await finish(plugin.on_shutdown)
        plugins.clear()

        seen.clear()
        for team in teams:
            for agent in team.agents.values():
                for provider in getattr(agent, "_owned_tool_providers", ()):
                    if id(provider) not in seen:
                        seen.add(id(provider))
                        await finish(provider.shutdown)

        # Metadata edits (including a newly created chat's template) can be
        # waiting on a debounce even when no chat run is active. Cancel/join
        # those timers and flush all opened stores before releasing the App's
        # writer lock. Never call MemoryManager.save(), which prunes unloaded
        # histories. Failed saves make the shutdown fail visibly.
        flush = getattr(getattr(self, 'memory_manager', None), 'flush', None)
        if callable(flush):
            await finish(flush)

        close_agents = getattr(getattr(self, '_environment', None), 'close_agents', None)
        if close_agents is not None:
            await finish(close_agents)

        adapter = getattr(self, "_nats_adapter", None)
        if adapter is not None:
            await finish(adapter.close)
        if errors:
            raise AppShutdownError(errors) from errors[0]
