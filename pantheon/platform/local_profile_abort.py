"""Explicit local startup cleanup and restart admission over generic App journals.

The enclosing LocalFleet lock owns the profile. No new recipe is submitted while
cleanup is pending; missing child journals must agree with the native node ledger.
"""
from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.deployment_abort import AppDeploymentAbort
from pantheon.apps.deployment_restart import plan_restart
from pantheon.models.bootstrap_abort import ModelBootstrapAbort


def children(session, record):
    recipe = record['recipe']
    yield 'consumers', session._consumer_id(recipe), recipe['apps']
    if recipe.get('model_apps'):
        yield 'providers', session.bootstrap.child_id(recipe, 'providers'), {
            name: entry['app'] for name, entry in recipe['model_apps'].items()}


async def unused(session, source_id, apps):
    state = await session.wire.status(session.info.node_id)
    if state.get('owner') != session.info.fleet_id or state.get('node_id') != session.info.node_id:
        raise AssemblyError('Unused profile targets belong to another node owner')
    recipe = dict(owner=session.info.fleet_id, operation_id=source_id)
    generations = {}
    for name, app in apps.items():
        if app['node_id'] != session.info.node_id:
            raise AssemblyError('Local profile target moved to another node')
        if any(session.deploy.operation_id(recipe, name, action) in state['operations']
               for action in ('install', 'prepare_start', 'start')):
            raise AssemblyError('Missing profile child journal has node operations; inspect recovery')
        found = [i for i in state['instances'].values()
                 if i.get('digest') == app['revision'] and i.get('scope') == app['scope']]
        if (len(found) > 1 or not found and app['generation'] != 0
                or found and (found[0].get('generation') != app['generation']
                    or found[0].get('state') != 'stopped' or found[0].get('resources')
                    or found[0].get('reservations'))):
            raise AssemblyError('Unused profile generation changed; inspect recovery')
        generations[name] = app['generation']
    return generations


async def untouched_models(session, recipe):
    rows = {row['deployment_id']: row for row in await session.directory.deployments()}
    result = {}
    for name, entry in recipe.get('model_apps', {}).items():
        current = rows.get(entry['deployment_id'])
        previous = entry.get('restart_from')
        if current != previous:
            raise AssemblyError('Unsubmitted model startup has changed publications')
        if previous is not None:
            result[name] = previous
    return result


async def abort(session):
    record, recipe = session._record, session._record['recipe']
    if record['phase'] == 'starting':
        intent = {**record, 'phase': 'aborting', 'startup_abort': True}
        await session._checkpoint(session.path, intent)
        record.update(intent)
    if record['phase'] != 'aborting' or record.get('startup_abort') is not True:
        raise AssemblyError('Resume the original local startup cleanup')
    operation_id = 'profile-abort-' + str(record['cycle'])
    if recipe.get('kind'):
        path = session.bootstrap._path(recipe['operation_id'])
        if path.exists() or path.is_symlink():
            source = session.bootstrap._load(path)
            if source['recipe'] != recipe:
                raise AssemblyError('Model startup differs from the saved profile')
            result = await session.bootstrap.abort(owner=session.info.fleet_id,
                source_operation_id=recipe['operation_id'], operation_id=operation_id)
            if result['state'] != 'aborted': return False
            source = session.bootstrap._load(path)
            record['models'] = {name: await session.directory.deployment(recipe['model_apps'][name]['deployment_id'])
                                for name in source['abort']['rows']}
        else:
            for _, source_id, apps in children(session, record):
                if session.deploy._path(source_id).exists() or session.deploy._path(source_id).is_symlink():
                    raise AssemblyError('Model bootstrap journal is missing for an existing child deployment')
                await unused(session, source_id, apps)
            record['models'] = await untouched_models(session, recipe)
    else:
        source_id = recipe['operation_id']
        path = session.deploy._path(source_id)
        if path.exists() or path.is_symlink():
            if session.deploy._load(path)['recipe'] != recipe:
                raise AssemblyError('Startup deployment differs from the saved profile')
            result = await AppDeploymentAbort(session.deploy).advance(owner=session.info.fleet_id,
                source_operation_id=source_id, operation_id=operation_id)
            if result['state'] != 'aborted': return False
        else:
            await unused(session, source_id, recipe['apps'])
    await session._checkpoint(session.path, record)
    return True


async def restart_state(session, record):
    """Verify the completed abort before selecting a fresh explicit start cycle."""
    recipe = record['recipe']
    if record['phase'] != 'stopped' or record.get('startup_abort') is not True:
        raise AssemblyError('Complete the original startup cleanup before restarting')
    bootstrap_present = False
    if recipe.get('kind'):
        path = session.bootstrap._path(recipe['operation_id'])
        bootstrap_present = path.exists() or path.is_symlink()
        if bootstrap_present:
            source = session.bootstrap._load(path)
            if (source['recipe'] != recipe or source['state'] != 'aborted'
                    or source['abort']['operation_id'] != 'profile-abort-' + str(record['cycle'])):
                raise AssemblyError('Saved model startup cleanup is not complete')
            if record['models'].keys() != source['abort']['rows'].keys():
                raise AssemblyError('Stopped profile is missing model cleanup receipts')
            await ModelBootstrapAbort(session.bootstrap).check_publications(source)
        elif await untouched_models(session, recipe) != record['models']:
            raise AssemblyError('Unsubmitted model publication checkpoint changed')
    generations = {}
    for role, source_id, apps in children(session, record):
        path = session.deploy._path(source_id)
        if path.exists() or path.is_symlink():
            if recipe.get('kind'):
                if not bootstrap_present:
                    raise AssemblyError('Model bootstrap journal is missing for an existing child deployment')
                child = ModelBootstrapAbort(session.bootstrap).child(recipe, role)
            else:
                child = session.deploy._load(path)
                if child['recipe'] != recipe:
                    raise AssemblyError('Aborted deployment differs from the saved profile')
            if child['state'] != 'aborted':
                raise AssemblyError('Original startup cleanup has not completed')
            new_id = 'local-profile-' + str(record['cycle'] + 1)
            if recipe.get('kind'):
                new_id = session.bootstrap.child_id({'owner': session.info.fleet_id, 'operation_id': new_id}, role)
            planned = await plan_restart(session.deploy, owner=session.info.fleet_id,
                source_operation_id=source_id, operation_id=new_id, apps=list(apps))
            generations.update({name: app['generation'] for name, app in planned['apps'].items()})
        else:
            generations.update(await unused(session, source_id, apps))
    stopped = {}
    for name, row in record['models'].items():
        if row['state'] != 'stopped' or await session.directory.deployment(row['deployment_id']) != row:
            raise AssemblyError('Stopped model publication changed before restart')
        if row['binding']['generation'] != generations[name]:
            raise AssemblyError('Stopped model publication differs from the aborted generation')
        stopped[name] = row
    return generations, stopped
