"""Ordinary Model Services management tools, independent of Agent/team imports.

The composition root owns the supplied manager and its connections. Team model
selection remains in the Agent; management preserves the original operations.
"""
from pantheon.toolset import ToolSet, tool


class ModelManagementToolSet(ToolSet):
    def __init__(self, manager, name="model_services", **kwargs):
        super().__init__(name, **kwargs)
        if manager is None:
            raise ValueError('Model management requires an explicitly supplied manager')
        self._manager = manager

    async def _m(self):
        if self._manager.resolver and not self._manager.resolver._client:
            await self._manager.resolver._ensure_client()
        return self._manager

    async def _cloud_manager(self):
        from .errors import ControlError
        manager = await self._m()
        if not getattr(manager.client, 'modal_available', True):
            raise ControlError(503, 'Modal requires an explicitly configured cloud account')
        return manager

    @tool
    async def model_services_overview(self) -> dict:
        """List the user's self-deployed model services, routes, running Modal GPU services and the launchable catalog.

        Returns deployments (id, name, state, node, published models with tools/context),
        routes (fleet-route:// refs), modal_gpu services, and catalog models/GPUs for modal_gpu_start.
        """
        from pantheon.models import modal_gpu
        from pantheon.models.managed import module
        m = await self._m()
        modal_available = getattr(m.client, 'modal_available', True)
        if modal_available:
            await modal_gpu.settle_expired(m)
        deployments = [dict(deployment_id=d['deployment_id'], name=d.get('name'), state=d['state'], node=d.get('node_name') or d['node_id'],
                            engine=d.get('engine'), mode=d.get('mode'),
                            models=[dict(ref=f"fleet-model://{d['deployment_id']}/{x['id']}", name=x.get('name') or x['id'],
                                         operations=x.get('operations'), tools=x.get('tools'), context=x.get('context'))
                                    for x in d.get('models') or []])
                       for d in await m.client.deployments()]
        routes = [dict(ref='fleet-route://' + r['route_id'], name=r['name'], candidates=r['candidates'])
                  for r in await m.client.routes()]
        return dict(deployments=deployments, routes=routes,
                    modal_available=modal_available,
                    modal_unavailable_reason=None if modal_available else 'No cloud account is configured for this App',
                    modal_gpu=await modal_gpu.services(m) if modal_available else None,
                    catalog=[dict(model_id=x['id'], name=x['display_name'], context_length=x['context_length'],
                                  weights_gb=round(sum(f['size'] for f in x['files']) / 1e9, 1), capabilities=x['capabilities'])
                             for x in module('llm_models').catalog()],
                    gpus=sorted(modal_gpu.GPUS))

    @tool
    async def modal_gpu_start(self, service_id: str, model_id: str = 'qwen3.6-35b-a3b-fp8', gpu: str = 'H100',
                              lifetime_hours: float = 4, user_confirmed: bool = False, gpu_count: int = 1) -> dict:
        """Launch a pinned catalog model on a platform Modal GPU (billed per GPU hour).

        Only call with user_confirmed=True after the user approved this exact launch via notify_user.

        Args:
            service_id: Short lowercase id (letters, digits, dashes), e.g. "qwen36". The model is then
                reachable as fleet-route://<service_id>.
            model_id: Catalog model id from model_services_overview.
            gpu: "H100", "H200", "B200", "A100-80GB" or "L40S".
            lifetime_hours: Auto-stop after this many hours (max 24).
            user_confirmed: Must be True; set only after explicit user approval.
            gpu_count: GPUs on the one machine (1, 2, 4 or 8); the model runs tensor parallel across them.
        """
        if not user_confirmed:
            return dict(started=False, message='Ask the user to approve this GPU launch with notify_user '
                        f'(model {model_id}, {gpu_count}x {gpu}, up to {lifetime_hours} h), then call again with user_confirmed=True.')
        if not 0 < lifetime_hours <= 24:
            raise ValueError('lifetime_hours must be between 0 and 24')
        from pantheon.models import modal_gpu
        return await modal_gpu.start(await self._cloud_manager(), service_id, model_id, gpu, int(lifetime_hours * 60), gpu_count)

    @tool
    async def model_options(self, node_id: str = '', gpu: str = '') -> dict:
        """Which engines (SGLang, Ollama) and models fit a node (node_id) or a new Modal machine (gpu: H100, H200,
        B200, A100-80GB, L40S or none). Each model says whether it fits, why not, and how many GPUs it needs
        (a model too large for one GPU runs on 2, 4 or 8 GPUs of one machine)."""
        from pantheon.models import model_deploy
        return await model_deploy.options(await self._m(), node_id, gpu)

    @tool
    async def search_models(self, engine: str, query: str, node_id: str = '', gpu: str = '', limit: int = 10) -> dict:
        """Search deployable models: engine 'sglang' searches Hugging Face (marked by what SGLang 0.5.20 serves),
        'ollama' searches the Ollama library (name + size tags like qwen3:8b) and lists models already on node_id.
        Pass a hit to deploy_model as repo (Hugging Face id, or Ollama name:tag). SGLang hits report gpus_needed."""
        from pantheon.models import model_deploy
        return await model_deploy.search(await self._m(), engine, query, node_id, gpu, limit)

    @tool
    async def deploy_model(self, engine: str, model_id: str = '', repo: str = '', file: str = '', revision: str = '',
                           node_id: str = '', gpu: str = '', lifetime_hours: float = 4, name: str = '',
                           user_confirmed: bool = False, gpu_count: int = 0) -> dict:
        """Deploy a model with SGLang or Ollama on one of the user's nodes (node_id) or a new Modal machine (gpu).

        Use a catalog model_id from model_options, or pin a public Hugging Face model: repo (+ file for an Ollama
        GGUF). A new Modal machine gets as many GPUs as the model needs (gpu_count 0), e.g. DeepSeek V4 Flash on
        4x H100; it is billed per GPU hour: only call with user_confirmed=True after the user approved engine,
        model, GPU type and count and time limit via notify_user. Then call deploy_status until ready.
        """
        from pantheon.models import model_deploy
        if not node_id and not user_confirmed:
            return dict(started=False, message='Ask the user to approve this Modal launch with notify_user '
                        f'({engine}, {model_id or repo}, GPU {gpu or "H100"}' + (f' x{gpu_count}' if gpu_count else ' (count from model_options/search)')
                        + f', up to {lifetime_hours} h), then call again with user_confirmed=True.')
        manager = await self._m() if node_id else await self._cloud_manager()
        if model_id:
            model = {'catalog_id': model_id}
        elif repo:
            model = (await model_deploy.resolve(engine, repo, revision, file))['model']
        else:
            raise ValueError('Give a catalog model_id or a Hugging Face repo')
        target = {'kind': 'node', 'node_id': node_id} if node_id else {
            'kind': 'modal', 'gpu': gpu or 'H100', 'lifetime_hours': lifetime_hours,
            **({'gpu_count': gpu_count} if gpu_count else {})}
        return await model_deploy.deploy(manager, target, engine, model, name)

    @tool
    async def deploy_status(self, deployment_id: str) -> dict:
        """Advance and report a deployment started with deploy_model (phases up to ready, with its model ref)."""
        from pantheon.models import model_deploy
        return await model_deploy.status(await self._m(), deployment_id)

    @tool
    async def modal_gpu_status(self, service_id: str, model_id: str = 'qwen3.6-35b-a3b-fp8') -> dict:
        """Advance and report a Modal GPU model service: starting_node, downloading_weights, starting_engine, ready (with its refs), failed or stopped."""
        from pantheon.models import modal_gpu
        return await modal_gpu.advance(await self._cloud_manager(), service_id, model_id)

    @tool
    async def modal_gpu_stop(self, service_id: str) -> dict:
        """Stop a Modal GPU model service: stop its engine, end the GPU and revoke its node."""
        from pantheon.models import modal_gpu
        return await modal_gpu.stop(await self._cloud_manager(), service_id)

    @tool
    async def model_service_set_running(self, deployment_id: str, running: bool) -> dict:
        """Start (running=True) or stop an existing self-deployed model service on its Fleet node."""
        row = await (await self._m()).set_running(deployment_id, running)
        return dict(deployment_id=deployment_id, state=row['state'])
