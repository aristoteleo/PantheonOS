"""Owner startup sequencing over the existing App deployment and model directory.

This journal owns no processes, keys or model routes. Artifacts and node-vault
credentials must already be prepared. It resumes the same provider deployment,
registers explicit models, and only then advances an ordinary consumer deployment.
"""
import hashlib
import json
import re
from pathlib import Path

from pantheon.apps.dependency_assembly import AssemblyError, IDENT, NAME, _copy, _matches
from pantheon.apps.deployment import deployment_recipe
from pantheon.apps.owner_journal import OwnerJournal
from pantheon.platform.registry_lock import registry_lock
from .prepared_registration import inputs


def resolve_models(value, bindings):
    if isinstance(value, dict):
        if '$model' in value:
            if set(value) != {'$model'} or not isinstance(value['$model'], str) or value['$model'] not in bindings:
                raise AssemblyError('Use an exact declared model provider reference')
            return dict(bindings[value['$model']])
        return {key: resolve_models(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_models(item, bindings) for item in value]
    return value


def recipe(*, owner, operation_id, apps, model_apps, kind='model-services'):
    value = _copy(dict(owner=owner, operation_id=operation_id, apps=apps, model_apps=model_apps, kind=kind))
    if (kind != 'model-services' or not _matches(IDENT, owner) or not _matches(NAME, operation_id)
            or not isinstance(model_apps, dict) or not 1 <= len(model_apps) <= 8
            or not isinstance(apps, dict) or model_apps.keys() & apps.keys()):
        raise AssemblyError('Supply distinct Model Service providers and consumer Apps')
    model_apps, apps = value['model_apps'], value['apps']
    bindings, deployments = {}, set()
    for alias, item in model_apps.items():
        if (not _matches(NAME, alias) or not isinstance(item, dict)
                or set(item) != {'app', 'deployment_id', 'name', 'models'}
                or not isinstance(item['deployment_id'], str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', item['deployment_id'])
                or item['deployment_id'] in deployments):
            raise AssemblyError('Supply unique prepared Model Service registrations')
        deployments.add(item['deployment_id'])
        app = item['app']
        deployment_recipe(owner, operation_id, {alias: app})
        backend = app['components'].get('backend', {})
        if (app['scope'] != 'model-' + item['deployment_id'] or app['bindings']
                or set(app['components']) != {'backend'}
                or set(backend) - {'values', 'credentials'} or backend.get('credentials')
                or not isinstance(backend.get('values'), dict) or set(backend['values']) != {'connector'}):
            raise AssemblyError('Model providers must use ordinary prepared Connector configuration')
        bindings[alias] = dict(node_id=app['node_id'], instance_id='prepared', revision=app['revision'],
                               generation=app['generation']+2, component='backend', port='http')
        inputs(item['name'], bindings[alias], backend['values']['connector'], item['models'])
    # Validate all references before accepting any node-side operation.
    consumers = resolve_models(apps, bindings)
    deployment_recipe(owner, operation_id, consumers)
    provider_targets = {(m['app']['node_id'], m['app']['revision'], m['app']['scope']) for m in model_apps.values()}
    if any((a['node_id'], a['revision'], a['scope']) in provider_targets for a in consumers.values()):
        raise AssemblyError('A consumer cannot redeploy its model provider')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class ModelServiceBootstrap(OwnerJournal):
    error_type = AssemblyError

    def __init__(self, deployment, manager, root):
        self.deployment, self.manager, self.root = deployment, manager, Path(root)

    def _path(self, operation_id):
        if not _matches(NAME, operation_id):
            raise AssemblyError('Use the original model startup operation ID')
        return self.root / (operation_id + '.json')

    def _load(self, path):
        self._private(path)
        with path.open('rb') as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise AssemblyError('Invalid model startup checkpoint')
        record = json.loads(raw)
        spec = recipe(**record['recipe'])
        if (record.get('protocol') != 1 or not isinstance(record.get('registered'), dict)
                or record['registered'].keys() - spec['model_apps'].keys()
                or record.get('state') not in ('pending', 'ready')
                or record.get('phase') not in ('installing', 'preparing', 'starting', 'registering', 'ready')
                or record.get('app') not in ('', *spec['apps'], *spec['model_apps'])):
            raise AssemblyError('Invalid model startup checkpoint')
        for receipt in record['registered'].values():
            if (not isinstance(receipt, dict) or set(receipt) != {'binding', 'config_revision', 'directory_hash'}
                    or any(not _matches(r'[a-f0-9]{64}', receipt[k]) for k in ('config_revision', 'directory_hash'))):
                raise AssemblyError('Invalid model registration receipt')
        return record

    @staticmethod
    def _public(record):
        return {key: record[key] for key in ('protocol', 'state', 'phase', 'app')} | {
            'operation_id': record['recipe']['operation_id'], 'observation': 'last-checkpoint'}

    def inspect(self, *, owner, operation_id):
        self._private(self.root, directory=True)
        record = self._load(self._path(operation_id))
        if record['recipe']['owner'] != owner:
            raise AssemblyError('Model startup belongs to another Fleet owner')
        return self._public(record)

    @staticmethod
    def child_id(spec, role):
        return 'model-start-' + digest([spec['owner'], spec['operation_id'], role])

    async def advance(self, *, owner, operation_id, apps=None, model_apps=None, kind='model-services'):
        proposed = recipe(owner=owner, operation_id=operation_id, apps=apps, model_apps=model_apps, kind=kind) if (
            apps is not None or model_apps is not None) else None
        path = self._path(operation_id)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private(self.root, directory=True)
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            if path.exists() or path.is_symlink():
                record = self._load(path)
                if record['recipe']['owner'] != owner or proposed is not None and proposed != record['recipe']:
                    raise AssemblyError('Model startup belongs to another immutable recipe')
            else:
                if proposed is None or sum(1 for _ in self.root.glob('*.json')) >= 1024:
                    raise AssemblyError('Supply a model startup recipe and available journal capacity')
                record = dict(protocol=1, recipe=proposed, registered={}, state='pending', phase='installing', app='')
                await self._checkpoint(path, record)
            spec = record['recipe']

            async def progress(result):
                record.update({key: result[key] for key in ('state', 'phase', 'app')})
                await self._checkpoint(path, record)
                return self._public(record)

            providers = await self.deployment.advance(owner=owner, operation_id=self.child_id(spec, 'providers'),
                apps={alias: entry['app'] for alias, entry in spec['model_apps'].items()})
            if providers['state'] != 'ready':
                return await progress(providers)
            bindings = {alias: {**identity, 'generation': identity['generation']+1, 'component':'backend', 'port':'http'}
                        for alias, identity in providers['prepared'].items()}
            for alias, entry in spec['model_apps'].items():
                if alias not in record['registered']:
                    await progress(dict(state='pending', phase='registering', app=alias))
                    row = await self.manager.register_prepared(entry['deployment_id'], entry['name'], bindings[alias],
                        entry['app']['components']['backend']['values']['connector'], entry['models'])
                    record['registered'][alias] = dict(binding=bindings[alias], config_revision=row['config_revision'],
                                                       directory_hash=digest(row))
                    await self._checkpoint(path, record)
            # No repeated provider discovery on each pending consumer poll. Check
            # exact directory/admission instead, without waking/loading any model.
            rows = {row['deployment_id']: row for row in await self.manager.client.deployments()}
            for alias, receipt in record['registered'].items():
                row = rows.get(spec['model_apps'][alias]['deployment_id'])
                if receipt['binding'] != bindings[alias] or digest(row) != receipt['directory_hash']:
                    raise AssemblyError('Published model service changed; inspect the original startup')
                status = await self.manager.rpc(bindings[alias], 'status')
                activity = await self.manager.rpc(bindings[alias], 'activity')
                if (status.get('config_revision') != receipt['config_revision'] or status.get('accepting') is not True
                        or status.get('active_model_operations') != 0 or activity.get('accepting') is not True
                        or (activity.get('engine_idle') or {}).get('admission_fenced')):
                    raise AssemblyError('Model service is unavailable; no consumer was restarted')
            result = await self.deployment.advance(owner=owner, operation_id=self.child_id(spec, 'consumers'),
                                                   apps=resolve_models(spec['apps'], bindings))
            return await progress(result)
