"""Authenticated per-user platform RPC host, separate from Agent execution."""

import asyncio
import threading
import time
from pathlib import Path

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger

from .apps_api import AppServicesAPI
from .fleet_api import FleetAPI
from .models_api import ModelServicesAPI
from .projects_api import ProjectsAPI
from .health import PlatformHealth
from .store_api import StoreAPI
from .model_directory import ModelDirectoryAPI
from .oauth_api import OAuthAPI


class PlatformService(OAuthAPI, ModelDirectoryAPI, StoreAPI, PlatformHealth, AppServicesAPI, FleetAPI, ModelServicesAPI, ProjectsAPI, ToolSet):
    """Serve platform operations on the existing user-scoped service bus.

    Deployment supplies the NATS credentials and Fleet coordinates, just as it
    does for other service workers. This host neither issues a broader identity
    nor proxies through ChatRoom. Desktop and Hub routing migrate separately.
    """

    def __init__(self, name: str = "pantheon-platform", workspace_path: str | None = None,
                 app_preset=None, app_preset_source=None, model_credential_preparer=None,
                 owner_state_directory=None, **kwargs):
        from .owner_state import configured_directory
        self._owner_state_directory = configured_directory(owner_state_directory)
        self.workspace_path = str(Path(workspace_path or Path.cwd()).resolve())
        self._project_manager = None
        self._project_manager_lock = threading.Lock()
        self._started_monotonic = time.monotonic()
        self._model_credential_preparer = model_credential_preparer
        # The legacy worker's re-exec bypasses snapshot shutdown and assumes it
        # owns Agent/browser processes. Platform restarts use its supervisor.
        kwargs["allow_in_place_restart"] = False
        super().__init__(name=name, **kwargs)
        from .app_preset import AppPreset
        self._app_preset_source = app_preset_source
        self._app_preset = AppPreset(app_preset, advance=self._advance_app_preset, load=app_preset_source,
                                     resume=self._resume_app_preset)

    async def _resume_app_preset(self, recipe, everything=False):
        """(spec | None, done): restart the preset Apps a replaced node lost.

        The resume in progress is kept privately so a restarted platform
        continues that exact operation rather than re-deciding from a partly
        started node. See preset_resume for what is (not) resumed.
        """
        import json
        import os
        from pantheon.apps.lifecycle import FleetLifecycle
        from pantheon.apps.resolver import get_shared_resolver
        from . import preset_resume
        from .app_preset import startup_recipe
        from .first_run import retire_stale_registrations
        pending = Path(self._owner_state_directory or (Path.home() / '.pantheon' / 'platform-private')) / 'preset-resume.json'

        def done(_spec):
            pending.unlink(missing_ok=True)
        if pending.is_file():
            spec = json.loads(pending.read_text())
            if spec.get('owner') == recipe['owner'] and spec.get('operation_id', '').startswith(
                    recipe['operation_id'][:60] + '-resume-'):
                return startup_recipe(spec), done
            pending.unlink()  # belongs to an earlier preset
        resolver = get_shared_resolver()
        if resolver is None:
            return None, done
        await resolver._ensure_client()
        lifecycle = FleetLifecycle(resolver)
        nodes = {app['node_id'] for app in preset_resume.targets(recipe).values()}
        states = {node: await lifecycle.status(node) for node in nodes}
        spec = preset_resume.resume_recipe(recipe, states, everything=everything)
        if spec is None:
            await self._keep_preset_alive(lifecycle, recipe, states)
            return None, done
        # The lost connector re-registers at its next generation.
        for item in (spec.get('model_apps') or {}).values():
            node = item['app']['node_id']
            await retire_stale_registrations(self._model_services_manager().client, states,
                                             [item['deployment_id']], node)
        spec = startup_recipe(spec)
        pending.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(spec, stream)
        logger.info(f"[app-preset] resuming {sorted(spec['apps'])} after node loss as {spec['operation_id']}")
        return spec, done

    async def _keep_preset_alive(self, lifecycle, recipe, states):
        """Exempt running preset Apps from idle stop, once per instance generation.

        Preset Apps are one generation-bound graph (grants pin exact consumer
        and provider generations), so one idle-stopped App would strand the
        rest. An owner may still turn this off in Fleet; it is not re-applied
        to the same generation.
        """
        from . import preset_resume
        applied = self.__dict__.setdefault('_preset_kept_alive', set())
        for app in preset_resume.targets(recipe).values():
            for instance in (states[app['node_id']].get('instances') or {}).values():
                key = (instance.get('instance_id'), instance.get('generation'))
                if (instance.get('digest') != app['revision'] or instance.get('scope') != app['scope']
                        or instance.get('state') != 'ready' or instance.get('keep_alive') or key in applied):
                    continue
                applied.add(key)
                try:
                    await lifecycle.usage(app['node_id'], 'keep_alive', instance_id=instance['instance_id'],
                                          revision=instance['digest'], generation=instance['generation'],
                                          keep_alive=True)
                except Exception as exc:
                    logger.warning(f"[app-preset] keep-alive for {app['scope']} failed: {exc}")

    @tool(exclude=True)
    async def platform_app_preset_start(self) -> dict:
        """Start the owner's startup Apps again after they were stopped.

        Preset Apps restart together (their grants pin exact generations): Apps
        still running are stopped first, then all start at their next generation.
        """
        recipe = self._app_preset.recipe
        if recipe is None or self._app_preset.status().get('state') != 'ready':
            return {'success': False, 'error': 'The startup preset is not ready; inspect it in Fleet → Startup apps'}
        task = getattr(self, '_preset_start_task', None)
        if task is not None and not task.done():
            return {'success': True, 'state': 'starting'}

        async def restart():
            import uuid
            from pantheon.apps.lifecycle import FleetLifecycle
            from pantheon.apps.resolver import get_shared_resolver
            from . import preset_resume
            try:
                resolver = get_shared_resolver()
                await resolver._ensure_client()
                lifecycle = FleetLifecycle(resolver)
                pending = []
                for app in preset_resume.targets(recipe).values():
                    state = await lifecycle.status(app['node_id'])
                    for instance in (state.get('instances') or {}).values():
                        if (instance.get('digest') == app['revision'] and instance.get('scope') == app['scope']
                                and instance.get('state') not in ('stopped', 'failed')):
                            operation_id = 'preset-stop-' + uuid.uuid4().hex[:16]
                            await lifecycle.submit(app['node_id'], 'stop', app['revision'], scope=app['scope'],
                                                   generation=instance['generation'], operation_id=operation_id)
                            pending.append((app['node_id'], operation_id))
                deadline = time.monotonic() + 300
                while pending and time.monotonic() < deadline:
                    await asyncio.sleep(2)
                    still = []
                    for node, operation_id in pending:
                        operation = ((await lifecycle.status(node)).get('operations') or {}).get(operation_id) or {}
                        if operation.get('state') not in ('succeeded', 'failed'):
                            still.append((node, operation_id))
                    pending = still
                self._app_preset.request_start()
            except Exception as exc:
                logger.warning(f'[app-preset] owner start failed: {exc}')
        self._preset_start_task = asyncio.create_task(restart())
        return {'success': True, 'state': 'starting'}

    async def _advance_app_preset(self, **spec):
        if spec.get('kind') == 'model-services':
            return await self.model_services_bootstrap(**{k: v for k, v in spec.items() if k != 'kind'})
        return await self.fleet_app_deploy(**spec)

    async def run_setup(self):
        from .owner_state import prepare_directory
        # Prepare before any maintenance/startup writer. Merely constructing a
        # service or previewing a recipe does not create state directories.
        prepare_directory(self._owner_state_directory)
        if self.worker is not None and hasattr(self.worker, "set_activity_callback"):
            self.worker.set_activity_callback(self._get_platform_status)
        self._start_dependency_maintenance()
        self._app_preset.start()

    def _get_platform_status(self):
        # A platform ping is not a statement that all hosted Apps are idle.
        return {**self._get_host_metrics(), "activity_scope": "platform"}

    def _projects(self):
        # Avoid scanning a network-backed workspace on the readiness path.
        with self._project_manager_lock:
            if self._project_manager is None:
                from .projects import ProjectManager
                self._project_manager = ProjectManager(
                    active_path=self.workspace_path, activate_on_start=False)
            return self._project_manager

    def _provider_settings(self):
        from pantheon.settings import Settings
        return Settings(self._store_workdir(), isolated_env=True)

    @tool(exclude=True)
    async def platform_info(self) -> dict:
        """Advertise implemented platform endpoints without launching Apps."""
        return {
            "service": "pantheon-platform",
            "api_version": 1,
            "methods": sorted(self.functions),
        }

    @tool(exclude=True)
    async def platform_app_preset_status(self) -> dict:
        """Startup progress only; never substitutes for live App/node health."""
        return self._app_preset.status()

    @tool(exclude=True)
    async def platform_app_preset_error(self) -> dict:
        """Why startup needs attention (bounded message), or an empty string."""
        return {'error': self._app_preset.last_error}

    @tool(exclude=True)
    async def platform_app_preset_reload(self) -> dict:
        """Read the Hub startup preset again after the owner saved one.

        Only when no preset is running: a pending or ready deployment keeps its
        original operation; recovery stays an explicit Fleet action.
        """
        from .app_preset import AppPreset
        status = self._app_preset.status()
        if self._app_preset_source is None:
            return {'success': False, 'error': 'This platform has no Hub startup preset'}
        if status.get('state') not in ('disabled',) and status.get('reason') != 'preset_unavailable':
            return {'success': False, 'error': 'A startup preset is already in progress', 'status': status}
        await self._app_preset.stop()
        self._app_preset = AppPreset(None, advance=self._advance_app_preset, load=self._app_preset_source,
                                     resume=self._resume_app_preset)
        self._app_preset.start()
        return {'success': True, 'status': self._app_preset.status()}

    @tool(exclude=True)
    async def platform_agent_setup(self, platform_key: str, budget: dict, tiers: dict | None = None) -> dict:
        """First-run General Team for this cloud platform (see first_run).

        platform_key: a revocable key the owner just minted (delivered only to
        their workspace node). budget: their Hub /me/llm-proxy answer. Returns
        the recipe for the owner to save; nothing is started here.
        """
        import hashlib
        import os
        import time
        from pantheon.apps.resolver import get_shared_resolver
        from . import first_run
        user = os.environ.get('USER_ID', '')
        hub, controller = os.environ.get('PANTHEON_HUB_URL', ''), os.environ.get('FLEET_CONTROLLER_URL', '')
        resolver = get_shared_resolver()
        if not (user and hub and controller and resolver):
            return {'success': False, 'error': 'This platform is not paired with a Hub and Fleet'}
        state = self._owner_state_directory or (Path.home() / '.pantheon' / 'platform-private')
        try:
            result = await first_run.prepare(
                resolver=resolver, owner='f_' + hashlib.sha256(user.encode()).hexdigest()[:16],
                hub=hub, controller=controller, platform_key=platform_key, budget=budget, tiers=tiers,
                cache=state / 'releases', operation_id=f'agent-setup-{int(time.time())}',
                directory=self._model_services_manager().client)
        except Exception as exc:
            return {'success': False, 'error': str(exc) or type(exc).__name__}
        return {'success': True, **result}

    async def cleanup(self):
        await self._app_preset.stop()
        # Release login waiters before draining accepted RPCs.
        await self._stop_oauth()
        # Stop accepting platform mutations before shutdown's final snapshot.
        worker = getattr(self, "worker", None)
        if worker is not None:
            await getattr(worker, "drain", worker.stop)()
        await self._stop_dependency_maintenance()
        backend = getattr(self, "_backend", None)
        connection = getattr(backend, "_nc", None)
        if connection is not None:
            await connection.close()
        await self._stop_model_directory()
        await self._stop_health_refresh()
        task = getattr(self, "_fleet_session_task", None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._fleet_session_task = None
        fleet = getattr(self, "_fleet_ts", None)
        if fleet is not None:
            await fleet.cleanup()
            self._fleet_ts = None
        manager = getattr(self, "_model_services", None)
        if manager is not None:
            try:
                await manager.client.aclose()
            finally:
                if manager.resolver is not None:
                    await manager.resolver.close()
                del self._model_services
        self._fleet_session_started = False
