"""Build the paired Agent and its ordinary dependency Apps for distribution.

Every child directory is still an editable, standalone App package. The index
only pins delivery bytes across platforms; it is not a second version manager.
Existing configured tool/plugin requirements are preserved, not disabled to
make a minimal Agent start. Mutable settings and keys never belong here.
"""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from pantheon.apps.release_set import index_packages, PLATFORM
from pantheon.apps.dependency_assembly import NAME, _matches
from pantheon.apps.portable import execution_package
from pantheon.chatroom.package import build_package
from pantheon.platform.dependency_package import build_package as build_allocator
from pantheon.platform.model_dependency_package import build_package as build_models


def build_release_set(destination, *, version, frontend, transports, providers=None, **agent_options):
    """Build each selected native variant; never infer another node or engine.

    Optional providers are existing ordinary App source directories. Only the
    canonical portable packaging path is used. An already deployed Model Service
    need not be copied or restarted to make it available to this Agent.
    """
    destination = Path(destination).absolute()
    providers = {} if providers is None else dict(providers)
    if (not isinstance(transports, dict) or not 1 <= len(transports) <= 4
            or any(not _matches(PLATFORM, p) for p in transports)
            or len(providers) > 13 or set(providers) & {'agent', 'allocator', 'model-access'}
            or any(not _matches(NAME, name) for name in providers)):
        raise ValueError('Supply native transport variants and distinct ordinary provider Apps')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.agent-release-set-', dir=destination.parent) as temporary:
        root = Path(temporary) / 'release'
        root.mkdir()
        packages = {name: {} for name in ('agent', 'allocator', 'model-access', *providers)}
        for platform, transport in sorted(transports.items()):
            variant = root / platform
            variant.mkdir()
            build_package(variant / 'agent', platform, version=version, frontend=frontend,
                          transport=transport, **agent_options)
            build_allocator(variant / 'allocator', platform)
            build_models(variant / 'model-access', platform)
            for alias, source in providers.items():
                with execution_package(Path(source), platform) as package:
                    shutil.copytree(package, variant / alias, symlinks=True,
                                    ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc', '.venv', 'node_modules', '.env*'))
            for alias in packages:
                packages[alias][platform] = variant / alias
        index_packages(root, packages)
        root.rename(destination)
    return destination


def _pairs(values):
    result = {}
    for value in values:
        key, separator, path = value.partition('=')
        if not separator or not path or key in result:
            raise ValueError('Use unique name=path selections')
        result[key] = path
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--frontend', required=True, type=Path)
    parser.add_argument('--transport', action='append', required=True, metavar='PLATFORM=PATH')
    parser.add_argument('--provider', action='append', default=[], metavar='ALIAS=APP_DIRECTORY')
    parser.add_argument('--credential', action='append')
    parser.add_argument('--dependencies', type=Path, help='Additional ordinary App dependency declarations')
    args = parser.parse_args()
    options = {}
    if args.credential is not None:
        options['credentials'] = tuple(args.credential)
    if args.dependencies:
        options['dependencies'] = json.loads(args.dependencies.read_text())
    build_release_set(args.output, version=args.version, frontend=args.frontend,
                      transports=_pairs(args.transport), providers=_pairs(args.provider), **options)


if __name__ == '__main__':
    main()
