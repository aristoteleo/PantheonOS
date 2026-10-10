"""Reviewed release changes for stopped local App products.

Use ordinary installation and upgrade-copy journals; re-render the next profile
cycle with its current transport authority. Neither approval nor copying starts
Apps. A retained-data rollback is a separate reviewed decision. The enclosing
LocalFleet owner lock serializes profile writers.
"""
from pantheon.apps.dependency_assembly import AssemblyError, DEPLOYMENT_BYTES, _matches
from pantheon.apps.deployment import AppDeployment
from pantheon.apps.deployment_upgrade import AppUpgradePreparation
from pantheon.models.bootstrap import digest
from .local_profile_update import UpdateJournal, changed_paths
from pantheon.utils.registry_lock import registry_lock


class ReleaseJournal(UpdateJournal):
    maximum_bytes = 2 * DEPLOYMENT_BYTES + 32768


def release_target(source, target):
    from .local_profile import manifest
    target = manifest(target)
    if (source['apps'] != target['apps'] or source['model_apps'] != target['model_apps']
            or source['packages'].keys() != target['packages'].keys()):
        raise AssemblyError('Release updates preserve configuration, App topology and model publications; review those changes separately')
    model_packages = {item['app']['package'] for item in source['model_apps'].values()}
    for alias in model_packages:
        if source['packages'][alias]['revision'] != target['packages'][alias]['revision']:
            raise AssemblyError('Model provider releases require their model deployment update workflow')
    changes = {name: target['packages'][app['package']]['revision'] for name, app in source['apps'].items()
               if source['packages'][app['package']]['revision'] != target['packages'][app['package']]['revision']}
    if not changes:
        raise AssemblyError('Select at least one changed App release')
    return target, changes


def _journal(session):
    return ReleaseJournal(session.root/'release-updates')


def _receipt(session, review_id):
    if not _matches(r'[a-f0-9]{64}', review_id):
        raise AssemblyError('Supply an exact release review ID')
    journal = _journal(session)
    journal._private(journal.root, directory=True)
    value = journal.read(journal.root/(review_id+'.json'))
    if (not isinstance(value, dict) or set(value) != {'protocol','checkpoint_hash','source','target','mode','origins','rollback_of'}
            or type(value['protocol']) is not int or value['protocol'] != 1
            or value['mode'] not in ('upgrade','rollback') or digest(value) != review_id
            or not _matches(r'[a-f0-9]{64}',value['checkpoint_hash'])):
        raise AssemblyError('Invalid release approval receipt')
    from .local_profile import manifest
    source = manifest(value['source'])
    _, changes = release_target(source, value['target'])
    if not isinstance(value['origins'], dict) or value['origins'].keys() != changes.keys():
        raise AssemblyError('Invalid retained release identities')
    for name, origin in value['origins'].items():
        original = source if value['mode']=='upgrade' else value['target']
        app = original['apps'][name]
        if (not isinstance(origin, dict) or set(origin) != {'digest','scope','generation'}
                or origin['digest'] != original['packages'][app['package']]['revision']
                or origin['scope'] != app['scope'] or type(origin['generation']) is not int
                or not 1 <= origin['generation'] <= 3_000_000):
            raise AssemblyError('Invalid retained release identity')
    if ((value['mode']=='upgrade' and value['rollback_of'] is not None)
            or (value['mode']=='rollback' and not _matches(r'[a-f0-9]{64}',value['rollback_of']))):
        raise AssemblyError('Invalid release rollback reference')
    return value


def _proposal(session, target, rollback_of=None):
    target, changes = release_target(session.spec, target)
    record = session._load()
    if (record['phase'] != 'stopped' or session.status()['state'] not in ('unopened','stopped')
            or record['manifest_hash'] != digest(session.spec)):
        raise AssemblyError('Stop the original profile cleanly before reviewing a release change')
    if record.get('startup_abort') and rollback_of is None:
        raise AssemblyError('Restart the cleaned-up profile or roll back its retained release before preparing another upgrade')
    mode = 'rollback' if rollback_of is not None else 'upgrade'
    if mode=='rollback':
        previous = _receipt(session, rollback_of)
        if previous['mode']!='upgrade' or previous['target']!=session.spec or previous['source']!=target:
            raise AssemblyError('Rollback must restore the exact retained source of this upgrade')
        origins = previous['origins']
    else:
        origins = {name: {'digest':session.spec['packages'][session.spec['apps'][name]['package']]['revision'],
            'scope':session.spec['apps'][name]['scope'], 'generation':record['recipe']['apps'][name]['generation']+3}
            for name in changes}
    return record, dict(protocol=1,checkpoint_hash=digest(record),source=session.spec,target=target,
                        mode=mode,origins=origins,rollback_of=rollback_of)


def review_release(session, target, *, rollback_of=None):
    _, receipt = _proposal(session,target,rollback_of)
    return dict(review_id=digest(receipt), mode=receipt['mode'], source_hash=digest(receipt['source']),
        target_hash=digest(receipt['target']), changes=changed_paths(receipt['source'],receipt['target']),
        data_policy='copy-source-data' if rollback_of is None else 'retained-source-data',
        candidate_writes='retained-separately')


def _copies(session):
    return AppUpgradePreparation(session.deploy, session.root/'release-copies')


def _copy_id(review_id):
    return 'release-copy-'+review_id[:40]


async def _all_stopped(session):
    state = await session.wire.status(session.info.node_id)
    if (state.get('owner') != session.info.fleet_id or state.get('node_id') != session.info.node_id
            or any(item.get('state')!='stopped' or item.get('resources') or item.get('reservations')
                   for item in state['instances'].values())):
        raise AssemblyError('All profile Apps must be stopped before approving a release')
    return state


async def _retained(session, receipt):
    state = await _all_stopped(session)
    for origin in receipt['origins'].values():
        found = [v for v in state['instances'].values() if v.get('digest')==origin['digest'] and v.get('scope')==origin['scope']]
        if len(found)!=1 or found[0].get('generation')!=origin['generation']:
            raise AssemblyError('Retained source generation changed; do not rebase this release decision')


async def approve_release(session, target, review_id, *, rollback_of=None):
    """Bounded advance; repeat the same approval while node operations are pending."""
    with registry_lock(session.root/'release-update.lock',timeout=0):
        record, receipt = _proposal(session,target,rollback_of)
        if not _matches(r'[a-f0-9]{64}',review_id) or digest(receipt)!=review_id:
            raise AssemblyError('Release or profile changed since review; review it again')
        await session.directory.initialize()
        generations, stopped = await session._restart_state(record)
        await _retained(session,receipt)
        journal = _journal(session)
        journal.root.mkdir(mode=0o700,exist_ok=True)
        journal._private(journal.root,directory=True)
        path=journal.root/(review_id+'.json')
        if path.exists() or path.is_symlink():
            if _receipt(session,review_id)!=receipt: raise AssemblyError('Release decision changed')
        else:
            await journal._checkpoint(path,receipt)
        # Persist intent before any node mutation. Exact-digest staging never
        # installs or executes an unreviewed package.
        await session._stage_packages(receipt['target'])
        _, changes = release_target(receipt['source'],receipt['target'])
        for name, revision in changes.items():
            operation_id=AppDeployment.operation_id({'owner':session.info.fleet_id,'operation_id':'release-install-'+review_id[:40]},name,'install')
            state=await session.wire.status(session.info.node_id)
            op=state['operations'].get(operation_id)
            request=dict(protocol=1,operation_id=operation_id,action='install',digest=revision,
                         scope=receipt['target']['apps'][name]['scope'],generation=0)
            if op is None:
                op=await session.wire.submit(session.info.node_id,'install',revision,scope=request['scope'],
                                             generation=0,operation_id=operation_id)
            if op.get('request')!=request: raise AssemblyError('Release install conflicts with its recorded request')
            if op.get('state') in ('queued','running'):
                return dict(state='pending',review_id=review_id,phase='install',app=name)
            if op.get('state')!='succeeded': raise AssemblyError('Inspect the original release install operation before retrying')
        if receipt['mode']=='upgrade':
            copy=await _copies(session).advance(owner=session.info.fleet_id,operation_id=_copy_id(review_id),
                source_operation_id=session._consumer_id(record['recipe']),apps=list(session.spec['apps']),revisions=changes)
            if copy['state']!='prepared': return dict(state='pending',review_id=review_id,phase='copy',app=copy['app'])
            generations.update({name:0 for name in changes})
        else:
            generations.update({name:origin['generation'] for name,origin in receipt['origins'].items()})
        await _preview(session,record,receipt['target'],generations,stopped)
        if session._load()!=record: raise AssemblyError('Profile changed during release approval')
        pointer=session.root/'approved-release.json'
        if pointer.exists() or pointer.is_symlink(): journal._private(pointer)
        await journal._checkpoint(pointer,dict(protocol=1,review_id=review_id))
        session.spec=receipt['target'];session._record=None;session._staged=False
        return dict(state='approved',review_id=review_id,target_hash=digest(session.spec),
            data_policy='copy-source-data' if receipt['mode']=='upgrade' else 'retained-source-data',
            candidate_writes='retained-separately')


async def _preview(session,record,target,generations,stopped):
    from pantheon.apps.deployment_preview import preview_deployment
    recipe=session._render(record['cycle']+1,generations,stopped,spec=target)
    if recipe.get('model_apps'):
        await preview_deployment(session.wire,owner=session.info.fleet_id,
            operation_id=session.bootstrap.child_id(recipe,'providers'),
            apps={name:item['app'] for name,item in recipe['model_apps'].items()})
    await preview_deployment(session.wire,owner=session.info.fleet_id,
        operation_id=session._consumer_id(recipe),apps=recipe['apps'])


def accepted_release(session,record):
    if record.get('manifest_hash')==digest(session.spec): return None
    journal=_journal(session);pointer=session.root/'approved-release.json'
    if not pointer.exists() and not pointer.is_symlink(): return None
    value=journal.read(pointer)
    if (not isinstance(value,dict) or set(value)!={'protocol','review_id'}
            or type(value['protocol']) is not int or value['protocol']!=1):
        raise AssemblyError('Invalid release approval reference')
    receipt=_receipt(session,value['review_id'])
    if receipt['target']!=session.spec: return None
    if (receipt['checkpoint_hash']!=digest(record) or digest(receipt['source'])!=record.get('manifest_hash')
            or record.get('phase')!='stopped'):
        raise AssemblyError('Release approval is stale or belongs to another cycle')
    return value['review_id'],receipt


async def release_generations(session,record,generations):
    approved=accepted_release(session,record)
    if approved is None: return generations
    review_id,receipt=approved
    await _retained(session,receipt)
    if receipt['mode']=='upgrade':
        # Observe the original copy intent again after a CLI/Desktop restart;
        # never turn an absent receipt into permission to adopt arbitrary data.
        prepared=await _copies(session).advance(owner=session.info.fleet_id,operation_id=_copy_id(review_id))
        if prepared['state']!='prepared': raise AssemblyError('Resume release preparation before starting this profile')
        return {**generations,**{name:0 for name in receipt['origins']}}
    return {**generations,**{name:origin['generation'] for name,origin in receipt['origins'].items()}}
