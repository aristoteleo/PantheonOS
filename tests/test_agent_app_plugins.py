"""App plugin composition through actual Teams, task storage and TLS grants."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.agent import Agent
from pantheon.factory.bindings import AgentToolBindings
from pantheon.internal.app_plugins import BoundManagementPlugin, create_app_plugins
from pantheon.team import PantheonTeam
from pantheon.team.plugin_registry import PluginInitializationError
from test_agent_dependency_bindings import endpoint, provider
from test_agent_model_scope import scopes
from test_agent_instance_factory import factory, RECIPE


def forbidden(*args, **kwargs):
    raise AssertionError("App plugin consulted ambient capabilities")


def configure(scope, sections):
    settings = scope.settings
    settings.package_templates = settings.work_dir / "package"
    settings.package_templates.mkdir(parents=True, exist_ok=True)
    (settings.package_templates / "settings.json").write_text(json.dumps(sections))
    (settings.package_templates / "mcp.json").write_text("{}")
    return settings


def make_agent(scope, name="Leader"):
    return Agent(name=name, instructions="Test App", model="openai/gpt-4o-mini",
                 model_scope=scope, use_memory=False)


@pytest.fixture(autouse=True)
def no_ambient_management(monkeypatch):
    monkeypatch.setattr("pantheon.internal.fleet_plugin._fleet_configured", forbidden)
    monkeypatch.setattr("pantheon.internal.model_services_plugin._fleet_configured", forbidden)
    monkeypatch.setattr("pantheon.models.manager.ModelServiceManager", forbidden)
    monkeypatch.setattr("pantheon.apps.resolver.get_shared_resolver", forbidden)
    monkeypatch.setenv("FLEET_KEY", "ambient-owner-secret")
    monkeypatch.setenv("FLEET_CONTROLLER_URL", "https://must-not-use.invalid")


@pytest.mark.asyncio
async def test_management_from_explicit_composition_is_instance_scoped_over_tls(scopes, endpoint):
    owners = []
    for token in ("a" * 64, "b" * 64):
        scope = scopes()
        settings = configure(scope, {"fleet_system": {"enabled": True},
                                     "model_services_system": {"enabled": True}})
        agent = make_agent(scope)
        remotes = {name: provider(endpoint, name, token) for name in ("fleet", "model_services")}
        bindings = AgentToolBindings(remotes)
        def lookup(candidate, owner=agent, bindings=bindings):
            if candidate is not owner:
                raise ValueError("Foreign Agent")
            return bindings
        plugins = await create_app_plugins(settings=settings, model_scope=scope, bindings_for=lookup)
        team = PantheonTeam([agent], plugins=plugins)
        await team.async_setup()
        owners.append((agent, plugins, remotes))
    try:
        for n, (agent, _, _) in enumerate(owners):
            menu = await agent.get_tools_for_llm()
            assert {"fleet__execute", "model_services__execute", "model_services__use_fleet_model"} <= {
                tool["function"]["name"] for tool in menu}
            for name in ("fleet", "model_services"):
                result = await agent.call_tool(name + "__execute", {"command": "inspect"},
                                                {"FLEET_KEY": "must-not-forward"})
                assert result["session"] == ("session-a", "session-b")[n]
        await asyncio.gather(*(p.on_shutdown() for p in owners[0][1]))
        # Plugin shutdown cannot close the borrowed client, or the other App.
        assert (await owners[0][2]["fleet"].call_tool("execute", {"command": "drain"}))["session"] == "session-a"
        assert (await owners[1][0].call_tool("model_services__execute", {"command": "alive"}))["session"] == "session-b"
        assert all("FLEET_KEY" not in request["args"] for _, _, request in endpoint.calls)
    finally:
        for _, plugins, remotes in owners:
            await asyncio.gather(*(p.on_shutdown() for p in plugins))
            await asyncio.gather(*(p.shutdown() for p in remotes.values()))


@pytest.mark.asyncio
async def test_required_binding_failure_cannot_silently_run_or_retry_partial_setup(scopes):
    scope = scopes()
    agent = make_agent(scope)
    calls = []
    def lookup(candidate):
        calls.append(candidate)
        return AgentToolBindings()
    team = PantheonTeam([agent], plugins=[BoundManagementPlugin("fleet", lookup)])
    with pytest.raises(RuntimeError, match="Required plugin"):
        await team.async_setup()
    assert not team._is_initialized
    assert "Remote Compute" not in agent.instructions
    with pytest.raises(RuntimeError, match="Team setup failed"):
        await team.run("This must not call a model")
    assert calls == [agent]


@pytest.mark.asyncio
async def test_instance_resolver_checks_object_identity_not_copied_public_id(endpoint):
    owner = factory(endpoint)
    try:
        agent = (await owner({"same-config": RECIPE}, conversation_id="chat-0"))[0]
        assert owner.bindings_for(agent).toolsets["shell"] is agent.providers["shell"]
        copied = SimpleNamespace(id=agent.id, _instance_identity=agent._instance_identity)
        with pytest.raises(ValueError, match="does not belong"):
            owner.bindings_for(copied)
    finally:
        await owner.shutdown()
    with pytest.raises(RuntimeError, match="stopping"):
        owner.bindings_for(agent)


@pytest.mark.asyncio
async def test_model_selection_checks_target_access_not_leader_and_rejects_stale_check(scopes, endpoint):
    ref = "fleet-model://test/qwen"
    spec = {"id": "qwen", "tools": True, "context": 65536, "operations": ["text"]}
    leader_client = SimpleNamespace(deployment=AsyncMock(side_effect=AssertionError("Used leader credentials")))
    target_client = SimpleNamespace(deployment=AsyncMock(return_value={"state": "ready", "models": [spec]}))
    leader = make_agent(scopes(fleet_client=leader_client))
    target = make_agent(scopes(fleet_client=target_client), "Worker")
    remote = provider(endpoint, "model_services")
    plugin = BoundManagementPlugin("model_services", lambda _: AgentToolBindings({"model_services": remote}))
    team = PantheonTeam([leader, target], plugins=[plugin])
    await team.async_setup()
    try:
        result = await leader.call_tool("model_services__use_fleet_model", {"model_ref": ref, "agent_name": "worker"})
        assert result["agent"] == "Worker" and target.models == [ref]
        leader_client.deployment.assert_not_called()
        # Metadata may arrive after a user's new selection.
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*_):
            entered.set()
            await release.wait()
            return {"state": "ready", "models": [spec]}
        target_client.deployment = delayed
        pending = asyncio.create_task(leader.call_tool("model_services__use_fleet_model",
            {"model_ref": ref, "agent_name": "Worker"}))
        await entered.wait()
        target.models = ["openai/new-selection"]
        release.set()
        with pytest.raises(RuntimeError, match="changed while checking"):
            await pending
        assert target.models == ["openai/new-selection"]
    finally:
        await plugin.on_shutdown()
        await remote.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["context", "tools", "text", "state", "missing_scope", "missing_client"])
async def test_model_selection_does_not_assign_unusable_or_unbound_model(scopes, endpoint, change):
    spec = {"id": "qwen", "tools": True, "context": 65536, "operations": ["text"]}
    row = {"state": "ready", "models": [spec]}
    if change == "context":
        spec["context"] = True
    elif change == "tools":
        spec["tools"] = False
    elif change == "text":
        spec["operations"] = ["image"]
    elif change == "state":
        row["state"] = "stopped"
    scope = scopes(fleet_client=SimpleNamespace(deployment=AsyncMock(return_value=row)))
    agent = make_agent(scope)
    if change == "missing_scope":
        agent.model_scope = None
    elif change == "missing_client":
        scope.fleet_client = None
    remote = provider(endpoint, "model_services")
    plugin = BoundManagementPlugin("model_services", lambda _: AgentToolBindings({"model_services": remote}))
    await PantheonTeam([agent], plugins=[plugin]).async_setup()
    try:
        with pytest.raises((ValueError, RuntimeError)):
            await agent.call_tool("model_services__use_fleet_model", {"model_ref": "fleet-model://test/qwen"})
        assert agent.models == ["openai/gpt-4o-mini"]
    finally:
        await plugin.on_shutdown()
        await remote.shutdown()


@pytest.mark.asyncio
async def test_scoped_task_state_and_output_verification_belong_to_composition(scopes, tmp_path):
    owners, calls = [], []
    # Identical conversation/project metadata must not collapse two App stores.
    context = {"chat_id": "same-chat", "project_root": str(tmp_path / "shared-workspace")}
    for label in ("stable", "candidate"):
        scope = scopes()
        settings = configure(scope, {"task_system": {"enabled": True}})
        agent = make_agent(scope)
        def resolver_for(candidate, agent=agent, label=label):
            assert candidate is agent
            async def resolve(path, ctx, node):
                calls.append((label, path, node))
                return {"exists": True, "is_dir": False, "store_path": path,
                        "source": {"node_id": node, "path": path}}
            return resolve
        plugins = await create_app_plugins(settings=settings, model_scope=scope,
            bindings_for=lambda _: AgentToolBindings(), output_resolver_for=resolver_for)
        team = PantheonTeam([agent], plugins=plugins)
        team._project_dir = context["project_root"]
        await team.async_setup()
        await agent.call_tool("task__register_output", {"path": label + ".txt", "node_id": "remote"}, context)
        assert str(settings.brain_dir) in agent.instructions
        output = await agent.call_tool("task__list_outputs", {}, context)
        assert [o["path"] for o in output["outputs"]] == [label + ".txt"]
        assert (settings.brain_dir / "same-chat" / "task_state.json").exists()
        owners.append(plugins)
    assert not (tmp_path / "shared-workspace" / ".pantheon").exists()
    assert calls == [("stable", "stable.txt", "remote"), ("candidate", "candidate.txt", "remote")]
    for plugins in owners:
        await asyncio.gather(*(p.on_shutdown() for p in plugins))


@pytest.mark.asyncio
async def test_missing_plugin_capabilities_fail_readiness(scopes):
    for section in ("task_system", "memory_system", "learning_system"):
        scope = scopes()
        settings = configure(scope, {section: {"enabled": True}})
        with pytest.raises(PluginInitializationError) as failure:
            await create_app_plugins(settings=settings, model_scope=scope,
                                     bindings_for=lambda _: AgentToolBindings())
        assert failure.value.plugin_name == section


@pytest.mark.asyncio
async def test_actual_runtime_initializes_full_explicit_plugin_composition_and_drains(scopes, endpoint):
    from pantheon.chatroom.environment import AgentEnvironment
    from pantheon.chatroom.runtime import AgentRuntime
    scope = scopes()
    settings = configure(scope, {
        **{name: {"enabled": True} for name in ("task_system", "think_system", "fleet_system",
                                               "model_services_system", "memory_system", "learning_system")},
        "context_compression": {"enable": True},
    })
    files = provider(endpoint, "file_manager")
    async def create():
        return await create_app_plugins(settings=settings, model_scope=scope,
            bindings_for=lambda _: AgentToolBindings(),
            auxiliary_bindings=AgentToolBindings({"file_manager": files}),
            output_resolver_for=lambda _: AsyncMock())
    async def close():
        await files.shutdown()
    env = AgentEnvironment(projects=SimpleNamespace(list_projects=lambda: [], active_project=None),
        templates=object(), settings=lambda: settings, ensure_services=AsyncMock(),
        create_agents=AsyncMock(), validate_model=lambda _: (True, ""),
        create_plugins=create, close_agents=close)
    runtime = AgentRuntime(memory_dir=str(settings.pantheon_dir / "conversations"), environment=env)
    try:
        plugins = await runtime._ensure_plugins()
        assert len(plugins) == 7
        assert all(p.required_for_setup for p in plugins)
        owned = [p.runtime for p in plugins if hasattr(p, "runtime")]
        assert len(owned) == 2
        assert all(r.execution.snapshot().scope is scope for r in owned)
        assert all(r.execution.tool_bindings.toolsets["file_manager"] is files for r in owned)
        assert (await files.call_tool("execute", {"command": "before drain"}))["session"] == "session-a"
    finally:
        await runtime.cleanup()
    with pytest.raises(RuntimeError, match="closed"):
        await files.call_tool("execute", {"command": "after drain"})


@pytest.mark.asyncio
async def test_route_selection_requires_target_context_metadata(scopes, endpoint):
    ref = "fleet-route://chat-model"
    metadata = {"tools": True, "operations": ["text"], "context": 65536}
    client = SimpleNamespace(
        route_operation=AsyncMock(return_value={"candidates": [{"deployment_id": "service", "model_id": "qwen"}]}),
        describe=AsyncMock(return_value=({}, metadata)))
    agent = make_agent(scopes(fleet_client=client))
    remote = provider(endpoint, "model_services")
    plugin = BoundManagementPlugin("model_services", lambda _: AgentToolBindings({"model_services": remote}))
    await PantheonTeam([agent], plugins=[plugin]).async_setup()
    try:
        await agent.call_tool("model_services__use_fleet_model", {"model_ref": ref})
        client.route_operation.assert_awaited_once_with("resolve", route_id="chat-model",
                                                       requires={"operation": "text", "tools": True})
        assert agent.models == [ref]
        agent.models = ["openai/gpt-4o-mini"]
        metadata["context"] = None
        with pytest.raises(ValueError, match="context"):
            await agent.call_tool("model_services__use_fleet_model", {"model_ref": ref})
        assert agent.models == ["openai/gpt-4o-mini"]
    finally:
        await plugin.on_shutdown()
        await remote.shutdown()


@pytest.mark.asyncio
async def test_cancelled_required_binding_never_retries_partial_team(scopes):
    from pantheon.team.plugin import TeamPlugin
    entered = asyncio.Event()
    class Pending(TeamPlugin):
        required_for_setup = True
        async def get_toolsets(self, team):
            team.team_agents[0]._ephemeral_hooks.append(lambda *_: [])
            entered.set()
            await asyncio.Event().wait()
        async def on_team_created(self, team):
            pass
    agent = make_agent(scopes())
    team = PantheonTeam([agent], plugins=[Pending()])
    setup = asyncio.create_task(team.async_setup())
    await entered.wait()
    setup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await setup
    with pytest.raises(RuntimeError, match="setup failed"):
        await team.async_setup()
    assert len(agent._ephemeral_hooks) == 1


@pytest.mark.asyncio
async def test_scoped_task_never_falls_back_to_ambient_output_or_headless(scopes, monkeypatch):
    from pantheon.apps.builtin.task import TaskToolSet
    scope = scopes()
    monkeypatch.setenv("PANTHEON_HEADLESS", "1")
    monkeypatch.setattr("pantheon.apps.builtin.task.output_paths.output_metadata", forbidden)
    tasks = TaskToolSet(settings=scope.settings, brain_dir=scope.settings.brain_dir)
    response = await tasks.register_output("result.txt")
    assert response["code"] == "output_verification_unavailable"
    # Ambient headless mode cannot suppress this App's decision checkpoint.
    response = await tasks.task_boundary("test", "EXECUTION", "test", "test", 1)
    assert response["success"] is False and "CHECKPOINT" in response["error"]


def test_scoped_task_rejects_context_path_escape(scopes, tmp_path):
    from pantheon.apps.builtin.task import TaskToolSet
    scope = scopes()
    tasks = TaskToolSet(settings=scope.settings, brain_dir=scope.settings.brain_dir)
    for identity in ("..", "../../other", "/absolute", "windows\\other", 3, 0, "control\n"):
        with pytest.raises(ValueError):
            tasks._get_brain_dir({"chat_id": identity})
    scope.settings.brain_dir.mkdir(parents=True, exist_ok=True)
    (scope.settings.brain_dir / "escaped").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        tasks._get_brain_dir({"chat_id": "escaped"})
