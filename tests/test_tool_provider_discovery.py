"""Remote App metadata must not delay unrelated tools or outlive its caller."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from nats.errors import NoRespondersError

import pantheon.providers as providers_module
from pantheon.agent import Agent
from pantheon.apps.proxy import ToolsetProxy
from pantheon.providers import LocalProvider, MCPProvider, ToolSetProvider
from pantheon.toolset import ToolSet, tool


class Echo(ToolSet):
    def __init__(self):
        super().__init__(name="echo")

    @tool
    async def echo(self, message: str) -> str:
        """Echo a message.

        Args:
            message: Text to echo.
        """
        return message


class Service:
    """Only the remote transport boundary is substituted; proxy retries are real."""

    def __init__(self):
        self.toolset = Echo()
        self.calls = []
        self.missing = False
        self.started = asyncio.Event()
        self.release = None
        self.cancelled = False

    async def invoke(self, method, args):
        self.calls.append((method, args))
        if method == "list_tools":
            self.started.set()
            if self.release is not None:
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            if self.missing:
                raise NoRespondersError()
            return await self.toolset.list_tools()
        return await getattr(self.toolset, method)(**args)


def remote(service=None):
    service = service or Service()
    proxy = ToolsetProxy(f"discovery-test-{uuid4()}")
    proxy.service = service
    return ToolSetProvider(proxy), service


def agent(**providers):
    value = Agent(name="discovery-test", instructions="Test", model="gpt-4o-mini")
    value.providers.update(providers)
    return value


class BackoffClock:
    """Advance the real proxy's retry sleeps without 37.5s of wall-clock waiting."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.pending = []
        self.scheduled = False

    async def sleep(self, delay):
        self.sleeps.append(delay)
        future = asyncio.get_running_loop().create_future()
        self.pending.append((self.now + delay, future))
        if not self.scheduled:
            self.scheduled = True
            asyncio.get_running_loop().call_soon(self.advance)
        await future

    def advance(self):
        self.scheduled = False
        pending, self.pending = self.pending, []
        self.now = min(deadline for deadline, _ in pending)
        for deadline, future in pending:
            if deadline <= self.now:
                if not future.done():
                    future.set_result(None)
            else:
                self.pending.append((deadline, future))
        if self.pending:
            self.scheduled = True
            asyncio.get_running_loop().call_soon(self.advance)


def clock_fixture(monkeypatch):
    clock = BackoffClock()
    monkeypatch.setattr(asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(providers_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    return clock


@pytest.mark.asyncio
async def test_exact_base_skips_five_missing_remote_providers(monkeypatch):
    clock = clock_fixture(monkeypatch)
    unavailable = [remote() for _ in range(5)]
    for _, service in unavailable:
        service.missing = True

    # This is the old serial discovery prefix, with the real ToolsetProxy retry
    # implementation: five providers each consume 0.5+1+2+2+2 seconds.
    for provider, _ in unavailable:
        with pytest.raises(NoRespondersError):
            await provider.list_tools()
    assert clock.now == 37.5
    assert clock.sleeps == [0.5, 1.0, 2.0, 2.0, 2.0] * 5
    assert [len(service.calls) for _, service in unavailable] == [6] * 5
    for provider, service in unavailable:
        provider.invalidate_cache()
        service.calls.clear()
    clock.sleeps.clear()

    async def local_echo(message: str):
        return message

    a = agent(**{f"missing{i}": pair[0] for i, pair in enumerate(unavailable)})
    a._base_functions["local_echo"] = local_echo
    assert await a.call_tool("local_echo", {"message": "instant"}) == "instant"
    assert clock.sleeps == []
    assert all(not service.calls for _, service in unavailable)


@pytest.mark.asyncio
async def test_schema_discovery_parallel_cooldown_expiry_and_recovery(monkeypatch):
    clock = clock_fixture(monkeypatch)
    unavailable = [remote() for _ in range(5)]
    for _, service in unavailable:
        service.missing = True
    healthy, service = remote()
    a = agent(**{f"missing{i}": pair[0] for i, pair in enumerate(unavailable)}, healthy=healthy)
    tools = await a.get_tools_for_llm()
    assert [x["function"]["name"] for x in tools if x["function"]["name"].endswith("__echo")] == ["healthy__echo"]
    assert clock.now == 7.5
    assert [len(s.calls) for _, s in unavailable] == [6] * 5
    await a.get_tools_for_llm()
    assert clock.now == 7.5
    assert [len(s.calls) for _, s in unavailable] == [6] * 5
    assert len(service.calls) == 1

    # Expiry still probes in parallel, not another 37.5-second serial pause.
    clock.now += 30.0
    await a.get_tools_for_llm()
    assert clock.now == 45.0
    assert [len(s.calls) for _, s in unavailable] == [12] * 5
    unavailable[2][1].missing = False
    clock.now += 30.0
    tools = await a.get_tools_for_llm()
    assert any(x["function"]["name"] == "missing2__echo" for x in tools)


@pytest.mark.asyncio
async def test_explicit_target_whitelist_and_one_schema_rpc():
    unrelated, unrelated_service = remote()
    target, target_service = remote()
    a = agent(unrelated=unrelated, target=target)
    assert await a.call_tool("target__echo", {"message": "right", "not_a_parameter": 10}) == "right"
    assert not unrelated_service.calls
    assert [name for name, _ in target_service.calls] == ["list_tools", "echo"]
    assert "not_a_parameter" not in target_service.calls[-1][1]
    assert target_service.calls[-1][1]["message"] == "right"
    with pytest.raises(ValueError, match="not found in provider 'target'"):
        await a.call_tool("target__absent", {})
    assert not unrelated_service.calls


@pytest.mark.asyncio
async def test_suffix_compatibility_keeps_registration_order():
    first, first_service = remote()
    second, second_service = remote()
    a = agent(first=first, second=second)
    assert await a.call_tool("echo", {"message": "legacy"}) == "legacy"
    assert [name for name, _ in first_service.calls] == ["list_tools", "echo"]
    assert [name for name, _ in second_service.calls] == ["list_tools"]


@pytest.mark.asyncio
async def test_explicit_target_failure_propagates_and_operation_retries_unchanged(monkeypatch):
    clock = clock_fixture(monkeypatch)
    provider, service = remote()
    service.missing = True
    a = agent(target=provider)
    with pytest.raises(NoRespondersError):
        await a.call_tool("target__echo", {"message": "fail"})
    assert len(service.calls) == 6
    with pytest.raises(NoRespondersError):
        await a.call_tool("target__echo", {"message": "still fail"})
    assert len(service.calls) == 6
    service.missing = False
    provider.invalidate_cache()
    await provider.list_tools()
    original = service.invoke
    attempts = 0

    async def recovering_operation(method, args):
        nonlocal attempts
        if method == "echo":
            attempts += 1
            if attempts < 3:
                raise NoRespondersError()
        return await original(method, args)

    service.invoke = recovering_operation
    assert await a.call_tool("target__echo", {"message": "recovered"}) == "recovered"
    assert attempts == 3
    assert clock.sleeps[-2:] == [0.5, 1.0]


@pytest.mark.asyncio
async def test_concurrent_schema_and_call_share_one_discovery():
    provider, service = remote()
    service.release = asyncio.Event()
    a = agent(target=provider)
    schema = asyncio.create_task(a.get_tools_for_llm())
    await service.started.wait()
    call = asyncio.create_task(a.call_tool("target__echo", {"message": "shared"}))
    await asyncio.sleep(0)
    service.release.set()
    tools, result = await asyncio.gather(schema, call)
    assert result == "shared"
    assert any(t["function"]["name"] == "target__echo" for t in tools)
    assert [name for name, _ in service.calls] == ["list_tools", "echo"]


@pytest.mark.asyncio
async def test_cancel_one_waiter_keeps_other_and_cancel_last_drains():
    provider, service = remote()
    service.release = asyncio.Event()
    first = asyncio.create_task(provider.list_tools())
    await service.started.wait()
    second = asyncio.create_task(provider.list_tools())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not service.cancelled
    service.release.set()
    assert len(await second) == 1
    assert len(service.calls) == 1

    provider.invalidate_cache()
    service.started.clear()
    service.release.clear()
    third = asyncio.create_task(provider.list_tools())
    await service.started.wait()
    third.cancel()
    with pytest.raises(asyncio.CancelledError):
        await third
    assert service.cancelled
    assert provider._discovery_task is None
    assert provider._discovery_waiters == {}
    assert provider._discovery_error is None
    service.release.set()
    assert len(await provider.list_tools()) == 1
    assert len(service.calls) == 3


@pytest.mark.asyncio
async def test_cancel_agent_discovery_drains_all_remote_tasks():
    pairs = [remote() for _ in range(3)]
    for _, service in pairs:
        service.release = asyncio.Event()
    a = agent(**{f"remote{i}": p for i, (p, _) in enumerate(pairs)})
    request = asyncio.create_task(a.get_tools_for_llm())
    await asyncio.gather(*(s.started.wait() for _, s in pairs))
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert all(s.cancelled for _, s in pairs)
    assert all(p._discovery_task is None and not p._discovery_waiters for p, _ in pairs)


@pytest.mark.asyncio
@pytest.mark.parametrize("old_fails", [False, True])
async def test_invalidate_inflight_fences_old_schema_and_failure(old_fails):
    provider, service = remote()
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    call_count = 0

    async def respond(method, args):
        nonlocal call_count
        if method != "list_tools":
            return await getattr(service.toolset, method)(**args)
        call_count += 1
        if call_count == 1:
            first_started.set()
            await first_release.wait()
            if old_fails:
                raise RuntimeError("retired failure")
            return {"success": True, "tools": []}
        return await service.toolset.list_tools()

    service.invoke = respond
    old = asyncio.create_task(provider.list_tools())
    await first_started.wait()
    provider.invalidate_cache()
    fresh = await provider.list_tools()
    first_release.set()
    assert await old is fresh
    assert [t.name for t in fresh] == ["echo"]
    assert call_count == 2
    assert provider._discovery_error is None
    assert await provider.call_tool("echo", {"message": "present"}) == "present"


@pytest.mark.asyncio
async def test_success_empty_schema_is_cached_and_not_refetched_for_descriptions():
    provider, service = remote()
    async def empty(method, args):
        service.calls.append((method, args))
        return {"success": True, "tools": []}
    service.invoke = empty
    assert await provider.list_tools() == []
    await provider._ensure_tool_descriptions()
    assert await provider.list_tools() == []
    assert len(service.calls) == 1


@pytest.mark.asyncio
async def test_metadata_timeout_is_bounded_and_can_be_invalidated(monkeypatch):
    monkeypatch.setattr(providers_module, "_TOOLSET_DISCOVERY_TIMEOUT", 0.01)
    provider, service = remote()
    service.release = asyncio.Event()
    with pytest.raises(TimeoutError):
        await provider.list_tools()
    assert service.cancelled
    with pytest.raises(TimeoutError):
        await provider.list_tools()
    assert len(service.calls) == 1
    service.release.set()
    provider.invalidate_cache()
    assert len(await provider.list_tools()) == 1


@pytest.mark.asyncio
async def test_mcp_and_local_discovery_stay_on_callers_task():
    caller = asyncio.current_task()
    calls = []

    class TaskBoundMCP(MCPProvider):
        async def list_tools(self):
            assert asyncio.current_task() is caller
            calls.append("mcp")
            return await local.list_tools()

    class TaskBoundLocal(LocalProvider):
        async def list_tools(self):
            assert asyncio.current_task() is caller
            calls.append("local")
            return await super().list_tools()

    local = TaskBoundLocal(Echo())
    await local.initialize()
    remote_provider, _ = remote()
    a = agent(mcp=TaskBoundMCP("task-bound"), remote=remote_provider, local=local)
    tools = await a.get_tools_for_llm()
    assert [t["function"]["name"] for t in tools if t["function"]["name"].endswith("__echo")] == ["local__echo", "mcp__echo", "remote__echo"]
    assert calls == ["mcp", "local", "local"]
    mcp_schema = next(t["function"] for t in tools if t["function"]["name"] == "mcp__echo")
    assert "_background" not in mcp_schema["parameters"]["properties"]


@pytest.mark.asyncio
async def test_shutdown_drains_discovery_and_cancellation_does_not_cooldown():
    provider, service = remote()
    service.release = asyncio.Event()
    pending = asyncio.create_task(provider.list_tools())
    await service.started.wait()
    await provider.shutdown()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert service.cancelled
    assert not provider._discovery_waiters
    assert provider._discovery_task is None
    assert provider._discovery_error is None


@pytest.mark.asyncio
async def test_metadata_deadline_never_becomes_operation_deadline(monkeypatch):
    monkeypatch.setattr(providers_module, "_TOOLSET_DISCOVERY_TIMEOUT", 0.01)
    provider, service = remote()
    original = service.invoke

    async def slower_operation(method, args):
        if method == "echo":
            await asyncio.sleep(0.025)
        return await original(method, args)

    service.invoke = slower_operation
    a = agent(target=provider)
    assert await a.call_tool("target__echo", {"message": "still completes"}) == "still completes"


@pytest.mark.asyncio
async def test_explicit_missing_method_cannot_match_other_provider_suffix():
    from pantheon.agent import ToolInfo

    target, target_service = remote()
    other, other_service = remote()
    other._tools_cache = [ToolInfo(name="target__absent", description="collision", inputSchema={})]
    a = agent(target=target, other=other)
    with pytest.raises(ValueError, match="not found in provider 'target'"):
        await a.call_tool("target__absent", {})
    assert [name for name, _ in target_service.calls] == ["list_tools"]
    assert not other_service.calls
