"""Adapt path-form Python Apps to the node lifecycle without copying an environment."""
from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path


def stream_backend(manifest: dict) -> bool:
    """Only the trusted, bundled Xpra drivers can use the stream adapter."""
    entry = manifest.get('entry', {})
    return (manifest.get('id') == 'browser' and entry.get('frontend') == 'ui:browser') or (
        manifest.get('id') == 'qupath' and entry.get('nativeDriver') ==
        'pantheon.apps.builtin.qupath.native:NativeAppManager')


def portable_backend(manifest: dict) -> bool:
    if stream_backend(manifest):
        return True
    entry = manifest.get('entry', {})
    backend = entry.get('fleetBackend') or entry.get('backend', '')
    return bool(backend and ':' not in backend and not entry.get('nativeDriver'))


def definition(manifest: dict, platform: str, workspace: str | None = None) -> dict:
    os_name, arch = platform.split('-', 1)
    if os_name not in ('linux', 'darwin', 'windows') or arch not in ('arm64', 'amd64'):
        raise ValueError(f'Unsupported node platform: {platform}')
    if stream_backend(manifest) and os_name != 'linux':
        raise ValueError('Xpra streaming requires a Linux node; native macOS/Windows capture is not available')
    # Environment paths are independent of the App code/artifact digest.
    python = ['python' if os_name == 'windows' else 'python3',
              '${PACKAGE}/.fleet-runtime/launch.py', '--install', '${INSTALL}']
    host = ['${PACKAGE}/.fleet-runtime/host.py']
    args = ['--package', '${PACKAGE}', '--data', '${DATA}']
    if workspace:
        args += ['--workspace', workspace]
    prepare = {'argv': ['python' if os_name == 'windows' else 'python3',
        '${PACKAGE}/.fleet-runtime/install.py', '--package', '${PACKAGE}', '--install', '${INSTALL}'],
        'timeout_seconds': 600}
    result = {
        'protocol': 1, 'app_id': manifest['id'], 'version': manifest['version'],
        'requires': {'os': [os_name], 'arch': [arch], 'caps': ['proc']},
        'components': [{'name': 'backend', 'runtime': 'process',
            'argv': [*python, *host, 'start', *args], 'ports': {'http': 0},
            'readiness': {'argv': [*python, *host, 'ready', *args], 'timeout_seconds': 120},
            'stop_seconds': 30}],
        'hooks': {
            'before_install': prepare,
            # Node-local caches can disappear when a cloud sandbox is replaced.
            # Prepare before spawning/readiness, preserving the durable App data.
            'before_start': prepare.copy(),
            'before_stop': {'argv': [*python, *host, 'drain', *args], 'timeout_seconds': 60},
        },
    }

    if stream_backend(manifest):
        result['components'][0]['ports']['stream'] = 0
        result['components'][0]['readiness']['timeout_seconds'] = 180
        result['components'][0]['env'] = {'BROWSER_XPRA_MODE': 'seamless'}
    return result


@contextmanager
def execution_package(directory: Path, platform: str, workspace: str | None = None):
    """Explicit fleet.json wins; legacy Python packages use the standard adapter.

    App code must declare pip dependencies in backend/requirements.txt (or
    requirements.txt). A source-node conda name is never a target-node path.
    """
    if (directory / 'fleet.json').is_file():
        yield directory
        return
    manifest_path = next((directory / n for n in ('app.json', 'atrium.json') if (directory / n).is_file()), None)
    if manifest_path is None:
        raise ValueError('App manifest is missing')
    manifest = json.loads(manifest_path.read_text())
    if not portable_backend(manifest):
        raise ValueError('This App needs a fleet.json execution package for the target node')
    # Refuse links rather than following them while copying an App artifact.
    def ignore(root, names):
        return [n for n in names if n in {'.git', '__pycache__', 'node_modules', '.venv'} or n.startswith('.env')]
    with tempfile.TemporaryDirectory(prefix='fleet-app-') as temp:
        root = Path(temp) / 'app'
        shutil.copytree(directory, root, symlinks=True, ignore=ignore)
        if any(p.is_symlink() for p in root.rglob('*')):
            raise ValueError('App artifacts cannot contain symbolic links')
        adapter = root / '.fleet-runtime'
        if adapter.exists():
            raise ValueError('.fleet-runtime is reserved for the Fleet adapter')
        adapter.mkdir()
        from pantheon.apps.builtin.desktop import app_runtime
        shutil.copyfile(app_runtime.__file__, adapter / 'app_runtime.py')
        shutil.copytree(Path(__file__).parent / 'portable_runtime' / 'assets', adapter / 'assets')
        for name in ('host.py', 'install.py', 'launch.py'):
            shutil.copyfile(Path(__file__).parent / 'portable_runtime' / name, adapter / name)
        if stream_backend(manifest):
            from pantheon.apps.builtin.desktop import browser, browser_snapshot, native_control, native_targets
            stream = root / '.stream-runtime'
            if stream.exists():
                raise ValueError('.stream-runtime is reserved for the Fleet adapter')
            stream.mkdir()
            shutil.copyfile(Path(__file__).parent / 'stream_runtime.py', stream / '__init__.py')
            shutil.copyfile(browser.__file__, stream / 'browser.py')
            shutil.copyfile(browser_snapshot.__file__, stream / 'browser_snapshot.py')
            shutil.copyfile(native_control.__file__, stream / 'native_control.py')
            shutil.copyfile(native_targets.__file__, stream / 'native_targets.py')
            if manifest['id'] == 'qupath':
                from pantheon.apps.builtin.qupath import native
                shutil.copytree(Path(native.__file__).parent, stream / 'qupath',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            manifest['entry']['backend'] = '.stream-runtime/__init__.py'
            (root / 'requirements.txt').write_text(
                'playwright>=1.58,<2\npython-xlib>=0.33,<1\npsutil>=6,<8\npillow>=10,<13\nloguru>=0.7,<1\n')
        if manifest.get('entry', {}).get('fleetBackend'):
            relative = manifest['entry']['fleetBackend']
            backend = (root / relative).resolve()
            if not backend.is_relative_to(root.resolve()) or not backend.is_file():
                raise ValueError('Fleet backend entry must be a file inside the App package')
            manifest['entry']['backend'] = relative
            # The node does not need the Agent framework or its model/provider
            # dependencies. Bundle the canonical ToolSet/context primitives,
            # without copying a controller environment or maintaining a fork.
            vendor = backend.parent / '_vendor' / 'pantheon'
            if vendor.exists():
                raise ValueError('Fleet ToolSet support directory is reserved')
            source = Path(__file__).parents[1]
            modules = ['toolset.py', 'utils/log.py', 'utils/misc.py',
                       'internal/package_runtime/context.py', 'remote/backend/base.py']
            for name in modules:
                target = vendor / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name, target)
            shutil.copytree(source / 'funcdesc', vendor / 'funcdesc', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            for name in ('', 'utils', 'internal', 'internal/package_runtime', 'remote', 'remote/backend'):
                (vendor / name / '__init__.py').write_text('')
        # Existing Workspace notebooks retain their original directory. Other
        # nodes own their own durable App workspace; never send a Mac a cloud path.
        notebook_workspace = workspace if manifest['id'] == 'integrated-notebook' else None
        (root / 'fleet.json').write_text(json.dumps(definition(manifest, platform, notebook_workspace)))
        # Dependency migration for the shipped pre-Fleet Spatial 3D package.
        # This profile replaces its source-only 'pantheon-base' conda name;
        # an App-authored requirements file always takes precedence.
        if manifest['id'] == 'spatial3d' and not any((root / p).exists() for p in ('backend/requirements.txt', 'requirements.txt')):
            (root / 'requirements.txt').write_text('anndata>=0.11,<0.13\nnumpy>=2,<3\nscipy>=1.14,<2\npandas>=2.2,<3\nzarr>=3,<4\n')
        # This describes only the generated immutable artifact, not the source repository.
        manifest['execution'] = {'protocol': 1, 'manifest': 'fleet.json'}
        (root / manifest_path.name).write_text(json.dumps(manifest))
        yield root
