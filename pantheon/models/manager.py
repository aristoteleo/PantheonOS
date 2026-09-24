"""Model service lifecycle. Closing the UI never closes an attached engine."""
import asyncio
from pathlib import Path
import re
import time

from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.apps.registry import BUILTIN_ROOT
from pantheon.apps.resolver import AppInstanceResolver
from .client import get_client


class ModelServiceManager:
    def __init__(self, client=None, resolver=None, *, group_store_root=None):
        self.client = client or get_client()
        self.resolver = resolver or AppInstanceResolver.from_env()
        self.locks = {}
        self.group_store_root = group_store_root

    def lock(self, deployment_id):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', deployment_id):
            raise ValueError('Invalid deployment id')
        if deployment_id not in self.locks:
            if len(self.locks) >= 128:
                self.locks = {k: v for k, v in self.locks.items() if v.locked()}
            self.locks[deployment_id] = asyncio.Lock()
        return self.locks[deployment_id]

    async def wait(self, node_id, operation):
        lifecycle = FleetLifecycle(self.resolver)
        deadline = time.monotonic() + 610
        while time.monotonic() < deadline:
            state = await lifecycle.status(node_id)
            op = state['operations'].get(operation['request']['operation_id'])
            if not op:
                raise RuntimeError('Operation unavailable; inspect this service in Fleet')
            if op['state'] == 'succeeded':
                return state
            if op['state'] not in ('queued', 'running'):
                raise RuntimeError(op.get('error') or 'Model connector failed to start')
            await asyncio.sleep(.5)
        raise RuntimeError('Setup is still running on the node. Check Fleet before retrying.')

    async def group_deployments(self, action='list', group_id='', config=None):
        from .group_management import operation
        return await operation(self, action, group_id, config)

    async def groups(self, action='list', group_id=''):
        """Inspect durable groups or finish an explicit stop; never start on view."""
        if action not in {'list', 'inspect', 'stop', 'continue_stop'}:
            raise ValueError('Unsupported model group action')
        if not self.resolver:
            raise RuntimeError('Fleet is not connected')
        await self.resolver._ensure_client()
        from .group_hub import HubGroupJournal
        from .group_coordinator import GroupCoordinator
        journal = HubGroupJournal(self.client, self.resolver._fleet)
        if action == 'list':
            return {'groups': await journal.list()}
        if action == 'inspect':
            return await journal.load(group_id)
        coordinator = GroupCoordinator(journal, FleetLifecycle(self.resolver))
        if action == 'stop':
            await coordinator.stop(group_id)
        row = await journal.load(group_id)
        if row['phase'] not in {'aborting', 'stopped'}:
            raise ValueError('Record an explicit group stop before continuing cleanup')
        return await coordinator.advance(group_id)

    async def rpc(self, binding, method, args=None):
        client = await FleetLifecycle(self.resolver)._client(binding['node_id'])
        # Owner configuration/recovery can restore resident memory: unload,
        # load and compute warmup each have their own bounded engine deadline.
        # Keep the Fleet/NATS envelope alive for that existing operation; a
        # timeout is still uncertain and must never trigger automatic replay.
        timeout = 600 if method in {'configure', 'resume'} else 15
        response = await client.invoke(binding['node_id'], 'model-service', binding, method, args or {}, timeout)
        result = response.get('response', {})
        if response.get('error') or result.get('error'):
            raise RuntimeError(response.get('error') or result['error'])
        return result

    async def ensure(self, row, *, binding_key='binding', directory=None, scope=None):
        lifecycle = FleetLifecycle(self.resolver)
        node_id, scope = row['node_id'], scope or 'model-' + row['deployment_id']
        if row.get(binding_key):
            digest = row[binding_key]['revision']
            state = await lifecycle.status(node_id)
            bound = state['instances'].get(row[binding_key]['instance_id'])
            if not bound or bound['digest'] != digest or bound['scope'] != scope or bound['app_id'] != 'model-service':
                raise ValueError('Model service binding is missing; inspect it in Fleet')
        else:
            directory = directory or Path(BUILTIN_ROOT) / 'model-service'
            digest = await lifecycle.stage(node_id, directory)
            state = await self.wait(node_id, await lifecycle.submit(node_id, 'install', digest, scope=scope))
        current = next((i for i in state['instances'].values() if i['digest'] == digest and i['scope'] == scope), None)
        if current and current['state'] == 'starting':
            op = next((o for o in state['operations'].values() if o['request']['scope'] == scope
                       and o['request']['action'] == 'start' and o['state'] in ('queued', 'running')), None)
            if op:
                state = await self.wait(node_id, op)
                current = state['instances'].get(current['instance_id'])
        if not current or current['state'] == 'stopped':
            state = await self.wait(node_id, await lifecycle.submit(node_id, 'start', digest, scope=scope,
                                      generation=(current or {}).get('generation', 0)))
            current = next(i for i in state['instances'].values() if i['digest'] == digest and i['scope'] == scope)
        if current['state'] != 'ready':
            raise RuntimeError('This connector needs recovery in Fleet; no engine was restarted')
        binding = dict(node_id=node_id, instance_id=current['instance_id'], revision=digest,
                       generation=current['generation'], component='backend', port='http')
        await lifecycle.usage(node_id, 'keep_alive', **{k: binding[k] for k in
            ('instance_id', 'revision', 'generation')}, keep_alive=True)
        return binding

    async def node(self, node_id, *, managed=False):
        if not self.resolver:
            raise RuntimeError('Fleet is not connected')
        if not self.resolver._client:
            await self.resolver._ensure_client()
        nodes = await self.resolver._list_nodes(max_age=0)
        node = next((n for n in nodes if n['node_id'] == node_id), None)
        if not node:
            raise ValueError('Select a node in your Fleet')
        runtimes = (node.get('capability') or {}).get('runtimes', {})
        if runtimes.get('app-rpc-auth') != '1':
            raise RuntimeError('Update Fleet on the selected node to enable authenticated Model Services management, then retry. No connector was installed.')
        if managed and runtimes.get('app-resources') != '1':
            raise RuntimeError('Update Fleet on this node for managed model resource reservations')
        return node

    async def create_managed(self, deployment_id, name, node_id, config):
        from .managed import validate, engines
        async with self.lock(deployment_id):
            node = await self.node(node_id, managed=True)
            cap = node['capability']
            config = validate(config, cap['os'] + '-' + cap['arch'])
            selected = engines().recipe(config['recipe_id'], target=cap['os'] + '-' + cap['arch'])
            if selected['engine'] == 'speaches' and (cap.get('runtimes', {}).get('app-readonly-mounts') != '1'
                    or cap.get('runtimes', {}).get('app-owner-user') != '1'):
                raise ValueError('Update Fleet on this Linux node for private, read-only speech model mounts')
            if selected['engine'] == 'sglang' and cap.get('runtimes', {}).get('app-readonly-mounts') != '1':
                raise ValueError('Update Fleet on this GPU node for read-only managed model mounts')
            existing = next((d for d in await self.client.deployments() if d['deployment_id'] == deployment_id), None)
            if existing and (existing.get('mode') != 'managed' or existing['node_id'] != node_id or existing.get('managed') != config):
                raise ValueError('This deployment already refers to a different configuration')
            row = existing or await self.client.save(dict(deployment_id=deployment_id, name=name, node_id=node_id,
                node_name=node.get('name', node_id), engine=engines().recipe(config['recipe_id'], target=cap['os'] + '-' + cap['arch'])['engine'], mode='managed', managed=config,
                state='draft', models=[], revision=0))
            if row['state'] != 'draft':
                return row
            row['binding'] = await self.ensure(row)
            row = await self.client.save(row)
            if row['engine'] == 'speaches':
                await self.rpc(row['binding'], 'speech_models', dict(action='prepare', model_id=config['model_recipe_id'], resume=True))
            elif selected.get('operation') in {'image', 'video'}:
                await self.rpc(row['binding'], 'diffusion_models', dict(action='prepare', model_id=config['model_recipe_id'], resume=True))
            elif row['engine'] != 'sglang':
                await self.rpc(row['binding'], 'engines_prepare', {'recipe_id': config['recipe_id'], 'resume': True})
            return row

    async def engine_recipes(self, node_id):
        from .managed import engines, module
        node = await self.node(node_id, managed=True)
        cap = node['capability']
        target = cap['os'] + '-' + cap['arch']
        return {'recipes': [r for r in engines().catalog() if target in r['platforms'] and target != 'darwin-amd64'],
                'speech_models': [{k: item[k] for k in ('id', 'model', 'operation', 'minimum_memory_bytes')}
                                  for item in module('speech_models').catalog()] if target == 'linux-amd64' else [],
                'diffusion_models': [{k: item[k] for k in ('id', 'model', 'operation', 'minimum_memory_bytes')}
                                     for item in module('diffusion_models').catalog()] if target == 'linux-amd64' else []}

    async def engines(self, deployment_id, action='catalog', recipe_id='', resume=False):
        if action not in {'catalog', 'jobs', 'prepare', 'cancel'}:
            raise ValueError('Unsupported engine preparation action')
        row = await self.client.deployment(deployment_id)
        if not row.get('binding') or row['state'] in {'stopped', 'stopping', 'recovering'}:
            raise ValueError('Start the connector before preparing an engine')
        args = {'recipe_id': recipe_id, 'resume': resume} if action == 'prepare' else (
            {'job_id': recipe_id} if action == 'cancel' else {})
        return await self.rpc(row['binding'], 'engines_' + action, args)

    async def start_managed(self, row):
        from .managed import package
        node = await self.node(row['node_id'], managed=True)
        cap = node['capability']
        if row['engine'] == 'speaches' and (cap.get('runtimes', {}).get('app-readonly-mounts') != '1'
                or cap.get('runtimes', {}).get('app-owner-user') != '1'):
            raise ValueError('Update Fleet on this Linux node for private, read-only speech model mounts')
        if row['engine'] == 'sglang' and cap.get('runtimes', {}).get('app-readonly-mounts') != '1':
            raise ValueError('Update Fleet on this GPU node for read-only managed model mounts')
        row['binding'] = await self.ensure(row)
        row['state'] = 'draft'
        row = await self.client.save(row)
        catalog = await self.rpc(row['binding'], 'engines_catalog')
        selected = next((r for r in catalog['recipes'] if r['id'] == row['managed']['recipe_id']), None)
        if not selected or (selected.get('runtime') != 'container' and not selected['prepared']):
            raise ValueError('Engine preparation has not completed. Inspect Downloads before starting it.')
        if selected.get('operation') in {'image', 'video'}:
            snapshot = await self.rpc(row['binding'], 'diffusion_models', dict(action='status', model_id=row['managed']['model_recipe_id']))
            if not snapshot['ready']:
                raise ValueError('Finish preparing the pinned diffusion weights in Downloads before starting')
            if (snapshot['minimum_memory_bytes'] > row['managed']['resources']['memory_bytes']
                    or selected['minimum_gpu_memory_bytes'] > row['managed']['resources']['devices'][0]['memory_bytes']):
                raise ValueError('The diffusion model exceeds this deployment’s memory budget')
        elif row['engine'] == 'sglang':
            snapshot = await self.rpc(row['binding'], 'snapshots_status', {
                'sha256': row['managed']['model_artifact_sha256'], 'context_length': row['managed']['context_length'],
                'parallel': row['managed']['parallel'], 'tensor_parallel_size': row['managed'].get('tensor_parallel_size', 1)})
            if not snapshot['ready']:
                raise ValueError('Download and prepare the pinned model bundle in Downloads before starting SGLang')
            if any(snapshot['estimate']['estimated_bytes'] > device['memory_bytes']
                   for device in row['managed']['resources']['devices']):
                raise ValueError('Weights, KV cache and workspace exceed this deployment’s GPU budget')
        if row['engine'] == 'speaches':
            snapshot = await self.rpc(row['binding'], 'speech_models', dict(action='status', model_id=row['managed']['model_recipe_id']))
            if not snapshot['ready']:
                raise ValueError('Finish preparing the pinned speech model in Downloads before starting')
            if snapshot['minimum_memory_bytes'] > row['managed']['resources']['memory_bytes']:
                raise ValueError('The speech model exceeds this deployment’s system memory budget')
        if row.get('engine_binding'):
            # Restart the installed artifact. A newer Agent's wrapper/catalog
            # must never implicitly change (or prevent restarting) this engine.
            row['engine_binding'] = await self.ensure(row, binding_key='engine_binding',
                scope='engine-' + row['deployment_id'])
        else:
            with package(row['managed'], cap['os'] + '-' + cap['arch']) as directory:
                row['engine_binding'] = await self.ensure(row, binding_key='engine_binding',
                    directory=directory, scope='engine-' + row['deployment_id'])
        # Persist engine ownership before configuring the connector. If either
        # RPC acknowledgement is lost, resume finds this exact scope/generation.
        row = await self.client.save(row)
        if row.get('engine_idle'):
            from .idle_management import reset_fence
            await reset_fence(self, row['binding'], await self.rpc(row['binding'], 'status'))
        configured = await self.rpc(row['binding'], 'configure', await self.managed_configuration(row, row['engine_binding']))
        if row.get('engine_idle'):
            await self.rpc(row['binding'], 'resume', {'config_revision': configured['config_revision']})
        row.update(state='ready', config_revision=configured['config_revision'])
        return await self.client.save(row)

    async def managed_configuration(self, row, binding):
        state = await FleetLifecycle(self.resolver).status(row['node_id'])
        instance, stopped = self.bound_instance(state, binding, 'engine-' + row['deployment_id'])
        if stopped or instance['state'] != 'ready':
            raise ValueError('Managed engine changed while configuring the connector')
        resource = next(r for r in instance['resources'] if r['component'] == 'backend')
        endpoint = resource['endpoints']['http']
        # Only a loopback port returned by the owned Fleet instance is used.
        from urllib.parse import urlsplit
        url = urlsplit(endpoint)
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or url.path or url.query or url.fragment:
            raise ValueError('Fleet returned an invalid managed engine endpoint')
        managed = {k: v for k, v in row['managed'].items() if k != 'resources'}
        managed.update(scope='engine-' + row['deployment_id'], memory_bytes=row['managed']['resources']['memory_bytes'])
        return {'config': {'engine': row['engine'], 'endpoint': endpoint}, 'managed': managed}

    async def attach(self, deployment_id, name, node_id, engine, endpoint, credential_file='', secret_ref=''):
        if not self.resolver:
            raise RuntimeError('Fleet is not connected')
        async with self.lock(deployment_id):
            node = await self.node(node_id)
            # Validate before persisting; same validator travels in the immutable artifact.
            from importlib.util import spec_from_file_location, module_from_spec
            spec = spec_from_file_location('model_connector_validation', BUILTIN_ROOT / 'model-service' / 'server.py')
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            config = module.validate_config(dict(engine=engine, endpoint=endpoint, credential_file=credential_file, secret_ref=secret_ref))
            if secret_ref and (node.get('capability') or {}).get('runtimes', {}).get('model-credentials') != '1':
                raise ValueError('Update Fleet on the selected node to use named credentials. No connector was installed.')
            existing = next((d for d in await self.client.deployments() if d['deployment_id'] == deployment_id), None)
            if existing and (existing.get('mode', 'attached') != 'attached' or existing['state'] != 'draft' or existing['node_id'] != node_id or existing['engine'] != engine):
                raise ValueError('This deployment already exists. Refresh and resume it instead of creating another.')
            row = existing or await self.client.save(dict(deployment_id=deployment_id, name=name, node_id=node_id,
                node_name=node.get('name', node_id), engine=engine, state='draft', models=[], revision=0))
            binding = await self.ensure(row)
            row['binding'] = binding
            row = await self.client.save(row)
            result = await self.rpc(binding, 'configure', {'config': config})
            row.update(binding=binding, config_revision=result['config_revision'])
            # Keep metadata even if the engine is currently offline; the user can refresh.
            row['state'] = 'ready'
            row = await self.client.save(row)
            return row

    async def discover(self, deployment_id):
        row = await self.client.deployment(deployment_id)
        if row.get('recovery') or row.get('engine_update') or row.get('connector_update'):
            raise ValueError('Resume the pending service operation before managing its models')
        if not row.get('binding'):
            raise ValueError('The connector has not finished setup. Inspect it in Fleet.')
        from .idle import wake
        row = await wake(self.client, row)
        return await self.rpc(row['binding'], 'discover')

    async def artifacts(self, deployment_id, action='list', job_id='', source=None, resume=False):
        if action not in {'list', 'submit', 'cancel', 'forget'}:
            raise ValueError('Unsupported artifact operation')
        row = await self.client.deployment(deployment_id)
        if row.get('state') in {'stopped', 'stopping', 'recovering'} or not row.get('binding'):
            raise ValueError('Start this service before managing its downloads')
        args = {} if action == 'list' else {'job_id': job_id}
        if action == 'submit':
            args.update(source=source, resume=resume)
        return await self.rpc(row['binding'], 'artifacts_' + action, args)

    async def snapshots(self, deployment_id, action='jobs', artifact_job_id='', resume=False, job_id=''):
        if action not in {'jobs', 'prepare', 'cancel'}:
            raise ValueError('Unsupported snapshot operation')
        row = await self.client.deployment(deployment_id)
        if row['state'] in {'stopped', 'stopping', 'recovering'} or not row.get('binding') or row.get('mode') != 'managed' or row['engine'] != 'sglang':
            raise ValueError('An owned SGLang connector is required')
        args = {'artifact_job_id': artifact_job_id, 'resume': resume} if action == 'prepare' else (
            {'job_id': job_id} if action == 'cancel' else {})
        return await self.rpc(row['binding'], 'snapshots_' + action, args)

    async def resources(self, node_id):
        return await FleetLifecycle(self.resolver).resource_status(node_id)

    async def speech_models(self, deployment_id, action='catalog', model_id='', resume=False):
        if action not in {'catalog', 'status', 'prepare', 'jobs', 'cancel', 'forget'}:
            raise ValueError('Unsupported speech model preparation action')
        row = await self.client.deployment(deployment_id)
        if (row.get('state') not in {'draft', 'ready'} or not row.get('binding')
                or row.get('engine') != 'speaches'):
            raise ValueError('Start a Speaches connector before preparing its local model cache')
        return await self.rpc(row['binding'], 'speech_models', dict(action=action, model_id=model_id, resume=resume))

    async def diffusion_models(self, deployment_id, action='catalog', model_id='', resume=False):
        if action not in {'catalog', 'status', 'prepare', 'jobs', 'cancel', 'forget'}:
            raise ValueError('Unsupported diffusion model preparation action')
        row = await self.client.deployment(deployment_id)
        if (row.get('state') not in {'draft', 'ready'} or not row.get('binding')
                or row.get('engine') != 'sglang'):
            raise ValueError('Start an SGLang connector before preparing its diffusion model cache')
        return await self.rpc(row['binding'], 'diffusion_models', dict(action=action, model_id=model_id, resume=resume))

    async def activity(self, deployment_id, action='list', request_id=''):
        if action not in {'list', 'cancel'}:
            raise ValueError('Unsupported request activity action')
        row = await self.client.deployment(deployment_id)
        if not row.get('binding') or row['state'] not in {'ready', 'stopping'}:
            raise ValueError('Start the connector to inspect its saved request activity')
        if action == 'cancel':
            return await self.rpc(row['binding'], 'cancel_request', {'request_id': request_id})
        return await self.rpc(row['binding'], 'activity')

    async def video_recovery(self, ref, action='inspect', ticket='', confirmation=''):
        from .jobs import parse_job_ref
        deployment_id, job_id = parse_job_ref(ref)
        if action not in {'inspect', 'release'}:
            raise ValueError('Unsupported video recovery action')
        async with self.lock(deployment_id):
            # Owner-scoped directory and generation-bound management RPC. An
            # inference grant alone never permits administrative capacity release.
            row = await self.client.deployment(deployment_id)
            if not row.get('binding') or row['state'] not in {'ready', 'stopping'}:
                raise ValueError('Inspect the original connector before recovering this video')
            result = await self.rpc(row['binding'], 'video_recovery', {
                'job_id': job_id, 'config_revision': row['config_revision'],
                'action': action, 'ticket': ticket, 'confirmation': confirmation})
            return {**result, 'ref': ref}

    async def model_operations(self, deployment_id, action='status', job_id='', operation='', artifact_job_id='', model_id='', pool_revision=None):
        if action not in {'status', 'submit', 'forget'}:
            raise ValueError('Unsupported model management action')
        row = await self.client.deployment(deployment_id)
        if row.get('mode') != 'managed' or row['state'] != 'ready' or not row.get('engine_binding'):
            raise ValueError('Start an owned engine before managing its models')
        from .idle import observe, wake
        policy = row.get('engine_idle') or {}
        if policy.get('phase') == 'enabled' and action == 'status':
            _, snapshot = await observe(self.client, row)
            # Connector keeps cached models/jobs while the engine is fenced.
            # Unknown memory stays unknown; observing never extends warm time.
            result = await self.rpc(row['binding'], 'models_status')
            return {**result, 'engine_idle': snapshot['state']}
        if action == 'submit':
            if operation not in {'import', 'load', 'unload', 'preload', 'unpin'}:
                raise ValueError('Unsupported model operation')
            if operation == 'preload' and row.get('managed', {}).get('load_policy') != 'resident':
                raise ValueError('Choose a resident service before enabling preloading')
            row = await wake(self.client, row)
        if action != 'forget':
            state = await FleetLifecycle(self.resolver).status(row['node_id'])
            instance, stopped = self.bound_instance(state, row['engine_binding'], 'engine-' + deployment_id)
            if stopped or instance['state'] != 'ready':
                raise ValueError('Owned engine is no longer ready; inspect it in Fleet')
        args = {} if action == 'status' else {'job_id': job_id}
        if action == 'submit':
            args.update(action=operation, artifact_job_id=artifact_job_id, model_id=model_id)
            if pool_revision is not None:
                args['pool_revision'] = pool_revision
        return await self.rpc(row['binding'], 'models_' + action, args)

    async def publish(self, deployment_id, models, revision):
        async with self.lock(deployment_id):
            row = await self.client.deployment(deployment_id)
            if row.get('mode') == 'group':
                raise ValueError('Manage this model through its original group lifecycle')
            if row['revision'] != revision:
                raise ValueError('Service changed. Refresh before publishing.')
            if any(row.get(k) for k in ('recovery', 'connector_update', 'engine_update', 'operation_stop')):
                raise ValueError('Resume the pending service operation before publishing models')
            from .idle import wake
            row = await wake(self.client, row)
            discovered = await self.rpc(row['binding'], 'discover')
            ids = {m['id'] for m in discovered['models']}
            if len({m['id'] for m in models}) != len(models) or any(m['id'] not in ids for m in models):
                raise ValueError('Publish unique models returned by this endpoint')
            row.update(models=models, config_revision=discovered['config_revision'])
            return await self.client.save(row)

    @staticmethod
    def bound_instance(state, binding, scope, *, stopping=False):
        instance = state['instances'].get(binding['instance_id'])
        if (not instance or instance['digest'] != binding['revision']
                or instance['scope'] != scope or instance['app_id'] != 'model-service'):
            raise ValueError('Model service identity changed; inspect its exact Fleet binding')
        stopped = (instance['state'] == 'stopped' and not instance.get('resources')
                   and not instance.get('reservations'))
        # Stop commits generation + 1. Recover an acknowledged or lost stop
        # without ever adopting (and then stopping) a newer live generation.
        allowed = {binding['generation'], binding['generation'] + 1} if stopped else {binding['generation']}
        # A stop may also settle a newer generation that is not live: a clean
        # stopped one, or a failed one (e.g. an on-demand start refused by memory
        # admission) that is then stopped at its own exact, Fleet-fenced generation.
        newer_inactive = stopping and instance['generation'] > binding['generation'] and (
            stopped or instance['state'] == 'failed')
        if instance['generation'] not in allowed and not newer_inactive:
            raise ValueError('Model service generation changed; inspect its exact Fleet binding')
        return instance, stopped

    async def stop_binding(self, row, key, scope):
        lifecycle = FleetLifecycle(self.resolver)
        binding = row[key]
        state = await lifecycle.status(row['node_id'])
        instance, stopped = self.bound_instance(state, binding, scope, stopping=True)
        if not stopped:
            op = await lifecycle.submit(row['node_id'], 'stop', binding['revision'],
                scope=scope, generation=instance['generation'])
            state = await self.wait(row['node_id'], op)
            instance, stopped = self.bound_instance(state, binding, scope, stopping=True)
            if not stopped:
                raise RuntimeError('Model service stop has not been confirmed by Fleet')
        row[key] = {**binding, 'generation': instance['generation']}
        # Persist before the next component stop, so partial failures remain
        # retryable. A failed save is recovered from the exact stopped instance.
        return await self.client.save(row)

    async def drain_binding(self, binding):
        deadline = time.monotonic() + 30
        while True:
            drained = await self.rpc(binding, 'drain')
            if drained.get('safe_to_stop') is True:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('Service is draining active calls or model operations. New calls are blocked; retry after they finish.')
            await asyncio.sleep(.5)

    async def upgrade_connector(self, deployment_id):
        """Explicit, resumable same-node connector update.

        The Hub intent pins the artifact before admissions are blocked. State is
        copied by Fleet locally only after exact-generation stop; secrets and
        endpoint configuration never leave the node. Failed upgrades remain
        unavailable for inference until explicitly resumed. A real update restores
        an idle engine first; rejected or already-current targets leave it asleep.
        """
        async with self.lock(deployment_id):
            row = await self.client.deployment(deployment_id)
            if row.get('mode') == 'group':
                raise ValueError('Manage this model through its original group lifecycle')
            if row.get('operation_stop'):
                raise ValueError('Finish stopping this operation before updating its connector')
            if row.get('recovery') or row.get('engine_update'):
                raise ValueError('Resume service recovery or engine update before updating its connector')
            if not row.get('binding') or row['state'] == 'draft':
                raise ValueError('Complete connector setup before updating it')
            node = await self.node(row['node_id'])
            if node.get('capability', {}).get('runtimes', {}).get('app-data-clone') != '1':
                raise ValueError('Update Fleet on this node to preserve connector state during an update')
            lifecycle = FleetLifecycle(self.resolver)
            scope = 'model-' + deployment_id
            pending = row.get('connector_update')
            if not pending:
                digest = await lifecycle.stage(row['node_id'], Path(BUILTIN_ROOT) / 'model-service')
                if digest == row['binding']['revision']:
                    return row
                state = await lifecycle.status(row['node_id'])
                if any(i['digest'] == digest and i['scope'] == scope for i in state['instances'].values()):
                    raise ValueError('This connector revision already has an instance; inspect it in Fleet before updating')
                if row.get('engine_idle') and row['state'] == 'ready':
                    from .recovery import recover_locked
                    row = await recover_locked(self, row)
                await self.wait(row['node_id'], await lifecycle.submit(row['node_id'], 'install', digest, scope=scope))
                pending = {'source': dict(row['binding']), 'target_revision': digest}
                row.update(connector_update=pending, state='stopping')
                row = await self.client.save(row)
            # Resume the saved target even if the Agent itself has since updated.
            source, digest = pending['source'], pending['target_revision']
            state = await lifecycle.status(row['node_id'])
            instance, stopped = self.bound_instance(state, source, scope)
            if not stopped:
                await self.drain_binding(source)
                state = await self.wait(row['node_id'], await lifecycle.submit(row['node_id'], 'stop',
                    source['revision'], scope=scope, generation=source['generation']))
                instance, stopped = self.bound_instance(state, source, scope)
            if not stopped:
                raise RuntimeError('The old connector has not stopped; its state was not copied')
            origin = {'digest': source['revision'], 'generation': instance['generation']}
            current = next((i for i in state['instances'].values() if i['digest'] == digest and i['scope'] == scope), None)
            if current is None or current['generation'] == 0:
                state = await self.wait(row['node_id'], await lifecycle.submit(row['node_id'], 'clone_data',
                    digest, scope=scope, generation=0, data_source=origin))
                current = next(i for i in state['instances'].values() if i['digest'] == digest and i['scope'] == scope)
            if current.get('data_source') != origin or current['app_id'] != 'model-service' or current['generation'] > 1:
                raise ValueError('Updated connector identity changed; inspect its exact Fleet binding')
            row['binding'] = dict(node_id=row['node_id'], instance_id=current['instance_id'], revision=digest,
                generation=current['generation'], component='backend', port='http')
            # A freshly copied instance has generation 0, which is not a live
            # Hub binding. The durable update intent already pins this target;
            # resume can recover a lost start acknowledgement from Fleet. Only
            # publish its binding after ensure returns a started generation.
            binding = await self.ensure(row)
            result = await self.rpc(binding, 'status')
            if result['config_revision'] != row['config_revision']:
                raise ValueError('Updated connector configuration differs; it was not published')
            if row.get('mode') == 'managed' and row.get('engine_binding'):
                state = await lifecycle.status(row['node_id'])
                engine, stopped = self.bound_instance(state, row['engine_binding'], 'engine-' + deployment_id)
                if stopped or engine['state'] != 'ready':
                    raise ValueError('Owned engine is no longer ready; inspect it before resuming the connector update')
            # Copied on-demand state starts fenced until the owner verifies the
            # exact engine generation. Reconcile idle memory before publishing
            # readiness; a failed/lost acknowledgement retains the update intent.
            if row.get('engine_idle'):
                from .idle_management import reset_fence
                await reset_fence(self, binding, result)
            resumed = await self.rpc(binding, 'resume', {'config_revision': row['config_revision']})
            if resumed.get('config_revision') != row['config_revision'] or resumed.get('accepting') is not True:
                raise ValueError('Updated connector has not confirmed readiness; resume its update')
            row.update(binding=binding, state='ready', connector_update=None)
            return await self.client.save(row)

    async def recover(self, deployment_id):
        if (await self.client.deployment(deployment_id)).get('mode') == 'group':
            raise ValueError('Recover the original model group; individual ranks cannot be restarted')
        from .recovery import recover
        return await recover(self, deployment_id)

    async def upgrade_engine(self, deployment_id, recipe_id):
        from .engine_upgrade import upgrade
        return await upgrade(self, deployment_id, recipe_id)

    async def stop_operation(self, deployment_id, revision):
        from .operation_stop import stop_operation
        return await stop_operation(self, deployment_id, revision)

    async def engine_idle_status(self, deployment_id):
        from .idle import observe
        row = await self.client.deployment(deployment_id)
        _, snapshot = await observe(self.client, row)
        return snapshot

    async def set_engine_idle(self, deployment_id, idle_seconds, revision):
        from .idle_management import enable, cancel
        from .recovery import recover_locked
        if type(idle_seconds) is not int or not 0 <= idle_seconds <= 86400:
            raise ValueError('Idle timeout must be 0 (disabled) or 1–86400 seconds')
        async with self.lock(deployment_id):
            row = await self.client.deployment(deployment_id)
            if type(revision) is not int or revision != row['revision']:
                raise ValueError('Model service changed; refresh before changing its idle policy')
            if idle_seconds:
                return await enable(self, row, idle_seconds)
            row = await cancel(self, row)
            # Turning off automatic idle restores normal ready service behavior.
            # Stop uses cancel directly, and must never take this wake path.
            if row.get('engine_idle') and row['state'] in {'ready', 'recovering'}:
                return await recover_locked(self, row)
            return row

    async def remove(self, deployment_id, revision):
        """Forget a stopped service; its node cache, weights and data stay on the node."""
        async with self.lock(deployment_id):
            row = await self.client.deployment(deployment_id)
            if row.get('mode') == 'group':
                raise ValueError('Manage this model through its original group lifecycle')
            if row['revision'] != revision:
                raise ValueError('Service changed. Refresh before removing it.')
            if row['state'] not in {'stopped', 'draft'}:
                raise ValueError('Stop this service before removing it')
            return await self.client.remove(deployment_id, revision)

    async def set_running(self, deployment_id, running):
        async with self.lock(deployment_id):
            row = await self.client.deployment(deployment_id)
            if row.get('mode') == 'group':
                if running:
                    if row['state'] == 'ready':
                        return row
                    raise ValueError('Continue or create an original model group; individual ranks cannot be started')
                await self.groups('stop', row['group_id'])
                return await self.client.deployment(deployment_id)
            if row.get('operation_stop'):
                raise ValueError('Finish stopping this operation before changing service state')
            if row.get('recovery'):
                raise ValueError('Resume service recovery before changing service state')
            if row.get('connector_update') or row.get('engine_update'):
                raise ValueError('Resume the pending update before changing service state')
            if running:
                if row['state'] == 'ready':
                    from .idle import wake
                    return await wake(self.client, row)
                if row['state'] == 'stopping':
                    raise ValueError('Finish stopping this service before restarting it')
                if row.get('mode') == 'managed':
                    return await self.start_managed(row)
                if row['state'] == 'draft':
                    raise ValueError('Complete this service’s endpoint configuration first')
                binding = await self.ensure(row)
                result = await self.rpc(binding, 'status')
                row.update(binding=binding, config_revision=result['config_revision'], state='ready')
            else:
                from .idle_management import cancel
                row = await cancel(self, row)
                if not row.get('binding'):
                    raise ValueError('Check the unfinished setup in Fleet')
                b = row['binding']
                lifecycle = FleetLifecycle(self.resolver)
                state = await lifecycle.status(row['node_id'])
                _, stopped = self.bound_instance(state, b, 'model-' + deployment_id)
                row['state'] = 'stopping'
                row = await self.client.save(row)
                if not stopped:
                    # Stop new admissions before waiting. Existing streams and
                    # model jobs finish normally; an interrupted stop is durable
                    # and resumes this exact generation on Retry stop.
                    await self.drain_binding(b)
                row = await self.stop_binding(row, 'binding', 'model-' + deployment_id)
                if row.get('mode') == 'managed' and row.get('engine_binding'):
                    row = await self.stop_binding(row, 'engine_binding', 'engine-' + deployment_id)
                row['state'] = 'stopped'
            return await self.client.save(row)
