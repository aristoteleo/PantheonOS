import asyncio
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.toolset_backend import register_toolset
from pantheon.toolset import ToolSet, tool, ExecutionContext, set_current_context_variables, reset_current_context_variables


class Service(ToolSet):
    def __init__(self):
        super().__init__('test')
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.cleaned = self.stopped = 0

    @tool
    async def mutation(self, value):
        self.started.set()
        await self.release.wait()
        assert not self.cleaned
        return value

    @tool(exclude=True)
    async def interrupt(self):
        self.release.set()
        return True

    @tool
    async def context(self, value=None, **kwargs):
        return {'context': dict(self.get_context()), 'metadata': kwargs}

    async def begin_shutdown(self):
        self.stopped += 1

    async def cleanup(self):
        self.cleaned += 1


@pytest.mark.asyncio
async def test_concurrent_rpc_interrupt_context_and_drain(tmp_path):
    ctx = AppContext('test', tmp_path, tmp_path, None)
    service = Service()
    await register_toolset(ctx, service)
    assert ctx.require_rpc_token and service.worker is None
    assert 'interrupt' in ctx.concurrent_methods
    token = set_current_context_variables(ExecutionContext(workdir='foreign', _call_agent=lambda:None))
    try:
        assert await ctx._methods['context'](color='blue') == {'context': {}, 'metadata': {'color': 'blue'}}
        for bad in ('context_variables', 'session_id'):
            with pytest.raises(ValueError, match='framework-only'):
                await ctx._methods['context'](**{bad: 'foreign'})
    finally:
        reset_current_context_variables(token)
    with pytest.raises(ValueError, match='framework-only'):
        await ctx._methods['mutation'](value=7, undeclared=True)
    call = asyncio.create_task(ctx._methods['mutation'](value=7))
    await service.started.wait()
    assert await ctx._methods['interrupt']()
    assert await call == 7
    service.started.clear()
    service.release.clear()
    call = asyncio.create_task(ctx._methods['mutation'](value=8))
    await service.started.wait()
    with pytest.raises(RuntimeError, match='draining'):
        await ctx.before_stop()
    assert not service.cleaned
    with pytest.raises(RuntimeError, match='stopping'):
        await ctx._methods['context']()
    service.release.set()
    assert await call == 8
    await ctx.before_stop()
    await ctx._cleanup()
    assert service.cleaned == service.stopped == 1


@pytest.mark.asyncio
async def test_setup_failure_closes_service_without_exposing_partial_methods(tmp_path):
    ctx = AppContext('test', tmp_path, tmp_path, None)
    service = Service()
    service.run_setup = AsyncMock(side_effect=RuntimeError('setup failed'))
    with pytest.raises(RuntimeError, match='setup failed'):
        await register_toolset(ctx, service)
    assert not ctx._methods and service.cleaned == service.stopped == 1
    await ctx._cleanup()
    assert service.cleaned == 1
