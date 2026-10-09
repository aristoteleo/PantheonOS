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


# Bound on each network step of a preset resume check (see _resume_app_preset).
RESUME_STEP_SECONDS = 60

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
                                     resume=self._resume_app_preset, applied=self._note_applied)

    def _private_path(self, name):
        return Path(self._owner_state_directory or (Path.home() / '.pantheon' / 'platform-private')) / name

    def _write_private(self, name, value):
        import json
        import os
        path = self._private_path(name)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream)
        os.replace(temporary, path)

    def _read_private(self, name):
        import json
        path = self._private_path(name)
        return json.loads(path.read_text()) if path.is_file() else None

    def _note_applied(self, spec):
        """Remember which recipe's operation runs the preset Apps now.

        A restarted platform re-reads its original (completed) operation; that
        must not hide a later resume of the same preset, whose operation the
        running instances actually belong to.
        """
        original = self._app_preset.recipe
        current = self._read_private('preset-applied.json')
        if (original is not None and current is not None and spec['operation_id'] == original['operation_id']
                and current.get('operation_id', '').startswith(original['operation_id'][:60] + '-resume-')):
            return
        self._write_private('preset-applied.json', spec)
        self._offer_platform_catalog()

    async def _retire_registrations(self, states, deployment_ids, node_id):
        """Retire stale model registrations so the preset registers them afresh.

        A fresh registration publishes only the recipe's models, so models the
        platform offered earlier are no longer "withdrawn by the owner".
        """
        from . import platform_catalog
        from .first_run import retire_stale_registrations
        client = self._model_services_manager().client
        if platform_catalog.DEPLOYMENT in deployment_ids:
            # Re-registration publishes only the setup's models; remember what the
            # owner had published so the catalog step offers it again afterwards.
            rows = {row['deployment_id']: row for row in await client.deployments()}
            kept = [m['id'] for m in (rows.get(platform_catalog.DEPLOYMENT) or {}).get('models') or []]
            if kept:
                carried = (self._read_private('platform-published.json') or {}).get('models') or []
                self._write_private('platform-published.json', {'models': list(dict.fromkeys(carried + kept))})
        await retire_stale_registrations(client, states, deployment_ids, node_id)
        if platform_catalog.DEPLOYMENT in deployment_ids:
            self._write_private('platform-catalog.json', {'offered': []})

    def _offer_platform_catalog(self):
        """Publish the platform model catalog for the Agent (see platform_catalog)."""
        task = getattr(self, '_catalog_task', None)
        if task is not None and not task.done():
            return
        try:
            self._catalog_task = asyncio.get_running_loop().create_task(self._publish_platform_catalog())
        except RuntimeError:
            pass  # No running loop (tests constructing the service synchronously).

    async def _publish_platform_catalog(self):
        from . import platform_catalog
        manager = self._model_services_manager()
        record = self._read_private('platform-catalog.json') or {'offered': []}
        try:
            row = await manager.client.deployment(platform_catalog.DEPLOYMENT)
        except Exception:
            return
        published = {m['id'] for m in row.get('models') or []}
        withdrawn = set(record['offered']) - published
        curated = []

        async def publish(manager, *, attempts=5, delay=20):
            nonlocal curated
            for attempt in range(attempts):
                try:
                    from pantheon.utils import openrouter_catalog
                    await openrouter_catalog.ensure_fresh()
                    kept = (self._read_private('platform-published.json') or {}).get('models') or []
                    curated = list(dict.fromkeys(kept + platform_catalog.curated_models()))
                    added = await platform_catalog.publish_curated(manager, curated, withdrawn=withdrawn - set(kept))
                    self._private_path('platform-published.json').unlink(missing_ok=True)
                    return added
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(f'[platform-catalog] attempt {attempt + 1} failed: {exc}')
                    await asyncio.sleep(delay)
            return 0

        setup = self._read_private('agent-setup.json') or {}
        try:
            routes = await platform_catalog.ensure_tier_routes(manager, setup.get('tiers') or {})
            if routes:
                logger.info(f'[platform-catalog] tier routes ready: {routes}')
        except Exception as exc:
            logger.warning(f'[platform-catalog] tier routes failed: {exc}')
        added = await publish(manager)
        if added:
            logger.info(f'[platform-catalog] published {added} platform models for the Agent')
        row = await manager.client.deployment(platform_catalog.DEPLOYMENT)
        offered = sorted(set(record['offered']) | ({m['id'] for m in row.get('models') or []} & set(curated)))
        self._write_private('platform-catalog.json', {'offered': offered})

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
        # Each step is bounded: right after a pod replacement a node may not have
        # registered yet, and a check that cannot finish is retried by the watch.
        step = lambda awaitable: asyncio.wait_for(awaitable, RESUME_STEP_SECONDS)
        await step(resolver._ensure_client())
        lifecycle = FleetLifecycle(resolver)
        nodes = {app['node_id'] for app in preset_resume.targets(recipe).values()}
        states = {node: await step(lifecycle.status(node)) for node in nodes}
        logger.info(f'[app-preset] resume check read {len(states)} nodes')
        spec = preset_resume.resume_recipe(recipe, states, everything=everything)
        if spec is None:
            stops = [] if everything else preset_resume.restart_stops(recipe, states)
            if not stops:
                await self._keep_preset_alive(lifecycle, recipe, states)
                return None, done
            # One node lost its Apps while others kept running: restart as one graph.
            logger.info(f'[app-preset] restarting {sorted(stops)} with the lost Apps')
            await self._stop_preset_apps(lifecycle, recipe, stops, 'preset-restart-')
            states = {node: await lifecycle.status(node) for node in nodes}
            spec = preset_resume.resume_recipe(recipe, states)
            if spec is None:
                return None, done
        # The lost connector re-registers at its next generation.
        for item in (spec.get('model_apps') or {}).values():
            node = item['app']['node_id']
            await step(self._retire_registrations(states, [item['deployment_id']], node))
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

    async def _stop_preset_apps(self, lifecycle, recipe, names, prefix):
        """Stop these preset Apps' running instances and wait (bounded).

        The operation ID prefix marks them as platform stops, so resume does
        not mistake them for the owner's decision.
        """
        import uuid
        from . import preset_resume
        apps = preset_resume.targets(recipe)
        pending = []
        for name in names:
            app = apps[name]
            state = await lifecycle.status(app['node_id'])
            for instance in (state.get('instances') or {}).values():
                if (instance.get('digest') == app['revision'] and instance.get('scope') == app['scope']
                        and instance.get('state') not in ('stopped', 'failed')):
                    operation_id = prefix + uuid.uuid4().hex[:16]
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

    @tool(exclude=True)
    async def platform_agent_release(self, action: str = 'check', target: dict | None = None) -> dict:
        """Update the startup preset to another pinned release set, or roll it back.

        check: the running and Hub-recommended (pinned) releases. upgrade: stop
        the preset, copy changed Apps' data and return the candidate recipe. The
        target is {url, sha256} of a release set the owner chose in the Store;
        without it the Hub-recommended release is used. rollback: stop it and
        return the retained source recipe. The owner saves the returned recipe
        to Hub (as at setup), then calls platform_app_preset_switch. Nothing
        here deletes either release's data (see preset_release).
        """
        import os
        setup = self._read_private('agent-setup.json')
        pinned = {'url': os.environ.get('PANTHEON_AGENT_RELEASE_URL', ''),
                  'sha256': os.environ.get('PANTHEON_AGENT_RELEASE_SHA256', '')}
        record = self._read_private('agent-release.json')
        if action == 'check':
            from .first_run import SETUP_VERSION
            if setup is None:
                # A preset from before setups were recorded: offer a fresh setup.
                return {'success': True, 'state': 'unmanaged',
                        'setup_outdated': self._app_preset.recipe is not None}
            applied = self._read_private('preset-applied.json') or {}
            rollback = (record is not None and applied.get('operation_id', '').startswith(
                record['target']['operation_id'][:60]))
            return {'success': True, 'running': setup['release'], 'pinned': pinned,
                    'setup_outdated': int(setup.get('version') or 1) < SETUP_VERSION,
                    'update_available': bool(pinned['sha256']) and pinned['sha256'] != setup['release']['sha256'],
                    'rollback_available': rollback,
                    'rollback_to': record['source_release'] if rollback else None}
        if action == 'cancel':
            # The owner did not save the returned recipe: resume restarts the
            # running release from its retained data again.
            self._app_preset.held = False
            return {'success': True, 'state': 'cancelled'}
        if action not in ('upgrade', 'rollback'):
            return {'success': False, 'error': 'Unsupported release action'}
        if setup is None or self._app_preset.recipe is None:
            return {'success': False, 'error': 'Set the Agent up before changing its release'}
        self._app_preset.held = True
        try:
            chosen = ({'url': str(target.get('url') or ''), 'sha256': str(target.get('sha256') or '')}
                      if isinstance(target, dict) else pinned)
            result = await (self._release_upgrade(setup, chosen) if action == 'upgrade'
                            else self._release_rollback(setup, record))
        except Exception as exc:
            # Nothing was switched: resume restarts the running release.
            self._app_preset.held = False
            logger.warning(f'[app-preset] release {action} failed: {exc}')
            return {'success': False, 'error': str(exc) or type(exc).__name__}
        return {'success': True, **result}

    async def _release_lifecycle(self):
        from pantheon.apps.lifecycle import FleetLifecycle
        from pantheon.apps.resolver import get_shared_resolver
        resolver = get_shared_resolver()
        await resolver._ensure_client()
        return FleetLifecycle(resolver)

    async def _release_states(self, lifecycle, recipe):
        from . import preset_resume
        nodes = {app['node_id'] for app in preset_resume.targets(recipe).values()}
        return {node: await lifecycle.status(node) for node in nodes}

    async def _release_upgrade(self, setup, pinned):
        import time
        from pantheon.apps.deployment_upgrade import AppUpgradePreparation
        from pantheon.apps.local_agent import _entries
        from pantheon.apps.release_set import stage_release_set
        from pantheon.models.bootstrap import ModelServiceBootstrap
        from . import preset_release, preset_resume
        from .app_preset import startup_recipe
        from .first_run import PLATFORM, compose_release
        from .release_source import release_set
        if not pinned['sha256'] or pinned['sha256'] == setup['release']['sha256']:
            raise ValueError('This release is already running')
        applied = self._read_private('preset-applied.json')
        if applied is None:
            raise ValueError('The running preset operation is unknown; start the Agent once, then retry')
        root = await release_set(pinned['url'], pinned['sha256'], self._private_path('releases'))
        entries = _entries(root, PLATFORM)
        operation_id = f'agent-upgrade-{int(time.time())}'
        _, candidate = compose_release(entries, setup, operation_id=operation_id)
        changes = preset_release.release_changes(applied, candidate)
        lifecycle = await self._release_lifecycle()
        tried = preset_release.retained_candidates(candidate, changes, await self._release_states(lifecycle, candidate))
        if tried:
            raise ValueError(f'This release ran before and its data for {", ".join(tried)} was kept; '
                             'Fleet does not overwrite used App data, so it cannot start from a fresh copy')
        await stage_release_set(lifecycle, root, owner=setup['owner'], placements={
            name: {'node_id': setup['nodes'][name], 'platform': PLATFORM,
                   'scope': candidate['apps'][name]['scope'], 'generation': 0} for name in changes})
        await self._install_releases(lifecycle, setup, candidate, changes, operation_id)
        await self._stop_preset_apps(lifecycle, applied, list(preset_resume.targets(applied)), 'preset-upgrade-')
        states = await self._release_states(lifecycle, applied)
        source_generations = preset_release.current_generations(applied, states)
        deployment = self._app_deployments()
        if deployment is None:
            raise ValueError('Fleet is not connected')
        copies = AppUpgradePreparation(deployment, deployment.root.parent / 'app-upgrade-preparations')
        source_operation = (ModelServiceBootstrap.child_id(applied, 'consumers') if applied.get('kind')
                            else applied['operation_id'])
        copy_id = 'release-copy-' + operation_id
        result = await copies.advance(owner=setup['owner'], operation_id=copy_id, source_operation_id=source_operation,
                                      apps=list(applied['apps']), revisions=changes)
        deadline = time.monotonic() + 1200
        while result['state'] != 'prepared':
            if time.monotonic() > deadline:
                raise ValueError('Copying App data is taking too long; retry the update to continue it')
            await asyncio.sleep(3)
            result = await copies.advance(owner=setup['owner'], operation_id=copy_id)
        generations = {**source_generations, **{name: 0 for name in changes}}
        _, target = compose_release(entries, setup, operation_id=operation_id, generations=generations)
        for item in (target.get('model_apps') or {}).values():
            await self._retire_registrations(states, [item['deployment_id']], item['app']['node_id'])
        record = preset_release.receipt(applied, target, changes, source_generations)
        self._write_private('agent-release.json', {**record, 'source_release': setup['release'],
                                                   'target_release': pinned})
        self._write_private('agent-setup.json', {**setup, 'release': pinned})
        return {'recipe': startup_recipe(target), 'changes': sorted(changes),
                'data_policy': 'copy-source-data', 'candidate_writes': 'retained-separately'}

    async def _install_releases(self, lifecycle, setup, candidate, changes, operation_id):
        """Install each changed package at generation 0 (the data copy needs it)."""
        pending = []
        for name, revision in changes.items():
            node, scope = setup['nodes'][name], candidate['apps'][name]['scope']
            state = await lifecycle.status(node)
            if any(i.get('digest') == revision for i in (state.get('installations') or {}).values()
                   if i.get('state') == 'installed'):
                continue
            op_id = f'release-install-{operation_id}-{name}'[:80]
            await lifecycle.submit(node, 'install', revision, scope=scope, generation=0, operation_id=op_id)
            pending.append((node, op_id))
        while pending:
            await asyncio.sleep(2)
            still = []
            for node, op_id in pending:
                operation = ((await lifecycle.status(node)).get('operations') or {}).get(op_id) or {}
                if operation.get('state') == 'failed':
                    raise ValueError('Installing the new release failed; inspect it in Fleet')
                if operation.get('state') != 'succeeded':
                    still.append((node, op_id))
            pending = still

    async def _release_rollback(self, setup, record):
        import time
        from . import preset_release, preset_resume
        from .app_preset import startup_recipe
        applied = self._read_private('preset-applied.json') or {}
        if record is None or not applied.get('operation_id', '').startswith(record['target']['operation_id'][:60]):
            raise ValueError('There is no release update of the running preset to roll back')
        lifecycle = await self._release_lifecycle()
        await self._stop_preset_apps(lifecycle, applied, list(preset_resume.targets(applied)), 'preset-rollback-')
        states = await self._release_states(lifecycle, record['source'])
        states.update(await self._release_states(lifecycle, applied))
        generations = preset_release.rollback_generations(record, states)
        target = preset_release.with_generations(record['source'], generations, f'agent-rollback-{int(time.time())}')
        for item in (target.get('model_apps') or {}).values():
            await self._retire_registrations(states, [item['deployment_id']], item['app']['node_id'])
        self._write_private('agent-setup.json', {**setup, 'release': record['source_release']})
        self._private_path('agent-release.json').unlink(missing_ok=True)
        return {'recipe': startup_recipe(target), 'changes': sorted(record['changes']),
                'data_policy': 'retained-source-data', 'candidate_writes': 'retained-separately'}

    @tool(exclude=True)
    async def platform_app_preset_switch(self) -> dict:
        """Run the preset the owner just saved to Hub (after a release change)."""
        from .app_preset import AppPreset
        if self._app_preset_source is None:
            return {'success': False, 'error': 'This platform has no Hub startup preset'}
        await self._app_preset.stop()
        self._app_preset = AppPreset(None, advance=self._advance_app_preset, load=self._app_preset_source,
                                     resume=self._resume_app_preset, applied=self._note_applied)
        self._app_preset.start()
        return {'success': True, 'status': self._app_preset.status()}

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
            from pantheon.apps.lifecycle import FleetLifecycle
            from pantheon.apps.resolver import get_shared_resolver
            from . import preset_resume
            try:
                resolver = get_shared_resolver()
                await resolver._ensure_client()
                await self._stop_preset_apps(FleetLifecycle(resolver), recipe,
                                             list(preset_resume.targets(recipe)), 'preset-stop-')
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
                                     resume=self._resume_app_preset, applied=self._note_applied)
        self._app_preset.start()
        return {'success': True, 'status': self._app_preset.status()}

    @tool(exclude=True)
    async def platform_agent_setup(self, platform_key: str, budget: dict, tiers: dict | None = None,
                                   replace: bool = False) -> dict:
        """First-run General Team for this cloud platform (see first_run).

        platform_key: a revocable key the owner just minted (delivered only to
        their workspace node). budget: their Hub /me/llm-proxy answer. Returns
        the recipe for the owner to save; nothing is started here.

        replace: update the running preset to a fresh setup. Its Apps stop (as
        platform stops) and their data is carried into the new packages; the
        owner saves the returned recipe, then calls platform_app_preset_switch,
        or platform_agent_release(cancel) to resume the original preset.
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
        if replace:
            current = self._read_private('preset-applied.json') or self._app_preset.recipe
            if current is None:
                return {'success': False, 'error': 'There is no running Agent setup to update'}
            self._app_preset.held = True
        try:
            if replace:
                from pantheon.apps.lifecycle import FleetLifecycle
                from . import preset_resume
                await resolver._ensure_client()
                await self._stop_preset_apps(FleetLifecycle(resolver), current,
                                             list(preset_resume.targets(current)), 'preset-resetup-')
            result = await first_run.prepare(
                resolver=resolver, owner='f_' + hashlib.sha256(user.encode()).hexdigest()[:16],
                hub=hub, controller=controller, platform_key=platform_key, budget=budget, tiers=tiers,
                cache=state / 'releases', operation_id=f'agent-setup-{int(time.time())}',
                directory=self._model_services_manager().client)
        except Exception as exc:
            self._app_preset.held = False  # nothing was switched: resume restarts the current preset
            return {'success': False, 'error': str(exc) or type(exc).__name__}
        # Release updates recompose this exact setup against another release set.
        self._write_private('agent-setup.json', result.pop('setup'))
        self._write_private('platform-catalog.json', {'offered': []})  # a new setup registers afresh
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
