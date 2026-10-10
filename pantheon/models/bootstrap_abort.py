"""Cancel composed model startup through its original generic App deployments.

The bootstrap journal is the cancellation fence. Consumers drain before model
providers, and directory CAS reflects only exact verified stopped generations.
No model discovery, credential preparation or replacement startup runs here.
"""
from copy import deepcopy

from pantheon.apps.dependency_assembly import AssemblyError, NAME, _matches
from pantheon.apps.deployment_abort import AppDeploymentAbort
from pantheon.utils.registry_lock import registry_lock
from .bootstrap import digest, resolve_models
from .managed import module


def validate(record):
    value = record.get('abort')
    if (not isinstance(value, dict) or set(value) != {'operation_id', 'rows'}
            or not _matches(NAME, value['operation_id'])
            or value['operation_id'] == record['recipe']['operation_id']
            or record['phase'] not in ('aborting', 'aborted')
            or record['state'] != ('aborted' if record['phase'] == 'aborted' else 'pending')
            or value['rows'] is not None and (not isinstance(value['rows'], dict)
                or value['rows'].keys() - record['recipe']['model_apps'].keys())
            or record['phase'] == 'aborted' and value['rows'] is None):
        raise AssemblyError('Invalid model startup abort checkpoint')
    return value


class ModelBootstrapAbort:
    def __init__(self, bootstrap):
        self.bootstrap = bootstrap
        self.deployment = bootstrap.deployment

    def child(self, spec, role):
        path = self.deployment._path(self.bootstrap.child_id(spec, role))
        if not path.exists() and not path.is_symlink():
            return None
        record = self.deployment._load(path)
        if (record['recipe']['owner'] != spec['owner']
                or record['recipe']['operation_id'] != self.bootstrap.child_id(spec, role)):
            raise AssemblyError('Model startup child belongs to another owner or operation')
        if role == 'providers':
            expected = {alias: entry['app'] for alias, entry in spec['model_apps'].items()}
        else:
            providers = self.child(spec, 'providers')
            if providers is None or set(providers['prepared']) != set(spec['model_apps']):
                raise AssemblyError('Consumer deployment has no complete original model bindings')
            bindings = {alias: {**identity, 'generation': identity['generation'] + 1,
                        'component': 'backend', 'port': 'http'}
                        for alias, identity in providers['prepared'].items()}
            expected = resolve_models(spec['apps'], bindings)
        if record['recipe']['apps'] != expected:
            raise AssemblyError('Model startup child differs from its original recipe')
        return record

    async def verify_unused_child(self, spec, role):
        """A missing journal is not proof that no node-side work happened."""
        apps = spec['apps'] if role == 'consumers' else {
            alias: entry['app'] for alias, entry in spec['model_apps'].items()}
        states = {}
        recipe = {'owner': spec['owner'], 'operation_id': self.bootstrap.child_id(spec, role)}
        for alias, app in apps.items():
            node = app['node_id']
            if node not in states:
                states[node] = await self.deployment.lifecycle.status(node)
            state = states[node]
            if state.get('owner') != spec['owner'] or state.get('node_id') != node:
                raise AssemblyError('Unused deployment belongs to another node owner')
            if any(self.deployment.operation_id(recipe, alias, action) in state['operations']
                   for action in ('install', 'prepare_start', 'start')):
                raise AssemblyError('Missing child journal has existing node operations; inspect recovery')
            matches = [i for i in state['instances'].values()
                       if i.get('digest') == app['revision'] and i.get('scope') == app['scope']]
            if (len(matches) > 1 or not matches and app['generation'] != 0
                    or matches and (matches[0].get('generation') != app['generation']
                        or matches[0].get('state') != 'stopped' or matches[0].get('resources')
                        or matches[0].get('reservations'))):
                raise AssemblyError('Unused deployment generation changed; inspect recovery')

    async def capture(self, record):
        spec = record['recipe']
        providers = self.child(spec, 'providers')
        rows = {r['deployment_id']: r for r in await self.bootstrap.manager.client.deployments()}
        captured = {}
        for alias, entry in spec['model_apps'].items():
            row = rows.get(entry['deployment_id'])
            receipt = record['registered'].get(alias)
            if row is None:
                if receipt or 'restart_from' in entry:
                    raise AssemblyError('Model publication disappeared before startup abort')
                continue
            if not receipt and row == entry.get('restart_from'):
                captured[alias] = row
                continue
            identity = (providers or {}).get('prepared', {}).get(alias)
            binding = {**identity, 'generation': identity['generation'] + 1,
                       'component': 'backend', 'port': 'http'} if identity else None
            config = module('server').validate_config(entry['app']['components']['backend']['values']['connector'])
            # Also recognize publication whose save succeeded but bootstrap
            # checkpoint did not. The exact prepared binding, configuration and
            # declared model selections must still belong to this startup.
            selected = {m['id']: m for m in entry['models']}
            actual = {m['id']: m for m in row.get('models', [])}
            if (not binding or row.get('binding') != binding or row.get('state') != 'ready'
                    or row.get('node_id') != entry['app']['node_id'] or row.get('name') != entry['name']
                    or row.get('engine') != config['engine'] or row.get('mode', 'attached') != 'attached'
                    or row.get('config_revision') != module('server').configuration_revision(config)
                    or any(row.get(k) for k in ('managed', 'engine_binding', 'engine_idle', 'recovery',
                        'connector_update', 'engine_update', 'operation_stop', 'last_operation_stop'))
                    or selected.keys() != actual.keys()
                    or any(actual[k].get('context_limit') != v.get('context_limit')
                        or 'operations' in v and actual[k].get('operations') != v['operations']
                        for k, v in selected.items())
                    or receipt and digest(row) != receipt['directory_hash']):
                raise AssemblyError('Model publication changed; inspect it before aborting startup')
            captured[alias] = row
        return deepcopy(captured)

    async def check_publications(self, record):
        """Reject edits between pending advances before stopping more providers."""
        spec = record['recipe']
        providers = self.child(spec, 'providers')
        rows = {r['deployment_id']: r for r in await self.bootstrap.manager.client.deployments()}
        for alias, entry in spec['model_apps'].items():
            original = record['abort']['rows'].get(alias)
            current = rows.get(entry['deployment_id'])
            if current == original:
                continue
            if original is not None and providers is not None and providers['state'] == 'aborted':
                abort = AppDeploymentAbort(self.deployment)._validate(providers,
                    self.bootstrap.child_id({'owner': spec['owner'],
                        'operation_id': record['abort']['operation_id']}, 'providers'))
                target = abort['targets'][alias]
                desired = {**original, 'state': 'stopped', 'revision': original['revision'] + 1,
                    'binding': {**original['binding'],
                        'generation': target['generation'] + int(target['stop'])}}
                if target['instance_id'] == original['binding']['instance_id'] and current == desired:
                    continue
            raise AssemblyError('Model publication changed during startup abort')

    async def publish_stopped(self, record):
        spec = record['recipe']
        providers = self.child(spec, 'providers')
        for alias, original in record['abort']['rows'].items():
            entry = spec['model_apps'][alias]
            if providers is not None:
                abort = AppDeploymentAbort(self.deployment)._validate(
                    providers, self.bootstrap.child_id({'owner': spec['owner'],
                    'operation_id': record['abort']['operation_id']}, 'providers'))
                if providers['state'] != 'aborted':
                    raise AssemblyError('Wait for model provider abort to finish')
                target = abort['targets'][alias]
                generation = target['generation'] + int(target['stop'])
                identity = target['instance_id']
            else:
                generation, identity = entry['app']['generation'], original['binding']['instance_id']
            binding = original['binding']
            if identity != binding['instance_id'] or generation < binding['generation']:
                raise AssemblyError('Stopped model identity differs from its publication')
            state = await self.deployment.lifecycle.status(entry['app']['node_id'])
            instance = state['instances'].get(identity, {})
            if (state.get('owner') != spec['owner'] or state.get('node_id') != entry['app']['node_id']
                    or instance.get('digest') != binding['revision'] or instance.get('scope') != entry['app']['scope']
                    or instance.get('generation') != generation or instance.get('state') != 'stopped'
                    or instance.get('resources') or instance.get('reservations')):
                raise AssemblyError('Model provider has not retained its verified stopped generation')
            desired = {**original, 'state': 'stopped', 'binding': {**binding, 'generation': generation}}
            current = await self.bootstrap.manager.client.deployment(entry['deployment_id'])
            if desired == original:
                if current != original: raise AssemblyError('Stopped model publication changed')
            elif current != {**desired, 'revision': original['revision'] + 1}:
                if current != original: raise AssemblyError('Model publication changed during startup abort')
                # The directory's revision CAS makes lost write acknowledgements
                # retryable without creating another update or losing metadata.
                await self.bootstrap.manager.client.save(desired)

    async def advance(self, *, owner, source_operation_id, operation_id):
        if not _matches(NAME, operation_id) or operation_id == source_operation_id:
            raise AssemblyError('Choose a distinct stable model startup abort ID')
        bootstrap = self.bootstrap
        path = bootstrap._path(source_operation_id)
        bootstrap._private(bootstrap.root, directory=True)
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            record = bootstrap._load(path)
            if record['recipe']['owner'] != owner:
                raise AssemblyError('Model startup belongs to another owner')
            if 'abort' not in record:
                record.update(abort={'operation_id': operation_id, 'rows': None},
                              state='pending', phase='aborting', app='')
                await bootstrap._checkpoint(path, record)
            abort = validate(record)
            if abort['operation_id'] != operation_id:
                raise AssemblyError('Resume the original model startup abort')
            if abort['rows'] is None:
                abort['rows'] = await self.capture(record)
                await bootstrap._checkpoint(path, record)
            for role in ('consumers', 'providers'):
                await self.check_publications(record)
                child = self.child(record['recipe'], role)
                if child is None:
                    await self.verify_unused_child(record['recipe'], role)
                    continue
                result = await AppDeploymentAbort(self.deployment).advance(owner=owner,
                    source_operation_id=bootstrap.child_id(record['recipe'], role),
                    operation_id=bootstrap.child_id({'owner': owner, 'operation_id': operation_id}, role))
                if result['state'] != 'aborted':
                    record['app'] = result['app']
                    await bootstrap._checkpoint(path, record)
                    return bootstrap._public(record)
            await self.publish_stopped(record)
            record.update(state='aborted', phase='aborted', app='')
            await bootstrap._checkpoint(path, record)
            return bootstrap._public(record)
