"""Assemble an independent Agent release from owned code and the built GUI.

No editable install, source path, credentials, journals, engine or other App
implementation is included. All mutable configuration is prepared by the owner.
The existing CLI/Desktop installation is left intact during migration.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import struct
import tempfile

from pantheon.apps.portable import definition
from pantheon.apps.schema import parse_manifest
from pantheon.apps.lifecycle import build_artifact


# Explicit SDK/control clients; don't copy the platform host or App manager.
SDK_MODULES = '''__init__ catalog dependency_assembly dependency_binding_client
dependency_client host_lifecycle owner_journal proxy reflect registry runtime_config
schema toolset_backend'''.split()
CHAT_MODULES = '''__init__ app_data app_models application data_transition environment event_hooks
event_store export launch lifecycle native routed_memory runtime settings_document
skill_files special_agents thread token_stats view_services'''.split()
MODEL_MODULES = '''__init__ client dependency direct direct_session errors http_pool
idle jobs media routing'''.split()


def _transport_platform(path):
    with path.open('rb') as stream:
        header = stream.read(64)
    if len(header) >= 20 and header[:4] == b'\x7fELF' and header[4:6] == b'\x02\x01':
        arch = {62: 'amd64', 183: 'arm64'}.get(struct.unpack_from('<H', header, 18)[0])
        if arch:
            return 'linux-' + arch
    if len(header) >= 8 and header[:4] == b'\xcf\xfa\xed\xfe':
        arch = {0x1000007: 'amd64', 0x100000c: 'arm64'}.get(struct.unpack_from('<I', header, 4)[0])
        if arch:
            return 'darwin-' + arch
    raise ValueError('Unsupported Fleet transport executable format')


def _copy_file(source, target):
    if source.is_symlink() or not source.is_file():
        raise ValueError(f'Release input must be a regular file: {source.name}')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _copy_tree(source, target):
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if any(p in {'.git', '__pycache__', '.venv', 'node_modules'} or p.startswith('.env') for p in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError('Release inputs cannot contain symlinks')
        if path.is_file():
            _copy_file(path, target / relative)


def build_package(destination, platform, *, version, frontend, transport,
                  credentials=('allocator', 'model_services', 'platform_budget', 'provider', 'files'),
                  dependencies=None):
    """Build into a new path only after every input has been copied successfully.

    Credential names are declarations, not keys. Custom releases may declare
    additional GUI/plugin grants, within the generic host's 16-field bound.
    """
    if platform not in {f'{os}-{arch}' for os in ('linux', 'darwin') for arch in ('amd64', 'arm64')}:
        raise ValueError('Agent durable owner locks currently require a POSIX node')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Supply an Agent release version')
    if (not 2 <= len(credentials) <= 16 or len(set(credentials)) != len(credentials)
            or not {'allocator', 'model_services'} <= set(credentials)
            or any(not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,63}', c) for c in credentials)):
        raise ValueError('Declare allocator, model_services and at most 14 other credential aliases')
    destination, frontend, transport = Path(destination).absolute(), Path(frontend), Path(transport)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if frontend.is_symlink() or not (frontend / 'index.js').is_file() or not (frontend / 'agent.css').is_file():
        raise ValueError('Supply the complete build:agent-app output')
    report = json.loads((frontend / 'build-report.json').read_text())
    if (report.get('desktopImplementation') is not False or not report.get('startupChunks')
            or report.get('version') != version):
        raise ValueError('Agent GUI boundary build report is missing or invalid')
    if transport.is_symlink() or not transport.is_file():
        raise ValueError('Supply the target-platform fleet-app-transport executable')
    if _transport_platform(transport) != platform:
        raise ValueError('Fleet transport executable does not match target platform')
    source = Path(__file__).parents[1]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.agent-release-') as staging:
        root = Path(staging) / 'release'
        root.mkdir()
        manifest = {
            'apiVersion': 2, 'id': 'agent', 'name': 'Pantheon Agent', 'version': version,
            'kind': 'service', 'surface': 'dom', 'runtime': 'process',
            'description': 'Versioned Agent conversations, execution and private configuration.',
            'entry': {'frontend': 'frontend/index.js', 'backend': 'backend/__init__.py'},
            'execution': {'protocol': 1, 'manifest': 'fleet.json'},
            'placement': {'requires': ['dom']},
            'dependencies': {
                'dependency-binding': {'range': '^0.1.1', 'uses': ['dependency-binding@1']},
                'model-services-control': {'range': '^0.1.0', 'uses': ['model-inference@1']},
            },
        }
        if dependencies is not None:
            if (not isinstance(dependencies, dict) or len(dependencies) > 64
                    or set(dependencies) & manifest['dependencies'].keys()):
                raise ValueError('Supply additional App dependencies without replacing startup services')
            manifest['dependencies'].update(json.loads(json.dumps(dependencies, allow_nan=False)))
        (root / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
        parse_manifest(manifest)
        _copy_tree(frontend, root / 'frontend')
        vendor = root / 'backend' / '_vendor' / 'pantheon'
        # Shared Agent execution modules remain one source, not maintained forks.
        for name in ('agent.py', 'background.py', 'settings.py', 'toolset.py', 'constant.py', 'dependency_provider.py', 'providers.py'):
            _copy_file(source / name, vendor / name)
        for directory in ('factory', 'funcdesc', 'internal', 'team', 'utils', 'data', 'skills'):
            _copy_tree(source / directory, vendor / directory)
        # Avoid package-root optional web-server imports in the independent build.
        (vendor / '__init__.py').write_text(f'"""Pantheon Agent release runtime."""\n__version__ = {version!r}\n')
        for directory, modules in (('apps', SDK_MODULES), ('chatroom', CHAT_MODULES), ('models', MODEL_MODULES)):
            for name in modules:
                _copy_file(source / directory / (name + '.py'), vendor / directory / (name + '.py'))
        # The task state machine is Agent-owned. Other builtin Apps are remote
        # dependencies and never copied into this distribution.
        builtin = vendor / 'apps' / 'builtin'
        builtin.mkdir()
        (builtin / '__init__.py').write_text('"""Agent-owned task state only; no ambient App lookup."""\n')
        _copy_tree(source.parent / 'apps' / 'task', builtin / 'task')
        for name in ('__init__.py', 'registry_lock.py'):
            _copy_file(source / 'platform' / name, vendor / 'platform' / name)
        executable = vendor / 'models' / 'fleet-app-transport'
        _copy_file(transport, executable)
        executable.chmod(0o755)
        (root / 'backend' / '__init__.py').write_text(
            'import sys\nif sys.version_info < (3, 11):\n'
            '    raise RuntimeError("Pantheon Agent needs Python 3.11 or newer")\n'
            'from pantheon.chatroom.native import register\n')
        _copy_file(Path(__file__).with_name('package-requirements.lock'), root / 'requirements.txt')
        adapter = root / '.fleet-runtime'
        for name in ('host.py', 'install.py', 'launch.py'):
            _copy_file(source / 'apps' / 'portable_runtime' / name, adapter / name)
        _copy_file(source.parent / 'apps' / 'desktop' / 'app_runtime.py', adapter / 'app_runtime.py')
        execution = definition(manifest, platform)
        execution['components'][0]['configuration'] = {
            'values': {'agent': {'required': True}},
            'credentials': {name: {'required': name in {'allocator', 'model_services'}} for name in credentials},
        }
        execution['hooks']['before_stop']['component'] = 'backend'
        (root / 'fleet.json').write_text(json.dumps(execution, indent=2) + '\n')
        # Content inventory binds frontend, backend, host and direct helper in
        # one immutable release; no source-machine paths or timestamps are saved.
        inventory = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(root.rglob('*')) if p.is_file()}
        (root / 'release.json').write_text(json.dumps({'protocol': 1, 'platform': platform,
            'version': version, 'files': inventory}, indent=2) + '\n')
        # Refuse a locally runnable but non-deliverable release. This is the
        # ordinary App encoder, including its wire and unpacked size limits.
        build_artifact(root)
        root.rename(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--frontend', required=True, type=Path)
    parser.add_argument('--transport', required=True, type=Path)
    parser.add_argument('--credential', action='append', default=None)
    parser.add_argument('--dependencies', type=Path, help='JSON map of additional ordinary App dependency declarations')
    args = parser.parse_args()
    options = {'credentials': tuple(args.credential)} if args.credential is not None else {}
    if args.dependencies:
        options['dependencies'] = json.loads(args.dependencies.read_text())
    build_package(args.output, args.platform, version=args.version, frontend=args.frontend,
                  transport=args.transport, **options)


if __name__ == '__main__':
    main()
