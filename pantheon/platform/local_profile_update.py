"""Reviewed configuration changes for stopped local App profiles.

No process or credential authority lives here. The LocalFleet owner lock, original
stop receipts and ordinary restart planner remain authoritative. Package/topology
updates and model publication changes require the separate release update path.
"""
import json

from pantheon.apps.dependency_assembly import AssemblyError, DEPLOYMENT_BYTES, _matches
from pantheon.apps.owner_journal import OwnerJournal
from pantheon.models.bootstrap import digest
from .app_preset import _unique_fields


def configuration_target(source, target):
    from .local_profile import manifest
    target = manifest(target)
    if source['packages'] != target['packages'] or source['model_apps'] != target['model_apps']:
        raise AssemblyError('Package or model publication changes require a release update')
    if source['apps'].keys() != target['apps'].keys() or any(
            (app['package'], app['scope']) != (target['apps'][name]['package'], target['apps'][name]['scope'])
            for name, app in source['apps'].items()):
        raise AssemblyError('Configuration updates must preserve App identities and topology')
    if source == target:
        raise AssemblyError('The selected configuration has no changes')
    return target


def changed_paths(before, after, path=''):
    """JSON pointers plus digests, never inline values/credential material."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            pointer = path + '/' + key.replace('~', '~0').replace('/', '~1')
            if key not in before or key not in after:
                result.append({'path': pointer, 'change': 'added' if key in after else 'removed'})
            else:
                result.extend(changed_paths(before[key], after[key], pointer))
        return result
    return [{'path': path, 'change': 'modified', 'before_hash': digest(before), 'after_hash': digest(after)}]


class UpdateJournal(OwnerJournal):
    error_type = AssemblyError
    maximum_bytes = 2 * DEPLOYMENT_BYTES + 4096

    def read(self, path):
        self._private(path)
        with path.open('rb') as stream:
            raw = stream.read(self.maximum_bytes + 1)
        if len(raw) > self.maximum_bytes:
            raise AssemblyError('Local configuration update exceeds its storage limit')
        try:
            return json.loads(raw, object_pairs_hook=_unique_fields)
        except (ValueError, TypeError, RecursionError):
            raise AssemblyError('Invalid local configuration update record') from None


def _proposal(session, target):
    target = configuration_target(session.spec, target)
    record = session._load()
    if (record['phase'] != 'stopped' or session.status()['state'] not in ('unopened', 'stopped')
            or record['manifest_hash'] != digest(session.spec)):
        raise AssemblyError('Stop the original profile cleanly before reviewing a configuration update')
    receipt = {'protocol': 1, 'checkpoint_hash': digest(record), 'source': session.spec, 'target': target}
    return record, receipt


def review_update(session, target):
    """Read only. Caller can inspect its private source/target inputs separately."""
    _, receipt = _proposal(session, target)
    return {'review_id': digest(receipt), 'source_hash': digest(receipt['source']),
            'target_hash': digest(receipt['target']),
            'changes': changed_paths(receipt['source'], receipt['target'])}


async def approve_update(session, target, review_id):
    """Authorize one exact candidate; do not start Apps or change old receipts."""
    from pantheon.utils.registry_lock import registry_lock
    with registry_lock(session.root/'configuration-update.lock', timeout=0):
        return await _approve_update(session, target, review_id)


async def _approve_update(session, target, review_id):
    record, receipt = _proposal(session, target)
    if not _matches(r'[a-f0-9]{64}', review_id) or digest(receipt) != review_id:
        raise AssemblyError('Configuration or profile changed since review; review it again')
    await session.directory.initialize()
    generations, stopped = await session._restart_state(record)
    state = await session.wire.status(session.info.node_id)
    if (state.get('owner') != session.info.fleet_id or state.get('node_id') != session.info.node_id
            or any(item.get('state') != 'stopped' or item.get('resources') or item.get('reservations')
                   for item in state['instances'].values())):
        raise AssemblyError('All profile Apps must be stopped before configuration approval')
    # Validate the fully rendered candidate using the same startup compiler.
    # This can snapshot the current bus credential, but issues no App grants.
    recipe = session._render(record['cycle'] + 1, generations, stopped, spec=receipt['target'])
    from pantheon.apps.deployment_preview import preview_deployment
    if recipe.get('model_apps'):
        await preview_deployment(session.wire, owner=session.info.fleet_id,
            operation_id=session.bootstrap.child_id(recipe, 'providers'),
            apps={name: item['app'] for name, item in recipe['model_apps'].items()})
    await preview_deployment(session.wire, owner=session.info.fleet_id,
        operation_id=session._consumer_id(recipe), apps=recipe['apps'])
    if session._load() != record:
        raise AssemblyError('Profile changed during configuration approval')
    directory = session.root/'configuration-updates'
    directory.mkdir(mode=0o700, exist_ok=True)
    journal = UpdateJournal(directory)
    journal._private(directory, directory=True)
    path = directory/(review_id + '.json')
    if path.exists() or path.is_symlink():
        if journal.read(path) != receipt:
            raise AssemblyError('Configuration update receipt changed')
    else:
        await journal._checkpoint(path, receipt)
    # Publish the reference last. Interrupted approval leaves either the old
    # decision or this complete receipt; never a partly rewritten profile.
    pointer = session.root/'approved-update.json'
    if pointer.exists() or pointer.is_symlink():
        journal._private(pointer)
    await journal._checkpoint(pointer, {'protocol': 1, 'review_id': review_id})
    session.spec = receipt['target']
    session._record = None
    session._staged = False
    return {'state': 'approved', 'review_id': review_id, 'target_hash': digest(session.spec)}


def accepts_update(session, record):
    """Called only when the caller's manifest differs from the saved cycle."""
    journal = UpdateJournal(session.root/'configuration-updates')
    pointer = session.root/'approved-update.json'
    if not pointer.exists() and not pointer.is_symlink():
        return False
    value = journal.read(pointer)
    if (not isinstance(value, dict) or set(value) != {'protocol', 'review_id'}
            or type(value['protocol']) is not int or value['protocol'] != 1
            or not _matches(r'[a-f0-9]{64}', value['review_id'])):
        raise AssemblyError('Invalid configuration approval reference')
    journal._private(journal.root, directory=True)
    receipt = journal.read(journal.root/(value['review_id'] + '.json'))
    if (not isinstance(receipt, dict) or set(receipt) != {'protocol', 'checkpoint_hash', 'source', 'target'}
            or type(receipt['protocol']) is not int or receipt['protocol'] != 1
            or digest(receipt) != value['review_id'] or receipt['checkpoint_hash'] != digest(record)
            or digest(receipt['source']) != record.get('manifest_hash')
            or receipt['target'] != session.spec or record.get('phase') != 'stopped'):
        raise AssemblyError('Configuration approval is stale or belongs to another candidate')
    from .local_profile import manifest
    configuration_target(manifest(receipt['source']), receipt['target'])
    return True


def main(argv=None):
    import argparse
    import asyncio
    from contextlib import AsyncExitStack
    import nats
    from pantheon.apps.local_agent import read_bundle, compose_profile
    from pantheon.apps.resolver import AppInstanceResolver
    from .local_fleet import LocalFleet
    from .local_profile import LocalAppProfile, private_json

    parser = argparse.ArgumentParser(description='Review or approve a stopped local product configuration update. '
        'Does not start Apps; after approval launch normally with the target setup. '
        'Keep both private setup files for review and a separately reviewed configuration reversal.')
    for name in ('profile', 'workspace', 'bundle', 'source-setup', 'target-setup'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--approve', help='Exact review_id returned by a previous review')
    args = parser.parse_args(argv)
    binaries, entries = read_bundle(args.bundle)
    source = compose_profile(entries, private_json(args.source_setup))
    target = compose_profile(entries, private_json(args.target_setup))

    async def run():
        async with LocalFleet(args.profile, binaries, workspace=args.workspace) as runtime, AsyncExitStack() as cleanup:
            info = runtime.coordinates
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                inbox_prefix=('_INBOX_'+info.fleet_id).encode())
            cleanup.push_async_callback(nc.close)
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id,
                                           str(runtime.workspace), connection=nc)
            cleanup.push_async_callback(resolver.close)
            session = LocalAppProfile(runtime, source, resolver)
            return (await approve_update(session, target, args.approve) if args.approve
                    else review_update(session, target))
    try:
        print(json.dumps(asyncio.run(run())), flush=True)
    except AssemblyError as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
