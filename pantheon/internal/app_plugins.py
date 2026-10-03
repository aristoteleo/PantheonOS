"""Plugin assembly for an Agent App with explicitly supplied capabilities.

The instance factory owns management clients. Plugins borrow them, never create
Fleet controllers/managers or inspect process credentials. The composition must
drain plugins before closing instance and auxiliary bindings.
"""
from __future__ import annotations

from pantheon.agent import ToolInfo, ToolProvider
from pantheon.factory.bindings import AgentToolBindings
from pantheon.internal.auxiliary_execution import AuxiliaryExecution
from pantheon.team.plugin import TeamPlugin
from pantheon.team.plugin_registry import create_owned_plugins
from pantheon.utils.model_scope import ModelCallScope


_SELECT = {
    "name": "use_fleet_model",
    "description": "Switch a team agent (default: leader) to a ready, tool-capable Fleet model for subsequent turns.",
    "parameters": {
        "type": "object",
        "properties": {
            "model_ref": {"type": "string", "description": "fleet-model:// or fleet-route:// reference"},
            "agent_name": {"type": "string", "description": "Team member name; omit for leader"},
        },
        "required": ["model_ref"],
        "additionalProperties": False,
    },
}

_FLEET_PROMPT = """
## Remote Compute (Pantheon-Fleet)

The `fleet` toolset provides explicitly authorized operations on the user's
Fleet nodes. Inspect the available nodes before selecting where to run work.
Use only operations present in your tool menu. A Shell or Files binding may run
on a different node from this Agent; never infer its location from the Agent
process or an is_self flag. Keep source and destination nodes explicit when
moving data.
""".strip()

_MODELS_PROMPT = """
## Self-deployed models (Model Services)

The `model_services` toolset provides authorized model management operations.
Use its available tools to inspect deployments, choose engines/models and follow
startup progress. A new paid GPU launch needs the user's approval for the model,
GPU count and lifetime before sending user_confirmed=True. Stop paid resources
when they are no longer needed. Do not repeatedly poll while startup is pending.

`use_fleet_model` selects a ready text model with tools and a confirmed context
limit for a team member. It checks that member's own Model Services access;
selection does not grant additional permissions.
""".strip()


class BoundModelServicesProvider(ToolProvider):
    """Remote management, local team selection using the target's own access.

    Local selection never uses the leader's model client to validate a different
    member's access. Selecting a model does not mint or widen any grant.
    """
    toolset_name = "model_services"

    def __init__(self, remote, team):
        self.remote, self.team = remote, team
        self._closed = False

    async def initialize(self):
        self._check_open()
        await self.remote.initialize()
        if any(tool.name == "use_fleet_model" for tool in await self.remote.list_tools()):
            raise ValueError("Model selection belongs to the Agent; remove it from the remote management binding")

    def _check_open(self):
        if self._closed:
            raise RuntimeError("Model Services binding is closed")

    async def list_tools(self):
        import copy
        self._check_open()
        return [*await self.remote.list_tools(),
                ToolInfo(name=_SELECT["name"], description=_SELECT["description"],
                         inputSchema=copy.deepcopy(_SELECT))]

    async def call_tool(self, name, args):
        self._check_open()
        if name != "use_fleet_model":
            return await self.remote.call_tool(name, args)
        if (not isinstance(args, dict) or not {"model_ref"} <= args.keys()
                or args.keys() - {"model_ref", "agent_name"}
                or not isinstance(args["model_ref"], str)
                or not isinstance(args.get("agent_name", ""), str)):
            raise ValueError("Invalid model selection arguments")
        from pantheon.models.client import parse_ref
        from pantheon.models.routing import parse_route_ref
        ref, member = args["model_ref"], args.get("agent_name", "")
        target = next((a for a in self.team.team_agents if not member or a.name.lower() == member.lower()), None)
        if target is None:
            raise ValueError("Selected Agent is not in this team")
        scope = getattr(target, "model_scope", None)
        if not isinstance(scope, ModelCallScope):
            raise ValueError("Selected Agent has no explicit model scope")
        client = scope.fleet()
        previous = list(target.models)
        if ref.startswith("fleet-route://"):
            route = parse_route_ref(ref)
            plan = await client.route_operation("resolve", route_id=route,
                                                requires={"operation": "text", "tools": True})
            if not isinstance(plan, dict) or not plan.get("candidates"):
                raise ValueError("No ready, tool-capable model backs this route")
            _, spec = await client.describe(ref)
        else:
            deployment, model = parse_ref(ref)
            row = await client.deployment(deployment)
            spec = next((m for m in row.get("models", []) if m.get("id") == model), None)
            if row.get("state") != "ready":
                raise ValueError("Model service is not ready")
        if (not isinstance(spec, dict) or spec.get("tools") is not True
                or type(spec.get("context")) is not int or spec["context"] <= 0
                or "text" not in spec.get("operations", [])):
            raise ValueError("Model must confirm text, tools and a positive context length")
        self._check_open()
        # A delayed check must not replace a newer selection or retired scope.
        if target.models != previous or target.model_scope is not scope:
            raise RuntimeError("Agent model selection changed while checking access; retry explicitly")
        target.models = [ref]
        return dict(agent=target.name, model=ref, previous=previous,
                    note="Applies to this chat from the next turn; choose it in the model picker to keep it.")

    async def shutdown(self):
        self._closed = True
        # The instance owner closes the borrowed remote after all Agent work.


class BoundManagementPlugin(TeamPlugin):
    required_for_setup = True

    def __init__(self, name, bindings_for):
        if name not in {"fleet", "model_services"} or not callable(bindings_for):
            raise ValueError("Supply a management capability and instance binding resolver")
        self.name, self.bindings_for = name, bindings_for
        self._providers = []
        self._stopping = False

    async def get_toolsets(self, team):
        if self._stopping:
            raise RuntimeError("Management plugin is stopping")
        if not team.team_agents:
            return []
        primary = team.team_agents[0]
        bindings = self.bindings_for(primary)
        if not isinstance(bindings, AgentToolBindings) or self.name not in bindings.toolsets:
            raise ValueError("Agent is missing its explicit management dependency")
        remote = bindings.toolsets[self.name]
        provider = BoundModelServicesProvider(remote, team) if self.name == "model_services" else remote
        await provider.initialize()
        if self._stopping:
            raise RuntimeError("Management plugin stopped during binding")
        if provider is not remote:
            self._providers.append(provider)
        return [(provider, [primary.name])]

    async def on_team_created(self, team):
        if self._stopping:
            raise RuntimeError("Management plugin is stopping")
        if not team.team_agents:
            return
        primary = team.team_agents[0]
        prompt = _FLEET_PROMPT if self.name == "fleet" else _MODELS_PROMPT
        if primary.instructions and prompt not in primary.instructions:
            primary.instructions += "\n\n" + prompt

    async def on_shutdown(self):
        self._stopping = True
        for provider in self._providers:
            await provider.shutdown()
        self._providers.clear()


async def create_app_plugins(*, settings, model_scope, bindings_for,
                             auxiliary_bindings=None, output_resolver_for=None):
    """Construct all enabled plugins without falling back to registry factories.

    These are composition inputs, not template fields or permissions. Both
    resolvers must return capabilities bound to the exact supplied Agent object.
    Missing capabilities fail readiness/setup instead of loading ambient tools.
    """
    if not isinstance(model_scope, ModelCallScope) or model_scope.settings is not settings:
        raise ValueError("Plugin settings and model scope must have the same owner")
    if not callable(bindings_for):
        raise ValueError("Plugins require an explicit instance binding resolver")

    from pantheon.internal.task_system.plugin import TaskSystemPlugin
    from pantheon.internal.think_plugin import _create_think_plugin
    from pantheon.internal.compression.plugin import _create_compression_plugin
    from pantheon.internal.memory_system.plugin import _create_memory_plugin
    from pantheon.internal.learning_system.plugin import _create_learning_plugin

    execution = AuxiliaryExecution(model_scope=model_scope, tool_bindings=auxiliary_bindings)

    def task(config, owner_settings):
        if not callable(output_resolver_for):
            raise ValueError("Task output registration requires an explicit Files resolver")
        return TaskSystemPlugin(settings=owner_settings, output_resolver_for=output_resolver_for)

    def auxiliary(factory):
        def make(config, owner_settings):
            if (not isinstance(auxiliary_bindings, AgentToolBindings)
                    or "file_manager" not in auxiliary_bindings.toolsets):
                raise ValueError("Auxiliary work requires an explicit Files dependency")
            return factory(config, owner_settings, execution=execution)
        return make

    plugins = await create_owned_plugins(settings, factories={
        "task_system": task,
        "think_system": _create_think_plugin,
        "fleet_system": lambda *_: BoundManagementPlugin("fleet", bindings_for),
        "model_services_system": lambda *_: BoundManagementPlugin("model_services", bindings_for),
        "memory_system": auxiliary(_create_memory_plugin),
        "learning_system": auxiliary(_create_learning_plugin),
        "compression": _create_compression_plugin,
    })
    for plugin in plugins:
        plugin.required_for_setup = True
    return plugins
