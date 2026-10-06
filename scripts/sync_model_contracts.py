"""Copy the shared Model Services contract into the separately shipped Hub.

Run with --check in cross-repository release validation. The checked copy keeps
Hub independent of the full Pantheon/Agent Python distribution.
"""
import argparse
import hashlib
import json
from pathlib import Path


FILES = ('__init__.py', 'errors.py', 'deployments.py', 'idle.py', 'operation_stop.py')


def sync(hub, *, check=False):
    source = Path(__file__).resolve().parents[1] / 'pantheon/model_contracts'
    hub = Path(hub).resolve()
    if not (hub / 'pantheon_hub/api/model_services.py').is_file():
        raise ValueError('Supply a Pantheon Hub source checkout')
    target = hub / 'pantheon_hub/model_contracts'
    if target.is_symlink():
        raise ValueError('Contract destination must not be a symlink')
    different = []
    for name in FILES:
        content, path = (source / name).read_bytes(), target / name
        if path.is_symlink():
            raise ValueError('Contract files must not be symlinks')
        if not path.exists() or path.read_bytes() != content:
            different.append(name)
            if not check:
                target.mkdir(exist_ok=True)
                path.write_bytes(content)
    manifest = (json.dumps({'protocol': 1, 'files': {
        name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in FILES
    }}, sort_keys=True, indent=2) + '\n').encode()
    stamp = target / 'source.json'
    if stamp.is_symlink():
        raise ValueError('Contract manifest must not be a symlink')
    if not stamp.exists() or stamp.read_bytes() != manifest:
        different.append('source.json')
        if not check:
            target.mkdir(exist_ok=True)
            stamp.write_bytes(manifest)
    if check and different:
        raise ValueError('Hub Model Services contracts differ: ' + ', '.join(different))
    return different


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hub', required=True, type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    sync(args.hub, check=args.check)
