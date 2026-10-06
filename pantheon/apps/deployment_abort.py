"""Abort a partial ordinary deployment without treating timeouts as exits.

The original deployment journal is the cancellation fence. Its same sidecar lock
serializes cooperating local owners; this is not distributed replica fencing.
Existing node operations settle before exact generations are stopped in reverse
dependency order. Artifacts, data and external shared providers are retained.
"""
from .dependency_assembly import AssemblyError, NAME, _matches
from .deployment import deployment_recipe
from pantheon.platform.registry_lock import registry_lock


class AppDeploymentAbort:
    def __init__(self, deployment):
        self.deployment = deployment

    def _validate(self, record, operation_id):
        abort = record.get('abort')
        if (not isinstance(abort, dict) or set(abort) != {'operation_id', 'targets', 'stopped'}
                or abort['operation_id'] != operation_id or not _matches(NAME, operation_id)
                or operation_id == record['recipe']['operation_id']
                or record['phase'] not in ('aborting', 'aborted')
                or record['state'] != ('aborted' if record['phase'] == 'aborted' else 'pending')):
            raise AssemblyError('Resume the original deployment abort intent')
        targets, stopped = abort['targets'], abort['stopped']
        order = list(reversed(record['order']))
        if (not isinstance(stopped, list) or stopped != order[:len(stopped)]
                or len(stopped) > len(order) or targets is None and stopped
                or record['state'] == 'aborted' and (targets is None or stopped != order)):
            raise AssemblyError('Invalid deployment abort checkpoint')
        if targets is not None:
            if not isinstance(targets, dict) or set(targets) != set(record['recipe']['apps']):
                raise AssemblyError('Invalid deployment abort targets')
            for name, target in targets.items():
                base = record['recipe']['apps'][name]['generation']
                if (not isinstance(target, dict) or set(target) != {'instance_id', 'generation', 'stop'}
                        or type(target['stop']) is not bool or type(target['generation']) is not int
                        or target['generation'] not in ((base + 1, base + 2) if target['stop'] else (base,))
                        or not isinstance(target['instance_id'], str)
                        or target['instance_id'] and not _matches(NAME, target['instance_id'])
                        or not target['instance_id'] and (target['stop'] or base != 0)):
                    raise AssemblyError('Invalid deployment abort generation')
        return abort

    @staticmethod
    def _public(record):
        return dict(protocol=1, operation_id=record['abort']['operation_id'],
                    source_operation_id=record['recipe']['operation_id'],
                    state=record['state'], app=record['app'],
                    stopped=list(record['abort']['stopped']), observation='last-checkpoint')

    def _operations(self, recipe, name, state):
        app = recipe['apps'][name]
        operations = {}
        for action in ('install', 'prepare_start', 'start'):
            op_id = self.deployment.operation_id(recipe, name, action)
            operation = state['operations'].get(op_id)
            if operation is None:
                continue
            request = dict(protocol=1, operation_id=op_id, action=action, digest=app['revision'],
                           scope=app['scope'], generation=0 if action == 'install' else
                           app['generation'] + (action == 'start'))
            if action == 'start':
                request['start_preparation_id'] = self.deployment.operation_id(recipe, name, 'prepare_start')
            if (operation.get('request') != request
                    or operation.get('state') not in ('queued', 'running', 'succeeded', 'failed')):
                raise AssemblyError('Original node operation is conflicting or unknown; inspect Fleet recovery')
            operations[action] = operation
        return operations

    @staticmethod
    def _matches(state, app):
        matches = [(key, item) for key, item in state['instances'].items()
                   if item.get('digest') == app['revision'] and item.get('scope') == app['scope']]
        if len(matches) > 1:
            raise AssemblyError('Deployment instance identity is ambiguous')
        return matches[0] if matches else ('', {})

    def _capture(self, recipe, name, state, operations, identity):
        app = recipe['apps'][name]
        key, instance = self._matches(state, app)
        if identity is not None and key != identity['instance_id']:
            raise AssemblyError('Prepared instance differs from the original deployment checkpoint')
        base = app['generation']
        prepare, start = operations.get('prepare_start'), operations.get('start')
        if not prepare or prepare['state'] != 'succeeded':
            if (start is not None or key and (instance.get('generation') != base
                    or instance.get('state') != 'stopped' or instance.get('resources')
                    or instance.get('reservations')) or not key and base != 0):
                raise AssemblyError('Unprepared target differs from the original unused generation')
            return dict(instance_id=key, generation=base, stop=False)
        generation = instance.get('generation')
        prepared = (generation == base + 1 and instance.get('state') == 'prepared'
                    and instance.get('start_preparation_id') == prepare['request']['operation_id']
                    and not instance.get('resources') and (not start or start['state'] == 'failed'))
        consumed = (generation == base + 2 and start is not None
                    and instance.get('state') in ('starting', 'ready', 'recovered', 'failed', 'degraded', 'stop_blocked'))
        if not key or type(generation) is not int or not (prepared or consumed):
            raise AssemblyError('Original preparation was replaced or is not safe to abort')
        return dict(instance_id=key, generation=generation, stop=True)

    def _request(self, record, name):
        recipe, abort = record['recipe'], record['abort']
        app, target = recipe['apps'][name], abort['targets'][name]
        intent = dict(owner=recipe['owner'], operation_id=abort['operation_id'])
        return dict(protocol=1, operation_id=self.deployment.operation_id(intent, name, 'stop'),
                    action='stop', digest=app['revision'], scope=app['scope'], generation=target['generation'])

    def _observe(self, record, name, state):
        app, target = record['recipe']['apps'][name], record['abort']['targets'][name]
        key, instance = self._matches(state, app)
        if key != target['instance_id']:
            raise AssemblyError('Abort target was removed or replaced')
        operation = state['operations'].get(self._request(record, name)['operation_id'])
        if operation and (not target['stop'] or operation.get('request') != self._request(record, name)
                          or operation.get('state') not in ('queued', 'running', 'succeeded')):
            raise AssemblyError('Abort stop needs recovery under its original Fleet operation')
        final = target['generation'] + int(target['stop'])
        stopped = (not key and not target['stop'] or instance.get('generation') == final
                   and instance.get('state') == 'stopped' and not instance.get('resources')
                   and not instance.get('reservations'))
        if stopped:
            if target['stop'] and operation is None:
                raise AssemblyError('Stopped generation has no matching abort operation')
            return not operation or operation['state'] == 'succeeded', operation
        if (not target['stop'] or instance.get('generation') != target['generation']
                or instance.get('state') not in ('prepared', 'starting', 'ready', 'recovered',
                                                 'failed', 'degraded', 'stop_blocked', 'draining')
                or instance.get('state') == 'draining' and operation is None
                or operation and operation['state'] == 'succeeded'):
            raise AssemblyError('Abort target generation changed or has not retained its stop receipt')
        if instance.get('state') == 'prepared' and (
                instance.get('start_preparation_id') != self.deployment.operation_id(record['recipe'], name, 'prepare_start')
                or instance.get('resources')):
            raise AssemblyError('Prepared abort target changed its original reservation')
        return False, operation

    async def advance(self, *, owner, operation_id, source_operation_id):
        if (not _matches(NAME, operation_id) or operation_id == source_operation_id):
            raise AssemblyError('Choose a distinct stable abort operation ID')
        deployment = self.deployment
        path = deployment._path(source_operation_id)
        deployment._private(deployment.root, directory=True)
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            record = deployment._load(path)
            if record['recipe']['owner'] != owner:
                raise AssemblyError('Deployment belongs to another Fleet owner')
            if 'abort' not in record:
                record.update(abort=dict(operation_id=operation_id, targets=None, stopped=[]),
                              phase='aborting', state='pending', app='')
                # The same lock excludes advance; durable cancellation fences
                # all future owner attempts even if observation is interrupted.
                await deployment._checkpoint(path, record)
            abort = self._validate(record, operation_id)
            recipe = record['recipe']
            states, operations, pending = {}, {}, False
            for name in record['order']:
                states[name] = await deployment._state(recipe, name)
                operations[name] = self._operations(recipe, name, states[name])
                pending |= any(op['state'] in ('queued', 'running') for op in operations[name].values())
            if pending:
                if abort['targets'] is not None:
                    raise AssemblyError('An original operation changed after abort targets were fixed')
                return self._public(record)
            if abort['targets'] is None:
                targets = {name: self._capture(recipe, name, states[name], operations[name], record['prepared'].get(name))
                           for name in record['order']}
                abort['targets'] = targets
                await deployment._checkpoint(path, record)
            for name in record['order']:
                stopped, _ = self._observe(record, name, states[name])
                if name in abort['stopped'] and not stopped:
                    raise AssemblyError('An aborted App was restarted or replaced')
            for name in reversed(record['order']):
                stopped, operation = self._observe(record, name, await deployment._state(recipe, name))
                if not stopped:
                    record['app'] = name
                    await deployment._checkpoint(path, record)
                    if operation is None:
                        request, app = self._request(record, name), recipe['apps'][name]
                        await deployment.lifecycle.submit(app['node_id'], 'stop', app['revision'],
                            scope=app['scope'], generation=request['generation'], operation_id=request['operation_id'])
                    return self._public(record)
                if name not in abort['stopped']:
                    abort['stopped'].append(name)
                    await deployment._checkpoint(path, record)
            record.update(state='aborted', phase='aborted', app='')
            await deployment._checkpoint(path, record)
            return self._public(record)

    async def restart_recipe(self, *, owner, source_operation_id, operation_id):
        """Review an explicit new deployment after a verified completed abort."""
        deployment = self.deployment
        path = deployment._path(source_operation_id)
        deployment._private(deployment.root, directory=True)
        with registry_lock(path.with_suffix('.lock'), timeout=0):
            record = deployment._load(path)
            abort = self._validate(record, record.get('abort', {}).get('operation_id'))
            if record['recipe']['owner'] != owner or record['state'] != 'aborted':
                raise AssemblyError('Wait for the owned deployment abort to complete')
            if operation_id == source_operation_id:
                raise AssemblyError('Use a new deployment operation ID after abort')
            next_path = deployment._path(operation_id)
            if next_path.exists() or next_path.is_symlink():
                raise AssemblyError('Resume the existing deployment instead of planning a replacement')
            apps = {}
            for name, app in record['recipe']['apps'].items():
                stopped, _ = self._observe(record, name, await deployment._state(record['recipe'], name))
                if not stopped:
                    raise AssemblyError('Aborted App generation is no longer stopped')
                target = abort['targets'][name]
                apps[name] = {**app, 'generation': target['generation'] + int(target['stop'])}
            return deployment_recipe(owner, operation_id, apps)[0]
