"""Switch a stopped single-machine profile to another product release.

A review compares the setup composed against the current and the target
product. Approving saves the launch with the target bundle and records the
switch, so it can be rolled back. The controller applies the release at the
next start: an upgraded App starts from a copy of its previous revision's
data; a rollback returns to the earlier revision's retained instance and data
(``os/internal/reconciler``). Nothing starts or stops here.
"""
import argparse
import json
from pathlib import Path

from pantheonos.apps.dependency_assembly import AssemblyError, _matches
from .launch import LaunchJournal, PrivateJournal, private_json, read_launch, validate_launch
from .update import changed_paths, digest

HISTORY = 'launch-releases.json'
MAX_HISTORY = 1024


def _history(launch):
    path = Path(launch['profile']) / HISTORY
    if not path.exists() and not path.is_symlink():
        return []
    value = PrivateJournal(path.parent).read(path)
    if not isinstance(value, list) or len(value) > MAX_HISTORY or any(
            not isinstance(v, dict) or set(v) != {'review_id', 'source_bundle', 'target_bundle'}
            or not _matches(r'[a-f0-9]{64}', v['review_id']) for v in value):
        raise AssemblyError('Invalid local release history')
    return value


def state(path):
    """Releases this launch can roll back (switches that led to its bundle)."""
    launch = read_launch(path)
    return {'pending': [], 'history': [v for v in _history(launch) if v['target_bundle'] == launch['bundle']]}


def _compose(bundle, setup):
    from pantheonos.apps.local_agent import compose_profile, read_bundle
    _, entries = read_bundle(bundle)
    return compose_profile(entries, private_json(setup))


def review(path, *, target_bundle=None, rollback_of=None):
    path = Path(path)
    source = read_launch(path)
    mode = 'upgrade'
    if rollback_of is not None:
        entry = next((v for v in state(path)['history'] if v['review_id'] == rollback_of), None)
        if entry is None:
            raise AssemblyError('Select a recorded release switch of this launch to roll back')
        target_bundle, mode = entry['source_bundle'], 'rollback'
    target_bundle = str(Path(target_bundle).expanduser().absolute())
    if target_bundle == source['bundle']:
        raise AssemblyError('Choose a different product bundle')
    target = validate_launch({**source, 'bundle': target_bundle})
    before, after = _compose(source['bundle'], source['setup']), _compose(target['bundle'], target['setup'])
    changes = changed_paths(before, after)
    if not changes:
        raise AssemblyError('The selected release composes the same Apps')
    decision = {'protocol': 2, 'path': str(path), 'source': source, 'target': target, 'rollback_of': rollback_of}
    return {'review_id': digest(decision), 'release_review_id': digest([before, after]),
            'source_hash': digest(before), 'target_hash': digest(after), 'changes': changes,
            'mode': mode, 'data_policy': 'copy-source-data' if mode == 'upgrade' else 'retained-source-data',
            'candidate_writes': 'retained-separately',
            'source_bundle': source['bundle'], 'target_bundle': target_bundle}, target


def approve(path, review_id, *, target_bundle=None, rollback_of=None):
    path = Path(path)
    result, target = review(path, target_bundle=target_bundle, rollback_of=rollback_of)
    if not _matches(r'[a-f0-9]{64}', review_id or '') or result['review_id'] != review_id:
        raise AssemblyError('The launch or release changed since review; review it again')
    source = read_launch(path)
    history = _history(source)
    if rollback_of is None:
        history = (history + [{'review_id': review_id, 'source_bundle': source['bundle'],
                                'target_bundle': target['bundle']}])[-MAX_HISTORY:]
    else:
        history = [v for v in history if v['review_id'] != rollback_of]
    profile = Path(source['profile'])
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    PrivateJournal(profile)._write(profile / HISTORY, history)
    LaunchJournal(path.parent)._write(path, target)
    return {'state': 'approved', 'review_id': review_id, 'release_review_id': result['release_review_id'],
            'target_hash': result['target_hash'], 'data_policy': result['data_policy'],
            'candidate_writes': result['candidate_writes'], 'launch': target}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Review or adopt another product release for a stopped launch.')
    parser.add_argument('--launch', required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--status', action='store_true')
    choice.add_argument('--target-bundle')
    choice.add_argument('--rollback-of')
    parser.add_argument('--approve')
    args = parser.parse_args(argv)
    try:
        if args.status:
            if args.approve:
                parser.error('--approve needs --target-bundle or --rollback-of')
            result = state(args.launch)
        elif args.approve:
            result = approve(args.launch, args.approve, target_bundle=args.target_bundle, rollback_of=args.rollback_of)
        else:
            result, _ = review(args.launch, target_bundle=args.target_bundle, rollback_of=args.rollback_of)
    except AssemblyError as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
