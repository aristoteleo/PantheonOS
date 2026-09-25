"""ModelServicesPlugin — let the leader run and manage the user's self-deployed models.

When the sandbox is wired to a Pantheon-Fleet, the leader gets a
``model_services`` toolset over the same Model Services manager the UI uses:
inspect deployments and routes, start/stop existing services, launch a pinned
catalog model on a platform Modal GPU, and switch a team agent onto a ready
self-deployed model. Launching a GPU costs money, so it requires the user's
explicit approval (obtained with ``notify_user``) in a previous turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pantheon.internal.fleet_plugin import _fleet_configured
from pantheon.team.plugin import TeamPlugin
from pantheon.toolset import ToolSet, tool

if TYPE_CHECKING:
    from pantheon.team.pantheon import PantheonTeam


MODEL_SERVICES_PROMPT = """
## Self-deployed models (Model Services)

You can manage the user's own model deployments with the `model_services`
toolset: list services and routes, start/stop an existing service, run a pinned
catalog model (e.g. Qwen3.6 35B-A3B) on a platform Modal GPU, and switch a team
agent onto a ready self-deployed model.

- Launching a Modal GPU is billed per GPU hour. BEFORE calling
  `modal_gpu_start`, ask the user with `notify_user` (state the model, GPU and
  lifetime) and only call it with `user_confirmed=True` after they approve.
- A launch takes minutes (node start, ~37 GB weights, engine warm-up). Call
  `modal_gpu_status` to advance and report progress; do not busy-loop — check a
  few times, then tell the user it is still starting.
- Stop GPU services the user no longer needs with `modal_gpu_stop`.
- Only switch an agent to a model that is ready and supports tools
  (`use_fleet_model` checks this).
""".strip()


class ModelServicesToolSet(ToolSet):
    """Agent-facing Model Services: the same operations as the Model Services app."""

    def __init__(self, team: "PantheonTeam | None" = None, name: str = "model_services", **kwargs):
        super().__init__(name, **kwargs)
        self._team = team
        self._manager = None

    async def _m(self):
        from pantheon.models.manager import ModelServiceManager
        if self._manager is None:
            self._manager = ModelServiceManager()
        if self._manager.resolver and not self._manager.resolver._client:
            await self._manager.resolver._ensure_client()
        return self._manager

    @tool
    async def model_services_overview(self) -> dict:
        """List the user's self-deployed model services, routes, running Modal GPU services and the launchable catalog.

        Returns deployments (id, name, state, node, published models with tools/context),
        routes (fleet-route:// refs), modal_gpu services, and catalog models/GPUs for modal_gpu_start.
        """
        from pantheon.models import modal_gpu
        from pantheon.models.managed import module
        m = await self._m()
        await modal_gpu.settle_expired(m)
        deployments = [dict(deployment_id=d['deployment_id'], name=d.get('name'), state=d['state'], node=d.get('node_name') or d['node_id'],
                            engine=d.get('engine'), mode=d.get('mode'),
                            models=[dict(ref=f"fleet-model://{d['deployment_id']}/{x['id']}", name=x.get('name') or x['id'],
                                         operations=x.get('operations'), tools=x.get('tools'), context=x.get('context'))
                                    for x in d.get('models') or []])
                       for d in await m.client.deployments()]
        routes = [dict(ref='fleet-route://' + r['route_id'], name=r['name'], candidates=r['candidates'])
                  for r in await m.client.routes()]
        return dict(deployments=deployments, routes=routes, modal_gpu=await modal_gpu.services(m),
                    catalog=[dict(model_id=x['id'], name=x['display_name'], context_length=x['context_length'],
                                  weights_gb=round(sum(f['size'] for f in x['files']) / 1e9, 1), capabilities=x['capabilities'])
                             for x in module('llm_models').catalog()],
                    gpus=sorted(modal_gpu.GPUS))

    @tool
    async def modal_gpu_start(self, service_id: str, model_id: str = 'qwen3.6-35b-a3b-fp8', gpu: str = 'H100',
                              lifetime_hours: float = 4, user_confirmed: bool = False) -> dict:
        """Launch a pinned catalog model on a platform Modal GPU (billed per GPU hour).

        Only call with user_confirmed=True after the user approved this exact launch via notify_user.

        Args:
            service_id: Short lowercase id (letters, digits, dashes), e.g. "qwen36". The model is then
                reachable as fleet-route://<service_id>.
            model_id: Catalog model id from model_services_overview.
            gpu: "H100", "A100-80GB" or "L40S".
            lifetime_hours: Auto-stop after this many hours (max 24).
            user_confirmed: Must be True; set only after explicit user approval.
        """
        if not user_confirmed:
            return dict(started=False, message='Ask the user to approve this GPU launch with notify_user '
                        f'(model {model_id}, GPU {gpu}, up to {lifetime_hours} h), then call again with user_confirmed=True.')
        if not 0 < lifetime_hours <= 24:
            raise ValueError('lifetime_hours must be between 0 and 24')
        from pantheon.models import modal_gpu
        return await modal_gpu.start(await self._m(), service_id, model_id, gpu, int(lifetime_hours * 60))

    @tool
    async def modal_gpu_status(self, service_id: str, model_id: str = 'qwen3.6-35b-a3b-fp8') -> dict:
        """Advance and report a Modal GPU model service: starting_node, downloading_weights, starting_engine, ready (with its refs), failed or stopped."""
        from pantheon.models import modal_gpu
        return await modal_gpu.advance(await self._m(), service_id, model_id)

    @tool
    async def modal_gpu_stop(self, service_id: str) -> dict:
        """Stop a Modal GPU model service: stop its engine, end the GPU and revoke its node."""
        from pantheon.models import modal_gpu
        return await modal_gpu.stop(await self._m(), service_id)

    @tool
    async def model_service_set_running(self, deployment_id: str, running: bool) -> dict:
        """Start (running=True) or stop an existing self-deployed model service on its Fleet node."""
        row = await (await self._m()).set_running(deployment_id, running)
        return dict(deployment_id=deployment_id, state=row['state'])

    @tool
    async def use_fleet_model(self, model_ref: str, agent_name: str = '') -> dict:
        """Switch a team agent (default: the leader) onto a ready self-deployed model for this chat.

        Args:
            model_ref: fleet-model://<deployment>/<model> or fleet-route://<route> from model_services_overview.
            agent_name: Team agent to switch; empty means the leader.
        """
        from pantheon.models.client import parse_ref
        from pantheon.models.routing import parse_route_ref
        if self._team is None or not self._team.team_agents:
            raise ValueError('No team agent is available to switch')
        m = await self._m()
        if model_ref.startswith('fleet-route://'):
            route_id = parse_route_ref(model_ref)
            resolved = await m.client.route_operation('resolve', route_id=route_id, requires={'operation': 'text', 'tools': True})
            if not resolved.get('candidates'):
                raise ValueError('No ready, tool-capable model backs this route yet')
        else:
            deployment_id, model_id = parse_ref(model_ref)
            row = await m.client.deployment(deployment_id)
            spec = next((x for x in row.get('models') or [] if x['id'] == model_id), None)
            if row['state'] != 'ready' or not spec:
                raise ValueError('This model service is not ready or does not publish that model')
            if spec.get('tools') is not True or not spec.get('context'):
                raise ValueError('Publish this model with Tools supported and a context length before using it for an agent')
        target = next((a for a in self._team.team_agents if not agent_name or a.name.lower() == agent_name.lower()), None)
        if target is None:
            raise ValueError(f'Agent {agent_name!r} is not in this team')
        previous = list(target.models)
        target.models = [model_ref]
        return dict(agent=target.name, model=model_ref, previous=previous,
                    note='Applies to this chat from the next turn; choose it in the model picker to keep it.')


class ModelServicesPlugin(TeamPlugin):
    """Inject the Model Services toolset + prompt into the leader when fleet-configured."""

    async def get_toolsets(self, team: "PantheonTeam") -> list[tuple[Any, list[str] | None]]:
        if not _fleet_configured() or not team.team_agents:
            return []
        return [(ModelServicesToolSet(team), [team.team_agents[0].name])]

    async def on_team_created(self, team: "PantheonTeam") -> None:
        if not _fleet_configured() or not team.team_agents:
            return
        primary = team.team_agents[0]
        if not getattr(primary, "instructions", None) or "## Self-deployed models (Model Services)" in primary.instructions:
            return
        primary.instructions += "\n\n" + MODEL_SERVICES_PROMPT


def _create_model_services_plugin(config: dict, settings: Any) -> ModelServicesPlugin:
    return ModelServicesPlugin()


from pantheon.team.plugin_registry import PluginDef, register_plugin

register_plugin(
    PluginDef(
        name="model_services_system",
        config_key="model_services_system",
        enabled_key="enabled",
        factory=_create_model_services_plugin,
        priority=21,
    )
)
