"""Owner startup sequencing over the existing App deployment and model directory.

This journal owns no processes, keys or model routes. Artifacts and node-vault
credentials are either pre-provisioned or explicitly prepared by the owner host.
It resumes the same provider deployment,
registers explicit models, and only then advances an ordinary consumer deployment.
"""
import hashlib
import json
import re
from pathlib import Path

from pantheon.apps.dependency_assembly import AssemblyError, IDENT, NAME, _copy, _matches, DEPLOYMENT_BYTES
from pantheon.apps.deployment import deployment_recipe
from pantheon.apps.owner_journal import OwnerJournal
from pantheon.platform.registry_lock import registry_lock
from .prepared_registration import inputs, rebind_inputs
from .platform_budget import budget_connector, budget_receipt


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
    value = _copy(dict(owner=owner, operation_id=operation_id, apps=apps, model_apps=model_apps, kind=kind), DEPLOYMENT_BYTES)
    if (kind != 'model-services' or not _matches(IDENT, owner) or not _matches(NAME, operation_id)
            or not isinstance(model_apps, dict) or not 1 <= len(model_apps) <= 8
            or not isinstance(apps, dict) or model_apps.keys() & apps.keys()):
        raise AssemblyError('Supply distinct Model Service providers and consumer Apps')
    model_apps, apps = value['model_apps'], value['apps']
    bindings, deployments = {}, set()
    for alias, item in model_apps.items():
        if (not _matches(NAME, alias) or not isinstance(item, dict)
                or set(item) - {'app', 'deployment_id', 'name', 'models', 'credential_source', 'restart_from'}
                or not {'app', 'deployment_id', 'name', 'models'} <= set(item)
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
        if 'restart_from' in item:
            previous = item['restart_from']
            if not isinstance(previous, dict) or not isinstance(previous.get('binding'), dict):
                raise AssemblyError('Supply the exact stopped publication for model restart')
            target = {**bindings[alias], 'instance_id': previous['binding'].get('instance_id')}
            _, selection = rebind_inputs(previous, target, backend['values']['connector'])
            _, requested = inputs(item['name'], target, backend['values']['connector'], item['models'])
            same_selection = selection.keys() == requested.keys() and all(
                requested[key]['context_limit'] == selection[key]['context_limit']
                and (requested[key]['operations'] is None
                     or requested[key]['operations'] == selection[key]['operations']) for key in requested)
            if (previous['deployment_id'] != item['deployment_id'] or previous['name'] != item['name']
                    or not same_selection):
                raise AssemblyError('Model restart must preserve the stopped publication and selection')
        if 'credential_source' in item:
            if item['credential_source'] != 'platform-budget':
                raise AssemblyError('Unsupported model credential source')
            budget_connector(backend['values']['connector'])
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
    maximum_bytes = 2 * DEPLOYMENT_BYTES

    def __init__(self, deployment, manager, root, *, prepare_credentials=None):
        self.deployment, self.manager, self.root = deployment, manager, Path(root)
        self.prepare_credentials = prepare_credentials

    def _path(self, operation_id):
        if not _matches(NAME, operation_id):
            raise AssemblyError('Use the original model startup operation ID')
        return self.root / (operation_id + '.json')

    def _load(self, path):
        self._private(path)
        with path.open('rb') as stream:
            raw = stream.read(self.maximum_bytes + 1)
        if len(raw) > self.maximum_bytes:
            raise AssemblyError('Invalid model startup checkpoint')
        record = json.loads(raw)
        spec = recipe(**record['recipe'])
        if (record.get('protocol') != 1 or not isinstance(record.get('registered'), dict)
                or record['registered'].keys() - spec['model_apps'].keys()
                or record.get('state') not in ('pending', 'prepared', 'ready', 'aborted')
                or record.get('phase') not in ('credentials', 'installing', 'preparing', 'prepared', 'starting', 'registering', 'ready', 'aborting', 'aborted')
                or record.get('app') not in ('', *spec['apps'], *spec['model_apps'])):
            raise AssemblyError('Invalid model startup checkpoint')
        for receipt in record['registered'].values():
            if (not isinstance(receipt, dict) or set(receipt) != {'binding', 'config_revision', 'directory_hash'}
                    or any(not _matches(r'[a-f0-9]{64}', receipt[k]) for k in ('config_revision', 'directory_hash'))):
                raise AssemblyError('Invalid model registration receipt')
        receipts = record.get('credential_receipts', {})
        expected = {alias for alias, entry in spec['model_apps'].items() if 'credential_source' in entry}
        if not isinstance(receipts, dict) or receipts.keys() - expected:
            raise AssemblyError('Invalid credential preparation receipts')
        for alias, receipt in receipts.items():
            app = spec['model_apps'][alias]['app']
            budget_receipt(receipt, owner=spec['owner'], node_id=app['node_id'],
                           connector=app['components']['backend']['values']['connector'])
        if record['registered'] and receipts.keys() != expected:
            raise AssemblyError('Model registration is missing its credential preparation receipt')
        if 'abort' in record:
            from .bootstrap_abort import validate
            validate(record)
        elif record['phase'] in ('aborting', 'aborted') or record['state'] == 'aborted':
            raise AssemblyError('Missing model startup abort checkpoint')
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

    async def abort(self, *, owner, source_operation_id, operation_id):
        from .bootstrap_abort import ModelBootstrapAbort
        return await ModelBootstrapAbort(self).advance(owner=owner, source_operation_id=source_operation_id,
                                                      operation_id=operation_id)

    async def prepare(self, *, owner, operation_id, apps=None, model_apps=None, kind='model-services'):
        """Start/register model providers, but only reserve consumer instances.

        Consumer backends receive no grants or configuration and do not start
        until advance is explicitly called. Installation hooks still run.
        Independent model providers must run to
        establish their exact published bindings.
        """
        return await self._advance(owner=owner, operation_id=operation_id, apps=apps,
            model_apps=model_apps, kind=kind, prepare_only=True)

    async def advance(self, *, owner, operation_id, apps=None, model_apps=None, kind='model-services'):
        return await self._advance(owner=owner, operation_id=operation_id, apps=apps,
            model_apps=model_apps, kind=kind, prepare_only=False)

    async def _advance(self, *, owner, operation_id, apps, model_apps, kind, prepare_only):
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
            if 'abort' in record:
                raise AssemblyError('Model startup is fenced by its original abort operation')
            spec = record['recipe']
            if prepare_only:
                child_path = self.deployment._path(self.child_id(spec, 'consumers'))
                if child_path.exists() or child_path.is_symlink():
                    child = self.deployment.inspect(owner=owner, operation_id=self.child_id(spec, 'consumers'))
                    if child['phase'] in ('starting', 'ready', 'aborting', 'aborted'):
                        raise AssemblyError('Consumers have entered startup or abort; preparation is no longer available')

            async def progress(result):
                record.update({key: result[key] for key in ('state', 'phase', 'app')})
                await self._checkpoint(path, record)
                return self._public(record)

            # Reject edited/deleted publications before credentials or any new
            # lifecycle work. An exact committed rebind is allowed after a lost
            # save/checkpoint reply; ordinary ready checks below still apply.
            for alias, entry in spec['model_apps'].items():
                if 'restart_from' not in entry or alias in record['registered']:
                    continue
                previous = entry['restart_from']
                desired = {**previous, 'state': 'ready', 'revision': previous['revision'] + 1,
                    'binding': {**previous['binding'], 'generation': previous['binding']['generation'] + 2}}
                current = await self.manager.client.deployment(entry['deployment_id'])
                if current != previous and current != desired:
                    raise AssemblyError('Stopped model publication changed; inspect the original restart')

            receipts = record.setdefault('credential_receipts', {})
            for alias, entry in spec['model_apps'].items():
                if 'credential_source' not in entry or alias in receipts:
                    continue
                if self.prepare_credentials is None:
                    raise AssemblyError('This startup requires an explicit owner budget credential preparer')
                await progress(dict(state='pending', phase='credentials', app=alias))
                app = entry['app']
                connector = app['components']['backend']['values']['connector']
                receipt = await self.prepare_credentials(owner=owner, node_id=app['node_id'], connector=dict(connector),
                                                         lifecycle=self.deployment.starter.lifecycle)
                receipts[alias] = budget_receipt(receipt, owner=owner, node_id=app['node_id'], connector=connector)
                await self._checkpoint(path, record)

            providers = await self.deployment.advance(owner=owner, operation_id=self.child_id(spec, 'providers'),
                apps={alias: entry['app'] for alias, entry in spec['model_apps'].items()})
            if providers['state'] != 'ready':
                return await progress(providers)
            bindings = {alias: {**identity, 'generation': identity['generation']+1, 'component':'backend', 'port':'http'}
                        for alias, identity in providers['prepared'].items()}
            for alias, entry in spec['model_apps'].items():
                if alias not in record['registered']:
                    await progress(dict(state='pending', phase='registering', app=alias))
                    config = entry['app']['components']['backend']['values']['connector']
                    if 'restart_from' in entry:
                        row = await self.manager.rebind_prepared(previous=entry['restart_from'],
                            binding=bindings[alias], configuration=config)
                    else:
                        row = await self.manager.register_prepared(entry['deployment_id'], entry['name'],
                            bindings[alias], config, entry['models'])
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
            run = self.deployment.prepare if prepare_only else self.deployment.advance
            result = await run(owner=owner, operation_id=self.child_id(spec, 'consumers'),
                               apps=resolve_models(spec['apps'], bindings))
            return await progress(result)
