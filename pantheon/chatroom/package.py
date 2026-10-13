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
import tempfile

from pantheon.apps.portable import definition
from pantheon.apps.schema import parse_manifest
from pantheon.apps.lifecycle import build_artifact


# Explicit SDK/control clients; don't copy the platform host or App manager.
SDK_MODULES = '''__init__ agent_defaults agent_execution_client catalog dependency_assembly dependency_binding_client
dependency_client host_lifecycle owner_journal proxy reflect registry runtime_config
schema toolset_backend'''.split()
CHAT_MODULES = '''__init__ app_data app_models application data_transition data_format environment event_hooks
event_store execution_engine execution_service export launch lifecycle native routed_memory runtime settings_document
skill_files special_agents thread token_stats view_services'''.split()
MODEL_MODULES = '''__init__ client dependency direct direct_session errors http_pool
idle jobs management_tools media messages routing'''.split()


from pantheon.models.package import transport_platform as _transport_platform


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


SOURCE_BUILD = {'argv': ['python3', 'build.py'], 'timeout_seconds': 1800}
# The Agent window's Vite project inside the UI repository.
FRONTEND_FILES = ('package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'vite.agentapp.config.ts',
                  'env.d.ts', 'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json', 'tsconfig.vitest.json', 'src')
# The package manager the window's lockfile belongs to.
FRONTEND_TOOLS = {'pnpm': '10.32.1'}
README = """# Pantheon Agent

The Agent's source: its Python runtime (`backend/pantheon`), prompts, agent
and team templates and skills (`backend/pantheon/factory/templates`), and its
window (`frontend/`, a Vite project built with pnpm).

`build.py` assembles the runnable package. Two inputs are prebuilt and pinned
in `build.json`: the Model Services transport and the compiled window. Editing
Python, prompts or skills needs no Node build; editing `frontend/` builds the
window with pnpm (Node.js 20).

Run a version as your Agent from the Store: it is built on your workspace,
installed next to your Agent and started with your Agent's data. Returning to
the official release is one action.
"""


def _check(version, credentials):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Supply an Agent release version')
    if (not 2 <= len(credentials) <= 16 or len(set(credentials)) != len(credentials)
            or not {'allocator', 'model_services'} <= set(credentials)
            or any(not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,63}', c) for c in credentials)):
        raise ValueError('Declare allocator, model_services and at most 14 other credential aliases')


def source_manifest(version, dependencies=None):
    """The Agent's app.json in its source repository (no execution; a build)."""
    manifest = {
        'apiVersion': 2, 'id': 'agent', 'name': 'Pantheon Agent', 'version': version,
        'kind': 'service', 'surface': 'dom', 'runtime': 'process',
        'description': 'Versioned Agent conversations, execution and private configuration.',
        'entry': {'frontend': 'frontend/index.js', 'backend': 'backend/__init__.py'},
        'placement': {'requires': ['dom']},
        'persistState': ['chatId'],
        'dataSchema': {'id': 'pantheon-agent', 'version': 1, 'accepts': [1]},
        'dependencies': {
            'dependency-binding': {'range': '^0.1.1', 'uses': ['dependency-binding@1']},
            'model-services-control': {'range': '^0.1.0', 'uses': ['model-inference@1']},
        },
    }
    from pantheon.apps.agent_execution_client import METHODS
    from pantheon.chatroom.data_transition import INITIALIZATION_CAPABILITY
    manifest['caps'] = {'agentDataInitialization': dict(INITIALIZATION_CAPABILITY),
                        'agentMCPMigration': {'protocols': [1, 2]},
                        'agentOAuthMigration': {'protocols': [1]}}
    from pantheon.apps.reflect import reflect_toolset_class
    from pantheon.chatroom.native import NativeAgentApplication
    manifest['provides'] = {
        'tools': [signature.model_dump(exclude_none=True)
                  for signature in reflect_toolset_class(NativeAgentApplication) if signature.name in METHODS],
        'interfaces': [{'name': 'agent-execution', 'version': 1, 'tools': list(METHODS)}],
    }
    if dependencies is not None:
        if (not isinstance(dependencies, dict) or len(dependencies) > 64
                or set(dependencies) & manifest['dependencies'].keys()):
            raise ValueError('Supply additional App dependencies without replacing startup services')
        manifest['dependencies'].update(json.loads(json.dumps(dependencies, allow_nan=False)))
    manifest['build'] = dict(SOURCE_BUILD)
    parse_manifest(manifest)
    return manifest


def export_frontend(ui_root, destination, env):
    """The Agent window's source from the UI repository's committed tree.

    env holds the build's public configuration (VITE_*), written to
    .env.production so a rebuild from this source matches the release.
    """
    import subprocess
    ui_root, destination = Path(ui_root), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    if any(not re.fullmatch(r'VITE_[A-Z0-9_]+', k) or '\n' in str(v) for k, v in env.items()):
        raise ValueError('Frontend build configuration is VITE_* names and single-line values')
    destination.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / 'frontend.tar'
        subprocess.run(['git', '-C', str(ui_root), 'archive', '--format=tar', f'--output={archive}',
                        'HEAD', *FRONTEND_FILES], check=True)
        import tarfile
        with tarfile.open(archive) as tree:
            tree.extractall(destination, filter='data')
    (destination / '.env.production').write_text(''.join(f'{k}={v}\n' for k, v in sorted(env.items())))
    return destination


def export_source(destination, *, version, inputs, frontend_source=None,
                  credentials=('allocator', 'model_services', 'platform_budget', 'provider', 'files'),
                  dependencies=None):
    """Write the Agent's source repository tree (docs/agent-app-repository.md §2).

    inputs pins the prebuilt transport ({platform: {url, sha256}}) and window
    ({url, sha256}); the window's source digest is recorded from
    frontend_source, which must be the source that window was built from.
    """
    from . import agent_build
    _check(version, credentials)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    source = Path(__file__).parents[1]
    root = destination
    root.mkdir(parents=True)
    manifest = source_manifest(version, dependencies)
    (root / 'app.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (root / 'README.md').write_text(README)
    _copy_file(Path(agent_build.__file__), root / 'build.py')
    vendor = root / 'backend' / 'pantheon'
    # Shared Agent execution modules remain one source, not maintained forks.
    for name in ('agent.py', 'agent_core_tools.py', 'agent_nodes.py', 'agent_loop.py', 'agent_tools.py', 'agent_model.py', 'agent_context.py', 'background.py', 'settings.py', 'toolset.py', 'constant.py', 'dependency_provider.py', 'providers.py'):
        _copy_file(source / name, vendor / name)
    for directory in ('factory', 'funcdesc', 'internal', 'team', 'utils', 'data', 'skills', 'repl'):
        _copy_tree(source / directory, vendor / directory)
    for directory, modules in (('apps', SDK_MODULES), ('chatroom', CHAT_MODULES), ('models', MODEL_MODULES)):
        for name in modules:
            _copy_file(source / directory / (name + '.py'), vendor / directory / (name + '.py'))
    # The task state machine is Agent-owned. Other builtin Apps are remote
    # dependencies and never copied into this distribution.
    builtin = vendor / 'apps' / 'builtin'
    builtin.mkdir()
    (builtin / '__init__.py').write_text('"""Agent-owned task state only; no ambient App lookup."""\n')
    _copy_tree(source.parent / 'apps' / 'task', builtin / 'task')
    # Store content (skills, agents, teams) installs into this App's data.
    for name in ('local.py', 'installer.py'):
        _copy_file(source / 'store' / name, vendor / 'store' / name)
    (vendor / 'store' / '__init__.py').write_text('"""Store content installation for this Agent."""\n')
    (root / 'backend' / '__init__.py').write_text(
        'import sys\nif sys.version_info < (3, 11):\n'
        '    raise RuntimeError("Pantheon Agent needs Python 3.11 or newer")\n'
        'from pantheon.chatroom.native import register\n')
    (root / 'cli.py').write_text(
        '"""CLI for this exact Agent release and its prepared dependencies."""\n'
        'from pathlib import Path\nimport os, runpy, sys\n'
        'if not os.environ.get("PANTHEON_APP_CONFIG") or not any(\n'
        '    arg.split("=", 1)[0] in ("--app-data", "--app_data") for arg in sys.argv[1:]):\n'
        '    raise SystemExit("This release CLI requires prepared App configuration and --app-data")\n'
        'sys.path.insert(0, str(Path(__file__).resolve().parent / "backend" / "_vendor"))\n'
        'runpy.run_module("pantheon.repl", run_name="__main__")\n')
    _copy_file(Path(__file__).with_name('package-requirements.lock'), root / 'requirements.txt')
    adapter = root / 'fleet-runtime'
    for name in ('host.py', 'install.py', 'launch.py'):
        _copy_file(source / 'apps' / 'portable_runtime' / name, adapter / name)
    _copy_tree(source / 'apps' / 'portable_runtime' / 'assets', adapter / 'assets')
    _copy_file(source.parent / 'apps' / 'desktop' / 'app_runtime.py', adapter / 'app_runtime.py')
    execution = definition({**manifest, 'execution': {}}, 'linux-amd64')
    execution['components'][0]['configuration'] = {
        'values': {'agent': {'required': True}},
        'credentials': {name: {'required': name in {'allocator', 'model_services'}} for name in credentials},
    }
    execution['hooks']['before_stop']['component'] = 'backend'
    (root / 'execution.json').write_text(json.dumps(execution, indent=2) + '\n')
    frontend = dict(inputs['frontend'])
    if frontend_source is not None:
        # Including .env.production: the window's public build configuration.
        agent_build.copy_tree(Path(frontend_source), root / 'frontend')
        frontend['source'] = agent_build.tree_digest(root / 'frontend')
    (root / 'build.json').write_text(json.dumps({'protocol': 1, 'credentials': list(credentials),
        'inputs': {'transport': inputs['transport'], 'frontend': frontend},
        'tools': dict(FRONTEND_TOOLS)}, indent=2) + '\n')
    return destination


def build_package(destination, platform, *, version, frontend, transport,
                  credentials=('allocator', 'model_services', 'platform_budget', 'provider', 'files'),
                  dependencies=None, frontend_source=None, urls=None, source_output=None):
    """Build into a new path only after every input has been copied successfully.

    The package is built from the Agent's exported source tree by its own
    build.py, as an owner's build of that repository would be. urls are where
    the pinned frontend and transport inputs are published; source_output
    keeps the exported source tree. Credential names are declarations, not
    keys. Custom releases may declare additional GUI/plugin grants, within the
    generic host's 16-field bound.
    """
    from . import agent_build
    if platform not in {f'{os}-{arch}' for os in ('linux', 'darwin') for arch in ('amd64', 'arm64')}:
        raise ValueError('Agent durable owner locks currently require a POSIX node')
    _check(version, credentials)
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
    urls = urls or {}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.agent-release-') as staging:
        staging = Path(staging)
        cache = staging / 'cache'
        (cache / 'inputs').mkdir(parents=True)
        window = agent_build.frontend_archive(frontend)
        window_sha = hashlib.sha256(window).hexdigest()
        (cache / 'inputs' / window_sha).write_bytes(window)
        transport_sha = agent_build.sha256_file(transport)
        _copy_file(transport, cache / 'inputs' / transport_sha)
        source = export_source(staging / 'source', version=version, credentials=credentials,
            dependencies=dependencies, frontend_source=frontend_source,
            inputs={'transport': {platform: {'url': urls.get('transport', ''), 'sha256': transport_sha}},
                    'frontend': {'url': urls.get('frontend', ''), 'sha256': window_sha}})
        root = staging / 'release'
        root.mkdir()
        agent_build.build(source, root, platform=platform, revision=report.get('revision', ''), cache=cache)
        parse_manifest(json.loads((root / 'app.json').read_text()))
        # Refuse a locally runnable but non-deliverable release. This is the
        # ordinary App encoder, including its wire and unpacked size limits.
        build_artifact(root)
        if source_output is not None:
            source.rename(Path(source_output))
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
