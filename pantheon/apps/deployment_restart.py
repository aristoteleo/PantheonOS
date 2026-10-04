"""Review a restart from an original, completed ordinary App deployment.

This owns no lifecycle. Selected Apps must already be drained and stopped; the
returned immutable recipe is submitted/resumed through AppDeployment.advance.
Shared providers remain pinned to the observed original running generation.
"""
from pantheon.apps.dependency_assembly import AssemblyError, _copy, _matches, NAME
from pantheon.apps.deployment import _references, _resolve, deployment_recipe
from pantheon.platform.registry_lock import registry_lock


async def plan_restart(deployment, *, owner, source_operation_id, operation_id, apps):
    if (not _matches(NAME, operation_id) or operation_id == source_operation_id
            or not isinstance(apps, list) or not 1 <= len(apps) <= 16
            or any(not _matches(NAME, name) for name in apps) or len(set(apps)) != len(apps)):
        raise AssemblyError('Select distinct Apps from a completed deployment and a new restart operation ID')
    deployment._private(deployment.root, directory=True)
    path = deployment._path(source_operation_id)
    deployment._private(path)
    with registry_lock(path.with_suffix('.lock'), timeout=0):
        record = deployment._load(path)
    source = record['recipe']
    if source['owner'] != owner:
        raise AssemblyError('Deployment belongs to another Fleet owner')
    if (record['state'] != 'ready' or record['phase'] != 'ready'
            or set(record['prepared']) != set(source['apps'])):
        raise AssemblyError('Recover the original deployment before planning a restart')
    next_path = deployment._path(operation_id)
    if next_path.exists() or next_path.is_symlink():
        raise AssemblyError('Resume the existing restart operation instead of planning a replacement')
    selected = set(apps)
    if not selected <= source['apps'].keys():
        raise AssemblyError('Restart selections must belong to the original deployment')
    retained = source['apps'].keys() - selected
    for name in retained:
        # Brokers can refer to consumer generations in values without a startup
        # edge. Keep those policies in the same restart, not silently stale.
        if _references(source['apps'][name]) & selected:
            raise AssemblyError('Include every deployed App whose bindings or configuration reference a restarted App')
    states, generations = {}, {}
    for name, app in source['apps'].items():
        node = app['node_id']
        if node not in states:
            states[node] = await deployment._state(source, name)
        state = states[node]
        identity = record['prepared'][name]
        if name in selected:
            instance = state['instances'].get(identity['instance_id'], {})
            if (instance.get('digest') != app['revision'] or instance.get('scope') != app['scope']
                    or instance.get('state') != 'stopped' or instance.get('resources')
                    or type(instance.get('generation')) is not int
                    or instance['generation'] != identity['generation'] + 2):
                raise AssemblyError('Drain and stop the original App generation before planning its restart')
            generations[name] = instance['generation']
        else:
            deployment._instance(state, app, deployment.operation_id(source, name, 'prepare_start'),
                                 identity, ready=True)

    def freeze(value):
        if isinstance(value, dict):
            if '$app' in value:
                return _resolve(value, record['prepared']) if value['$app'] in retained else dict(value)
            return {key: freeze(item) for key, item in value.items()}
        if isinstance(value, list):
            return [freeze(item) for item in value]
        return value

    restarted = {name: {**freeze(source['apps'][name]), 'generation': generations[name]}
                 for name in sorted(selected)}
    # References to kept providers are now exact. Selected providers/consumers
    # still resolve together during ordinary prepare/configure/start.
    return _copy(deployment_recipe(owner, operation_id, restarted)[0])
