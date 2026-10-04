"""Deliver a set of ordinary immutable App artifacts to explicit Fleet nodes.

The index is a distribution aid, not another App version or lifecycle format.
It contains no owner configuration, grants or credentials. Installation/start
still use the existing AppDeployment journal after this byte-only staging step.
"""
import asyncio
import json
from pathlib import Path
import tempfile

from .dependency_assembly import AssemblyError, IDENT, NAME, _matches
from .lifecycle import build_artifact, MAX_ARTIFACT


PLATFORM = r'(linux|darwin|windows)-(amd64|arm64)'
INDEX = 'release-set.json'


def _declarations(path, platform):
    # build_artifact has already validated the platform manifest name and links.
    manifest = json.loads((path / 'app.json').read_text())
    variants = manifest.get('execution', {}).get('platform_manifests', {})
    definition = json.loads((path / (variants[platform] if variants else 'fleet.json')).read_text())
    return manifest, definition


def index_packages(root, packages):
    """Index built App directories, keyed by deployment alias and platform."""
    root = Path(root).resolve(strict=True)
    if not isinstance(packages, dict) or not 1 <= len(packages) <= 16:
        raise AssemblyError('Supply between one and sixteen App packages')
    entries = {}
    for alias, variants in packages.items():
        if not _matches(NAME, alias) or not isinstance(variants, dict) or not 1 <= len(variants) <= 6:
            raise AssemblyError('Supply named App packages with explicit platform variants')
        entries[alias] = {}
        identity = None
        for platform, path in variants.items():
            if not _matches(PLATFORM, platform):
                raise AssemblyError('Unsupported release platform')
            path = Path(path)
            if not path.is_absolute():
                path = root / path
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
                raise AssemblyError('Release packages must be directories within the release set')
            path = path.resolve(strict=True)
            payload, revision = build_artifact(path, platform)
            manifest, definition = _declarations(path, platform)
            os_name, arch = platform.split('-')
            requires = definition.get('requires', {})
            if requires.get('os') != [os_name] or requires.get('arch') != [arch]:
                raise AssemblyError('Index compiled App packages for one exact platform each')
            current = (manifest['id'], manifest['version'])
            if identity is not None and current != identity:
                raise AssemblyError('All platform variants must have the same App identity and version')
            identity = current
            entries[alias][platform] = dict(path=path.relative_to(root).as_posix(),
                app_id=current[0], version=current[1], revision=revision, bytes=len(payload))
    index = {'protocol': 1, 'apps': entries}
    with (root / INDEX).open('x') as stream:
        json.dump(index, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return index


def _read_index(root):
    path = root / INDEX
    if path.is_symlink() or path.stat().st_size > 65536:
        raise AssemblyError('Invalid release set index')
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or set(value) != {'protocol', 'apps'}
            or type(value['protocol']) is not int or value['protocol'] != 1
            or not isinstance(value['apps'], dict) or not 1 <= len(value['apps']) <= 16):
        raise AssemblyError('Invalid release set index')
    return value


def _prepare(root, placements, spool):
    index = _read_index(root)
    if not isinstance(placements, dict) or not 1 <= len(placements) <= 16:
        raise AssemblyError('Select explicit release aliases and target nodes')
    result = {}
    for alias, target in placements.items():
        if (not _matches(NAME, alias) or alias not in index['apps']
                or not isinstance(target, dict)
                or set(target) != {'node_id', 'platform', 'scope', 'generation'}
                or not _matches(IDENT, target['node_id']) or not _matches(NAME, target['scope'])
                or not _matches(PLATFORM, target['platform'])
                or type(target['generation']) is not int or not 0 <= target['generation'] < 2**63 - 2):
            raise AssemblyError('Supply explicit node, platform, scope and expected generation for every App')
        variants = index['apps'][alias]
        entry = variants.get(target['platform']) if isinstance(variants, dict) else None
        if (not isinstance(entry, dict) or set(entry) != {'path', 'app_id', 'version', 'revision', 'bytes'}
                or not _matches(r'[a-f0-9]{64}', entry['revision'])
                or type(entry['bytes']) is not int or not 0 < entry['bytes'] <= MAX_ARTIFACT
                or not isinstance(entry['path'], str)):
            raise AssemblyError('Selected App platform is missing or has invalid release metadata')
        relative = Path(entry['path'])
        if relative.is_absolute() or not relative.parts or any(p in {'.', '..'} for p in relative.parts):
            raise AssemblyError('Release package path must be relative to its distribution')
        path = root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise AssemblyError('Release package paths cannot contain symbolic links')
        # Read all selected sources before any network operation. A private spool
        # retains the verified bytes across awaits without keeping N releases in RAM.
        payload, revision = build_artifact(path, target['platform'])
        manifest, definition = _declarations(path, target['platform'])
        os_name, arch = target['platform'].split('-')
        if (revision != entry['revision'] or len(payload) != entry['bytes']
                or (manifest.get('id'), manifest.get('version')) != (entry['app_id'], entry['version'])
                or definition.get('requires', {}).get('os') != [os_name]
                or definition.get('requires', {}).get('arch') != [arch]):
            raise AssemblyError('Release contents changed; rebuild and review the release set')
        (spool / revision).write_bytes(payload)
        result[alias] = {**target, 'revision': revision}
    if len({(v['node_id'], v['revision'], v['scope']) for v in result.values()}) != len(result):
        raise AssemblyError('Each App target must have a distinct deployment identity')
    return result


async def stage_release_set(lifecycle, root, *, owner, placements):
    """Stage exact bytes only; safe to retry after partial delivery/lost replies.

    Returns the normal deployment target map. No install hook, grant, process,
    journal or startup preset is created. The selected owner/nodes/platforms must
    all match before the first upload; no default-node or architecture fallback.
    """
    if not _matches(IDENT, owner):
        raise AssemblyError('Supply the Fleet owner for this release delivery')
    root = Path(root)
    if root.is_symlink():
        raise AssemblyError('Use a real release set directory')
    root = root.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='pantheon-release-delivery-') as temporary:
        spool = Path(temporary)
        # Joining this worker on cancellation keeps its spool alive until it
        # finishes; it cannot publish remotely or outlive directory cleanup.
        worker = asyncio.create_task(asyncio.to_thread(_prepare, root, placements, spool))
        try:
            targets = await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                await worker
            except Exception:
                pass
            raise
        nodes = {}
        for target in targets.values():
            node = target['node_id']
            if node not in nodes:
                snapshot = await lifecycle.status(node)
                if (snapshot.get('owner') != owner or snapshot.get('node_id') != node
                        or snapshot.get('dependency_config_protocol') != 1):
                    raise AssemblyError('Selected node does not match this owner or prepared App protocol')
                nodes[node] = await lifecycle.target_platform(node)
            if nodes[node] != target['platform']:
                raise AssemblyError('Selected release platform does not match the target Fleet node')
        staged = set()
        for target in targets.values():
            identity = (target['node_id'], target['revision'])
            if identity not in staged:
                payload = (spool / target['revision']).read_bytes()
                # stage_exact verifies the same SHA again and skips installed
                # digests using a fresh node observation, rather than a local receipt.
                actual = await lifecycle.stage_exact(target['node_id'], payload, target['revision'])
                if actual != target['revision']:
                    raise AssemblyError('Node did not acknowledge the selected release')
                staged.add(identity)
        return {name: {k: v for k, v in target.items() if k != 'platform'}
                for name, target in targets.items()}


def main():
    import argparse
    import os
    from .lifecycle import FleetLifecycle
    from .resolver import AppInstanceResolver

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-set', required=True, type=Path)
    parser.add_argument('--placements', required=True, type=Path,
                        help='JSON map: release alias -> node_id, platform, scope, generation')
    parser.add_argument('--owner', required=True)
    parser.add_argument('--output', required=True, type=Path,
                        help='Write ordinary deployment targets to a new private JSON file')
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error('Output already exists; preserve the original delivery targets')
    placements = json.loads(args.placements.read_text())

    async def deliver():
        resolver = AppInstanceResolver.from_env()
        if resolver is None:
            raise AssemblyError('Connect the owner platform to Fleet before delivering releases')
        try:
            return await stage_release_set(FleetLifecycle(resolver), args.release_set,
                                           owner=args.owner, placements=placements)
        finally:
            await resolver.close()

    targets = asyncio.run(deliver())
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(targets, stream, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
