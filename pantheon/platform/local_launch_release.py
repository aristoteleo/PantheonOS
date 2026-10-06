"""Review an ordinary local App release and durably adopt its launch description.

The LocalFleet lock covers node preparation and the final launch replacement.
Receipts precede mutation. Retrying an acknowledged or interrupted replacement
uses the original decision; it never invents a new copy or a new source cycle.
"""
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path

from pantheon.apps.dependency_assembly import AssemblyError, _matches
from pantheon.apps.local_agent import read_bundle, compose_profile
from pantheon.models.bootstrap import digest
from .local_launch import LaunchJournal, read_launch, validate_launch
from .local_profile import LocalAppProfile, private_json
from .local_profile_release import ReleaseJournal, review_release, approve_release


def _product(launch):
    binaries, entries = read_bundle(launch['bundle'])
    return binaries, compose_profile(entries, private_json(launch['setup']))


def _journal(launch):
    return ReleaseJournal(Path(launch['profile'])/'app-profile'/'launch-releases')


def _read(journal, review_id):
    if not _matches(r'[a-f0-9]{64}', review_id):
        raise AssemblyError('Supply the exact launch release review ID')
    journal._private(journal.root, directory=True)
    value = journal.read(journal.root/(review_id+'.json'))
    if (not isinstance(value, dict) or set(value) != {'protocol', 'path', 'source', 'target', 'release', 'rollback_of'}
            or type(value['protocol']) is not int or value['protocol'] != 1 or digest(value) != review_id
            or not isinstance(value['path'], str) or not Path(value['path']).is_absolute()
            or value['rollback_of'] is not None and not _matches(r'[a-f0-9]{64}', value['rollback_of'])):
        raise AssemblyError('Invalid saved launch release decision')
    source, target = validate_launch(value['source']), validate_launch(value['target'])
    if {k:v for k,v in source.items() if k != 'bundle'} != {k:v for k,v in target.items() if k != 'bundle'}:
        raise AssemblyError('A release must preserve the owner launch choices')
    return value


async def operate(path, *, target_bundle=None, rollback_of=None, approval=None):
    """No Apps start here. Pending node operations stay in this owner's lifetime."""
    import nats
    from pantheon.apps.resolver import AppInstanceResolver
    from .local_fleet import LocalFleet

    path = Path(path)
    current = read_launch(path)
    if approval is not None and not _matches(r'[a-f0-9]{64}', approval):
        raise AssemblyError('Supply the exact launch release review ID')
    if (target_bundle is None) == (rollback_of is None):
        raise AssemblyError('Choose a target bundle or a retained release to roll back')
    journal = _journal(current)
    saved = None
    if approval and (journal.root/(approval+'.json')).exists():
        saved = _read(journal, approval)
        if saved['path'] != str(path) or current not in (saved['source'], saved['target']):
            raise AssemblyError('Launch selection changed since this release was reviewed')
    source = saved['source'] if saved else current
    previous = _read(journal, rollback_of) if rollback_of else None
    if previous:
        if previous['rollback_of'] is not None or previous['target'] != source:
            raise AssemblyError('Rollback must restore the retained source of this launch release')
        target = previous['source']
        reverse_id = previous['release']['review_id']
    else:
        if not isinstance(target_bundle, str) or not Path(target_bundle).is_absolute():
            raise AssemblyError('Choose an absolute target bundle path')
        target = {**source, 'bundle': target_bundle}
        reverse_id = None
    if saved and (saved['target'] != target or saved['rollback_of'] != rollback_of):
        raise AssemblyError('Requested release differs from the saved decision')
    _, source_spec = _product(source)
    binaries, target_spec = _product(target)
    # Use the selected distribution's verified executables. An incompatible
    # Runner must reject the existing ledger; never downgrade its schema fence.
    async with LocalFleet(source['profile'], binaries, workspace=source['workspace']) as runtime, AsyncExitStack() as cleanup:
        info = runtime.coordinates
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                                inbox_prefix=('_INBOX_'+info.fleet_id).encode())
        cleanup.push_async_callback(nc.close)
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id,
                                      str(runtime.workspace), connection=nc)
        cleanup.push_async_callback(resolver.close)
        session = LocalAppProfile(runtime, source_spec, resolver)
        release = review_release(session, target_spec, rollback_of=reverse_id)
        receipt = dict(protocol=1, path=str(path), source=source, target=target,
                       release=release, rollback_of=rollback_of)
        review_id = digest(receipt)
        if saved is not None and saved != receipt:
            raise AssemblyError('Product or profile changed since the saved decision')
        if approval is None:
            return {**release, 'release_review_id': release['review_id'], 'review_id': review_id,
                    'source_bundle': source['bundle'], 'target_bundle': target['bundle']}
        if not _matches(r'[a-f0-9]{64}', approval) or approval != review_id:
            raise AssemblyError('Product, launch or profile changed; review this release again')
        if read_launch(path) != current:
            raise AssemblyError('Launch selection changed during review')
        journal.root.mkdir(mode=0o700, exist_ok=True)
        journal._private(journal.root, directory=True)
        if saved is None:
            await journal._checkpoint(journal.root/(review_id+'.json'), receipt)
        while True:
            result = await approve_release(session, target_spec, release['review_id'], rollback_of=reverse_id)
            if result['state'] == 'approved':
                break
            if result['state'] != 'pending':
                raise AssemblyError('Inspect the original release operation before continuing')
            await asyncio.sleep(.2)
        # All expensive work has finished. A lost acknowledgement here is
        # recoverable from the immutable receipt, whether rename happened or not.
        if read_launch(path) != current:
            raise AssemblyError('Release prepared, but launch selection changed; restore the reviewed choice before resuming')
        await LaunchJournal(path.parent)._checkpoint(path, target)
        return {**result, 'release_review_id': result['review_id'], 'review_id': review_id,
                'launch': target}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', required=True, help='Private saved launch JSON; replaced only after approval completes')
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--target-bundle', help='Verified new local product bundle')
    selection.add_argument('--rollback-of', help='Original approved launch release review ID')
    parser.add_argument('--approve', help='Exact review_id from a preceding review')
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(operate(args.launch, target_bundle=args.target_bundle,
                                   rollback_of=args.rollback_of, approval=args.approve))
        print(json.dumps(result), flush=True)
    except AssemblyError as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__':
    main()
