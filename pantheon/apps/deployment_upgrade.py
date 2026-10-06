"""Prepare compatible App release candidates using Fleet's ordinary data clone.

The source deployment must already be drained/stopped. Preparation does not start
or publish the candidate, transform schemas, or discard either data generation.
The returned recipe goes through AppDeployment; routing/cutover is separate.
"""
import json
from pathlib import Path

from .dependency_assembly import AssemblyError, DIGEST, NAME, DEPLOYMENT_BYTES, _copy, _matches
from .deployment import AppDeployment, deployment_recipe
from .deployment_preview import preview_deployment
from .deployment_restart import plan_restart
from .owner_journal import OwnerJournal
from .schema import DataSchema
from pantheon.platform.registry_lock import registry_lock


async def _plan(deployment, *, owner, source_operation_id, operation_id, apps, revisions):
    if (not isinstance(revisions, dict) or not revisions or not isinstance(apps, list)
            or not revisions.keys() <= set(apps)
            or any(not _matches(NAME, k) or not _matches(DIGEST, v) for k, v in revisions.items())):
        raise AssemblyError('Select exact changed releases within the stopped restart group')
    recipe = await plan_restart(deployment, owner=owner, source_operation_id=source_operation_id,
                                operation_id=operation_id, apps=apps, allow_aborted=False)
    sources = {}
    for name, revision in revisions.items():
        app = recipe['apps'][name]
        if app['revision'] == revision:
            raise AssemblyError('An upgrade target must differ from its source release')
        old = await deployment.lifecycle.manifest(app['node_id'], app['revision'])
        new = await deployment.lifecycle.manifest(app['node_id'], revision)
        if old['manifest']['id'] != new['manifest']['id']:
            raise AssemblyError('A release upgrade must preserve the App identity')
        try:
            schemas = [DataSchema.model_validate(m['manifest']['dataSchema'])
                       if m['manifest'].get('dataSchema') is not None else None for m in (old, new)]
        except ValueError:
            raise AssemblyError('Invalid App data schema declaration') from None
        source_schema, target_schema = schemas
        if source_schema is not None or target_schema is not None:
            if (source_schema is None or target_schema is None or source_schema.id != target_schema.id
                    or source_schema.version not in target_schema.accepts):
                raise AssemblyError('Target App cannot open the source data schema; explicit migration is required')
        sources[name] = {'digest': app['revision'], 'generation': app['generation']}
        app.update(revision=revision, generation=0)
    # Same contract compiler as normal preparation, including pinned external
    # providers and policies referencing all selected upcoming generations.
    await preview_deployment(deployment.lifecycle, **recipe)
    return {'protocol': 1, 'source_operation_id': source_operation_id,
            'recipe': recipe, 'sources': sources}


class AppUpgradePreparation(OwnerJournal):
    """Durable copy intent; each call advances bounded Fleet operations.

    Installed target artifacts are a prerequisite. Keep the same operation ID
    after timeout/cancellation. Previously used candidate data is never adopted.
    """
    error_type = AssemblyError
    maximum_bytes = 2 * DEPLOYMENT_BYTES

    def __init__(self, deployment, root):
        self.deployment, self.root = deployment, Path(root)

    def _path(self, operation_id):
        if not _matches(NAME, operation_id):
            raise AssemblyError('Use a stable upgrade preparation operation ID')
        return self.root / (operation_id + '.json')

    def _load(self, path):
        self._private(path)
        with path.open('rb') as stream:
            raw = stream.read(self.maximum_bytes + 1)
        if len(raw) > self.maximum_bytes:
            raise AssemblyError('Upgrade preparation exceeds its storage limit')
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {'protocol', 'source_operation_id', 'recipe', 'sources'}
                or type(value['protocol']) is not int or value['protocol'] != 1
                or not _matches(NAME, value['source_operation_id'])
                or not isinstance(value['sources'], dict) or not value['sources']):
            raise AssemblyError('Invalid upgrade preparation checkpoint')
        recipe, _ = deployment_recipe(**value['recipe'])
        if not value['sources'].keys() <= recipe['apps'].keys():
            raise AssemblyError('Invalid upgrade source selection')
        for name, source in value['sources'].items():
            if (not isinstance(source, dict) or set(source) != {'digest', 'generation'}
                    or not _matches(DIGEST, source['digest']) or type(source['generation']) is not int
                    or source['generation'] <= 0 or recipe['apps'][name]['generation'] != 0
                    or source['digest'] == recipe['apps'][name]['revision']):
                raise AssemblyError('Invalid upgrade data source')
        return value

    async def prepared_recipe(self, *, owner, operation_id):
        """Private owner input for AppDeployment, not a public status payload."""
        result = await self.advance(owner=owner, operation_id=operation_id)
        if result['state'] != 'prepared':
            raise AssemblyError('Wait for all candidate data copies before deploying')
        record = self._load(self._path(operation_id))
        if record['recipe']['owner'] != owner:
            raise AssemblyError('Upgrade belongs to another Fleet owner')
        return _copy(record['recipe'], DEPLOYMENT_BYTES)

    async def rollback_recipe(self, *, owner, operation_id, rollback_operation_id):
        """Explicit rollback to retained source data after stopping the candidate.

        Candidate-only writes stay in its separate data directory; they are not
        merged or downgraded into the old schema. The caller must present this
        data policy before publishing the returned ordinary deployment recipe.
        Partial candidate starts must complete the ordinary deployment abort
        before rollback; uncertain node operations are never treated as stopped.
        """
        record = self._load(self._path(operation_id))
        candidate = record['recipe']
        if candidate['owner'] != owner:
            raise AssemblyError('Upgrade belongs to another Fleet owner')
        # Restart freezes references to retained Apps inside both bindings and
        # component values. Contract preview only interprets declared bindings;
        # recheck the original retained set too, including policy-only refs.
        source_path = self.deployment._path(record['source_operation_id'])
        with registry_lock(source_path.with_suffix('.lock'), timeout=0):
            source = self.deployment._load(source_path)
        original = source['recipe']
        if (original['owner'] != owner or source['state'] != 'ready' or source['phase'] != 'ready'
                or set(source['prepared']) != set(original['apps'])
                or not candidate['apps'].keys() <= original['apps'].keys()):
            raise AssemblyError('Original deployment is no longer available for rollback review')
        for name in original['apps'].keys() - candidate['apps'].keys():
            state = await self.deployment._state(original, name)
            self.deployment._instance(state, original['apps'][name],
                self.deployment.operation_id(original, name, 'prepare_start'),
                source['prepared'][name], ready=True)
        candidate_path = self.deployment._path(operation_id)
        with registry_lock(candidate_path.with_suffix('.lock'), timeout=0):
            current = self.deployment._load(candidate_path)
        if current['recipe'] != candidate:
            raise AssemblyError('Candidate differs from this upgrade intent')
        if current['state'] == 'aborted':
            from .deployment_abort import AppDeploymentAbort
            result = await AppDeploymentAbort(self.deployment).restart_recipe(owner=owner,
                source_operation_id=operation_id, operation_id=rollback_operation_id)
        else:
            result = await plan_restart(self.deployment, owner=owner, source_operation_id=operation_id,
                                        operation_id=rollback_operation_id, apps=list(candidate['apps']))
            expected = {name: {**app, 'generation': app['generation'] + 3}
                        for name, app in candidate['apps'].items()}
            if result['apps'] != expected:
                raise AssemblyError('Completed candidate differs from this upgrade intent')
        for name, origin in record['sources'].items():
            target = result['apps'][name]
            state = await self.deployment._state(candidate, name)
            previous = [i for i in state['instances'].values()
                        if i.get('digest') == origin['digest'] and i.get('scope') == target['scope']]
            if (len(previous) != 1 or previous[0].get('generation') != origin['generation']
                    or previous[0].get('state') != 'stopped' or previous[0].get('resources')
                    or previous[0].get('reservations')):
                raise AssemblyError('Original rollback data generation changed or is no longer stopped')
            target.update(revision=origin['digest'], generation=origin['generation'])
        await preview_deployment(self.deployment.lifecycle, **result)
        return {'data_policy': 'retained-source-data', 'candidate_writes': 'retained-separately', 'recipe': result}

    async def advance(self, *, owner, operation_id, source_operation_id=None, apps=None, revisions=None):
        path = self._path(operation_id)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private(self.root, directory=True)
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            exists = path.exists() or path.is_symlink()
            if exists:
                record = self._load(path)
                if record['recipe']['owner'] != owner:
                    raise AssemblyError('Upgrade belongs to another Fleet owner')
                expected = (record['source_operation_id'], sorted(record['recipe']['apps']),
                            {name: record['recipe']['apps'][name]['revision'] for name in record['sources']})
                if ((source_operation_id is not None and source_operation_id != expected[0])
                        or (apps is not None and sorted(apps) != expected[1])
                        or (revisions is not None and revisions != expected[2])):
                    raise AssemblyError('Resume the original immutable upgrade preparation')
                source_operation_id, apps, revisions = expected
            proposed = await _plan(self.deployment, owner=owner, source_operation_id=source_operation_id,
                                   operation_id=operation_id, apps=apps, revisions=revisions)
            if exists and record != proposed:
                raise AssemblyError('Upgrade source or retained dependency generation changed')
            # Check every destination before the first copy, and on every retry.
            for name, origin in proposed['sources'].items():
                target = proposed['recipe']['apps'][name]
                state = await self.deployment._state(proposed['recipe'], name)
                matches = [i for i in state['instances'].values()
                           if i.get('digest') == target['revision'] and i.get('scope') == target['scope']]
                op_id = AppDeployment.operation_id(proposed['recipe'], name, 'clone_data')
                operation = state['operations'].get(op_id)
                if matches and (not exists or len(matches) != 1 or operation is None
                        or matches[0].get('data_source') != origin or matches[0].get('generation') != 0
                        or matches[0].get('state') != 'stopped' or matches[0].get('resources')
                        or matches[0].get('reservations')):
                    raise AssemblyError('Candidate data already exists outside this unused upgrade intent')
                if not exists and operation is not None:
                    raise AssemblyError('An existing clone operation has no matching owner intent')
            if not exists:
                if sum(1 for _ in self.root.glob('*.json')) >= 1024:
                    raise AssemblyError('Upgrade preparation journal is full')
                await self._checkpoint(path, proposed)
            for name, origin in proposed['sources'].items():
                target = proposed['recipe']['apps'][name]
                op_id = AppDeployment.operation_id(proposed['recipe'], name, 'clone_data')
                request = dict(protocol=1, operation_id=op_id, action='clone_data',
                               digest=target['revision'], scope=target['scope'], generation=0, data_source=origin)
                state = await self.deployment._state(proposed['recipe'], name)
                op = state['operations'].get(op_id)
                if op is None:
                    op = await self.deployment.lifecycle.submit(target['node_id'], 'clone_data', target['revision'],
                        scope=target['scope'], generation=0, operation_id=op_id, data_source=origin)
                if op.get('request') != request:
                    raise AssemblyError('Upgrade clone operation conflicts with the Fleet ledger')
                if op.get('state') in ('queued', 'running'):
                    return dict(protocol=1, operation_id=operation_id, state='pending', app=name)
                if op.get('state') != 'succeeded':
                    raise AssemblyError('Resume or inspect the original failed Fleet clone operation')
                state = await self.deployment._state(proposed['recipe'], name)
                matches = [i for i in state['instances'].values() if i.get('digest') == target['revision']
                           and i.get('scope') == target['scope']]
                if (len(matches) != 1 or matches[0].get('data_source') != origin
                        or matches[0].get('generation') != 0 or matches[0].get('state') != 'stopped'
                        or matches[0].get('resources') or matches[0].get('reservations')):
                    raise AssemblyError('Fleet did not retain the exact unused candidate data')
            return dict(protocol=1, operation_id=operation_id, state='prepared', app='')
