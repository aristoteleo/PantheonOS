"""Model deployment management RPCs independent of Agent execution.

ChatRoom temporarily inherits this API for wire compatibility. The platform
service serves the same implementation without loading ChatRoom.
"""

from pantheon.toolset import tool
from pantheon.utils.log import logger


class ModelServicesAPI:
    def _model_services_manager(self):
        from pantheon.models.manager import ModelServiceManager
        if not hasattr(self, '_model_services'):
            self._model_services = ModelServiceManager()
        return self._model_services

    @tool(exclude=True)
    async def model_services_agent_preset(self, spec: dict, fleet_tiers: dict,
                                         allow_wake: bool = False) -> dict:
        """Preview an Agent App preset using existing Model Service publications.

        spec supplies owner, operation_id, App targets/configuration, tool policies
        and node-vault references. fleet_tiers maps normal/high/low to exact model
        or route references. Returns the generic deployable recipe plus the
        connector-wide authorization scope for review. Does not start/save Apps.
        """
        from pantheon.apps.agent_deployment import compose_selected_deployment
        result = await compose_selected_deployment(self._model_services_manager().client,
            spec=spec, fleet_tiers=fleet_tiers, allow_wake=allow_wake)
        return {'success': True, **result}

    @tool(exclude=True)
    async def model_services_agent_preset_update(self, recipe: dict, operation_id: str,
                                                fleet_tiers: dict, allow_wake: bool = False) -> dict:
        """Preview new models for an existing canonical Agent preset; never save/start."""
        from pantheon.apps.agent_deployment import update_selected_deployment
        result = await update_selected_deployment(self._model_services_manager().client,
            recipe=recipe, operation_id=operation_id, fleet_tiers=fleet_tiers, allow_wake=allow_wake)
        return {'success': True, **result}


    def _model_service_bootstrap(self):
        from pantheon.models.bootstrap import ModelServiceBootstrap
        deployment = self._app_deployments()
        if deployment is None:
            raise ValueError('Fleet is not connected')
        return ModelServiceBootstrap(deployment, self._model_services_manager(),
                                     deployment.root.parent / 'model-startup',
                                     prepare_credentials=getattr(self, '_model_credential_preparer', None))

    @tool(exclude=True)
    async def model_services_bootstrap(self, owner: str, operation_id: str, action: str = 'advance',
                                       apps: dict | None = None, model_apps: dict | None = None) -> dict:
        """Resume original provider registration and consumer App startup; never auto-heal."""
        bootstrap = self._model_service_bootstrap()
        if action == 'inspect':
            if apps is not None or model_apps is not None:
                raise ValueError('Inspect the original startup without a new recipe')
            result = bootstrap.inspect(owner=owner, operation_id=operation_id)
        elif action == 'advance':
            self._start_dependency_maintenance()
            result = await bootstrap.advance(owner=owner, operation_id=operation_id, apps=apps, model_apps=model_apps)
        else:
            raise ValueError('Unsupported model startup action')
        return {'success': True, **result}

    @tool(exclude=True)
    async def model_services_list(self) -> dict:
        return {'deployments': await self._model_services_manager().client.deployments()}


    @tool(exclude=True)
    async def model_services_modal_gpu(self, action: str = 'list', service_id: str = '',
                                       model_id: str = 'qwen3.6-35b-a3b-fp8', gpu: str = 'H100',
                                       lifetime_minutes: int = 240, cpu: int | None = None,
                                       memory_gib: int | None = None, gpu_count: int = 1) -> dict:
        """Run a pinned catalog LLM on a platform Modal GPU node (start/advance/stop/list/catalog), or a bare GPU/CPU node (start_node/stop_node); gpu_count GPUs on one machine."""
        from pantheon.models import modal_gpu
        manager = self._model_services_manager()
        if manager.resolver and not manager.resolver._client:
            await manager.resolver._ensure_client()
        if action == 'catalog':
            from pantheon.models.managed import module
            return {'models': [{k: m[k] for k in ('id', 'display_name', 'context_length', 'maximum_context_length',
                                                  'minimum_gpu_memory_bytes', 'capabilities')}
                               | {'supported_gpus': m.get('supported_gpus') or sorted(modal_gpu.GPUS)}
                               | {'size': sum(f['size'] for f in m['files'])} for m in module('llm_models').catalog()],
                    'gpus': sorted(modal_gpu.GPUS),
                    'node_options': {'gpus': sorted(modal_gpu.GPUS) + ['none'], 'cpu': modal_gpu.NODE_CPU,
                                     'memory_gib': modal_gpu.NODE_MEMORY_GIB, 'gpu_counts': list(modal_gpu.GPU_COUNTS)}}
        if action == 'list':
            launches = await modal_gpu.services(manager)
            await modal_gpu.settle_expired(manager, launches)
            return {'services': launches}
        if action == 'start':
            return await modal_gpu.start(manager, service_id, model_id, gpu, lifetime_minutes, gpu_count)
        if action == 'advance':
            return await modal_gpu.advance(manager, service_id, model_id)
        if action == 'stop':
            return await modal_gpu.stop(manager, service_id)
        if action == 'start_node':
            return await modal_gpu.start_node(manager, service_id, gpu, lifetime_minutes, cpu, memory_gib, gpu_count)
        if action == 'stop_node':
            return await modal_gpu.stop_node(manager, service_id)
        raise ValueError('Unsupported Modal GPU service action')


    @tool(exclude=True)
    async def model_services_deploy(self, action: str = 'options', node_id: str = '', gpu: str = '', engine: str = '',
                                    repo: str = '', revision: str = '', file: str = '', target: dict | None = None,
                                    model: dict | None = None, name: str = '', deployment_id: str = '',
                                    query: str = '', limit: int = 20, sort: str = 'popular',
                                    context_length: int | None = None) -> dict:
        """Deploy a model: options (engines + recommended models for a node or a Modal GPU), featured (trending
        and most-used official Hugging Face releases that fit), search (Hugging Face for SGLang, the Ollama library + models on the node for Ollama), resolve (pin a selection),
        deploy (target + engine + model) and status (advance a deployment)."""
        from pantheon.models import model_deploy
        manager = self._model_services_manager()
        if manager.resolver and not manager.resolver._client:
            await manager.resolver._ensure_client()
        if action == 'options':
            return await model_deploy.options(manager, node_id, gpu)
        if action == 'search':
            return await model_deploy.search(manager, engine, query, node_id, gpu, limit, sort)
        if action == 'featured':
            return await model_deploy.featured(manager, node_id, gpu)
        if action == 'resolve':
            return await model_deploy.resolve(engine, repo, revision, file)
        if action == 'deploy':
            return await model_deploy.deploy(manager, target or {}, engine, model or {}, name, context_length)
        if action == 'status':
            return await model_deploy.status(manager, deployment_id)
        raise ValueError('Unsupported model deploy action')


    @tool(exclude=True)
    async def model_services_group_deployments(self, action: str = 'list', group_id: str = '', config: dict | None = None) -> dict:
        """Create or continue an original model group deployment across Fleet nodes."""
        return await self._model_services_manager().group_deployments(action, group_id, config)


    @tool(exclude=True)
    async def model_services_groups(self, action: str = 'list', group_id: str = '') -> dict:
        """Inspect durable model groups or explicitly stop their owned ranks."""
        return await self._model_services_manager().groups(action, group_id)


    @tool(exclude=True)
    async def model_services_activity(self, deployment_id: str, action: str = 'list', request_id: str = '') -> dict:
        return await self._model_services_manager().activity(deployment_id, action, request_id)


    @tool(exclude=True)
    async def model_services_inference_jobs(self, deployment_id: str, policy: str = 'direct_only') -> dict:
        """List durable job metadata on one node without fetching inputs/results."""
        return await self._model_services_manager().client.inference_jobs(deployment_id, policy=policy)


    @tool(exclude=True)
    async def model_services_inference_job(self, ref: str, action: str = 'status', policy: str = 'relay_allowed') -> dict:
        """Observe/cancel a fixed typed job; never resubmit or resolve its alias."""
        return await self._model_services_manager().client.job_operation(ref, action, policy=policy)


    @tool(exclude=True)
    async def model_services_video_recovery(self, ref: str, action: str = 'inspect', ticket: str = '', confirmation: str = '') -> dict:
        """Owner-only recovery; explicit attestation is not engine completion evidence."""
        return await self._model_services_manager().video_recovery(ref, action, ticket, confirmation)


    @tool(exclude=True)
    async def model_services_upgrade_connector(self, deployment_id: str) -> dict:
        return await self._model_services_manager().upgrade_connector(deployment_id)


    @tool(exclude=True)
    async def model_services_upgrade_engine(self, deployment_id: str, recipe_id: str) -> dict:
        return await self._model_services_manager().upgrade_engine(deployment_id, recipe_id)


    @tool(exclude=True)
    async def model_services_recover(self, deployment_id: str) -> dict:
        return await self._model_services_manager().recover(deployment_id)


    @tool(exclude=True)
    async def model_services_stop_operation(self, deployment_id: str, revision: int) -> dict:
        return await self._model_services_manager().stop_operation(deployment_id, revision)


    @tool(exclude=True)
    async def model_services_engine_idle_status(self, deployment_id: str) -> dict:
        return await self._model_services_manager().engine_idle_status(deployment_id)


    @tool(exclude=True)
    async def model_services_engine_idle(self, deployment_id: str, idle_seconds: int, revision: int) -> dict:
        return await self._model_services_manager().set_engine_idle(deployment_id, idle_seconds, revision)


    @tool(exclude=True)
    async def model_services_routes(self, action: str = 'list', route: dict | None = None,
                                    route_id: str = '', revision: int = 0, requires: dict | None = None) -> dict:
        return await self._model_services_manager().client.route_operation(action, route, route_id, revision, requires)


    @tool(exclude=True)
    async def model_services_register_prepared(self, deployment_id: str, name: str, binding: dict,
                                               configuration: dict, models: list[dict]) -> dict:
        """Register a prepared connector and explicitly selected chat models."""
        return await self._model_services_manager().register_prepared(deployment_id, name, binding, configuration, models)


    @tool(exclude=True)
    async def model_services_attach(self, deployment_id: str, name: str, node_id: str,
                                    engine: str, endpoint: str, credential_file: str = '', secret_ref: str = '') -> dict:
        return await self._model_services_manager().attach(deployment_id, name, node_id, engine, endpoint, credential_file, secret_ref)


    @tool(exclude=True)
    async def model_services_discover(self, deployment_id: str) -> dict:
        return await self._model_services_manager().discover(deployment_id)


    @tool(exclude=True)
    async def model_services_publish(self, deployment_id: str, models: list[dict], revision: int) -> dict:
        return await self._model_services_manager().publish(deployment_id, models, revision)


    @tool(exclude=True)
    async def model_services_remove(self, deployment_id: str, revision: int) -> dict:
        """Remove a stopped model service from the directory (node caches are kept)."""
        return await self._model_services_manager().remove(deployment_id, revision)


    @tool(exclude=True)
    async def model_services_set_running(self, deployment_id: str, running: bool) -> dict:
        manager = self._model_services_manager()
        row = await manager.set_running(deployment_id, running)
        if running and row.get('state') == 'ready' and row.get('models'):
            try:
                # A started service states its models' current context/capabilities.
                row = await manager.sync_models(deployment_id)
            except Exception as error:
                logger.warning(f'Could not sync models of {deployment_id}: {error}')
        return row


    @tool(exclude=True)
    async def model_services_service_models(self, deployment_id: str) -> dict:
        """Models a running service offers, what it reports and which ones chat uses."""
        return await self._model_services_manager().service_models(deployment_id)


    @tool(exclude=True)
    async def model_services_set_in_chat(self, deployment_id: str, model_id: str, enabled: bool,
                                         context_limit: int | None = None) -> dict:
        """Offer a service's model to Agent/Playground (or withdraw it). Capabilities come
        from the service; context_limit caps its context (0 removes the cap)."""
        return await self._model_services_manager().set_in_chat(deployment_id, model_id, enabled, context_limit)


    @tool(exclude=True)
    async def model_services_artifacts(self, deployment_id: str, action: str = 'list',
                                       job_id: str = '', source: dict | None = None,
                                       resume: bool = False) -> dict:
        return await self._model_services_manager().artifacts(deployment_id, action, job_id, source, resume)


    @tool(exclude=True)
    async def model_services_resources(self, node_id: str) -> dict:
        return await self._model_services_manager().resources(node_id)


    @tool(exclude=True)
    async def model_services_snapshots(self, deployment_id: str, action: str = 'jobs',
                                       artifact_job_id: str = '', resume: bool = False, job_id: str = '') -> dict:
        return await self._model_services_manager().snapshots(deployment_id, action, artifact_job_id, resume, job_id)


    @tool(exclude=True)
    async def model_services_speech_models(self, deployment_id: str, action: str = 'catalog',
                                          model_id: str = '', resume: bool = False) -> dict:
        return await self._model_services_manager().speech_models(deployment_id, action, model_id, resume)


    @tool(exclude=True)
    async def model_services_diffusion_models(self, deployment_id: str, action: str = 'catalog',
                                             model_id: str = '', resume: bool = False) -> dict:
        return await self._model_services_manager().diffusion_models(deployment_id, action, model_id, resume)


    @tool(exclude=True)
    async def model_services_llm_models(self, deployment_id: str, action: str = 'catalog',
                                       model_id: str = '', resume: bool = False) -> dict:
        return await self._model_services_manager().llm_models(deployment_id, action, model_id, resume)


    @tool(exclude=True)
    async def model_services_create_managed(self, deployment_id: str, name: str, node_id: str, config: dict) -> dict:
        return await self._model_services_manager().create_managed(deployment_id, name, node_id, config)


    @tool(exclude=True)
    async def model_services_engine_recipes(self, node_id: str) -> dict:
        return await self._model_services_manager().engine_recipes(node_id)


    @tool(exclude=True)
    async def model_services_engines(self, deployment_id: str, action: str = 'catalog', recipe_id: str = '', resume: bool = False) -> dict:
        return await self._model_services_manager().engines(deployment_id, action, recipe_id, resume)


    @tool(exclude=True)
    async def model_services_model_operations(self, deployment_id: str, action: str = 'status',
            job_id: str = '', operation: str = '', artifact_job_id: str = '', model_id: str = '', pool_revision: int | None = None) -> dict:
        return await self._model_services_manager().model_operations(deployment_id, action,
            job_id, operation, artifact_job_id, model_id, pool_revision)
