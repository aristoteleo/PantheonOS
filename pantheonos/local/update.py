"""Reviewed configuration changes for a stopped single-machine profile.

A review compares the composed source and target setups (same product) and
lists the changed paths with digests, never values. Approving re-checks the
same review; the native owner then saves the launch with the target setup.
The next start saves the new deployment, and the controller restarts only the
Apps whose configuration changed. Nothing starts or stops here.
"""
import argparse
import hashlib
import json

from pantheonos.appkit.dependency_assembly import AssemblyError, _matches


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


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
    return [{'path': path or '/', 'change': 'modified', 'before_hash': digest(before), 'after_hash': digest(after)}]


def configuration_target(source, target):
    if source['packages'] != target['packages']:
        raise AssemblyError('A different product is a release change; review it as a release')
    if source['apps'].keys() != target['apps'].keys() or source['model_apps'].keys() != target['model_apps'].keys() or any(
            (app['package'], app['scope']) != (target['apps'][name]['package'], target['apps'][name]['scope'])
            for name, app in source['apps'].items()):
        raise AssemblyError('Configuration updates must preserve App identities and data scopes')
    if source == target:
        raise AssemblyError('The selected configuration has no changes')
    return target


def review(source, target):
    target = configuration_target(source, target)
    receipt = {'protocol': 2, 'source': source, 'target': target}
    return {'review_id': digest(receipt), 'source_hash': digest(source), 'target_hash': digest(target),
            'changes': changed_paths(source, target)}


def approve(source, target, review_id):
    if not _matches(r'[a-f0-9]{64}', review_id) or review(source, target)['review_id'] != review_id:
        raise AssemblyError('Configuration changed since review; review it again')
    return {'state': 'approved', 'review_id': review_id, 'target_hash': digest(target)}


def main(argv=None):
    from pantheonos.appkit.local_agent import compose_profile, read_bundle
    from .launch import private_json
    parser = argparse.ArgumentParser(description='Review or approve a stopped local configuration update. '
        'Does not start Apps; after approval launch normally with the target setup.')
    for name in ('profile', 'workspace', 'bundle', 'source-setup', 'target-setup'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--approve', help='Exact review_id returned by a previous review')
    args = parser.parse_args(argv)
    try:
        _, entries = read_bundle(args.bundle)
        source = compose_profile(entries, private_json(args.source_setup))
        target = compose_profile(entries, private_json(args.target_setup))
        result = approve(source, target, args.approve) if args.approve else review(source, target)
    except AssemblyError as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
