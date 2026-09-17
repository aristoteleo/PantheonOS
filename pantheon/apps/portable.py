"""Adapt path-form Python Apps to the node lifecycle without copying an environment."""
from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path


def portable_backend(manifest: dict) -> bool:
    entry = manifest.get('entry', {})
    backend = entry.get('backend', '')
    return bool(backend and ':' not in backend and not entry.get('nativeDriver'))


def definition(manifest: dict, platform: str) -> dict:
    os_name, arch = platform.split('-', 1)
    if os_name not in ('linux', 'darwin', 'windows') or arch not in ('arm64', 'amd64'):
        raise ValueError(f'Unsupported node platform: {platform}')
    python = '${INSTALL}/venv/Scripts/python.exe' if os_name == 'windows' else '${INSTALL}/venv/bin/python'
    host = ['${PACKAGE}/.fleet-runtime/host.py']
    args = ['--package', '${PACKAGE}', '--data', '${DATA}']
    return {
        'protocol': 1, 'app_id': manifest['id'], 'version': manifest['version'],
        'requires': {'os': [os_name], 'arch': [arch], 'caps': ['proc']},
        'components': [{'name': 'backend', 'runtime': 'process',
            'argv': [python, *host, 'start', *args], 'ports': {'http': 0},
            'readiness': {'argv': [python, *host, 'ready', *args], 'timeout_seconds': 120},
            'stop_seconds': 30}],
        'hooks': {
            'before_install': {'argv': ['python' if os_name == 'windows' else 'python3',
                '${PACKAGE}/.fleet-runtime/install.py', '--package', '${PACKAGE}', '--install', '${INSTALL}'],
                'timeout_seconds': 600},
            'before_stop': {'argv': [python, *host, 'drain', *args], 'timeout_seconds': 60},
        },
    }


@contextmanager
def execution_package(directory: Path, platform: str):
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
        for name in ('host.py', 'install.py'):
            shutil.copyfile(Path(__file__).parent / 'portable_runtime' / name, adapter / name)
        (root / 'fleet.json').write_text(json.dumps(definition(manifest, platform)))
        # Dependency migration for the shipped pre-Fleet Spatial 3D package.
        # This profile replaces its source-only 'pantheon-base' conda name;
        # an App-authored requirements file always takes precedence.
        if manifest['id'] == 'spatial3d' and not any((root / p).exists() for p in ('backend/requirements.txt', 'requirements.txt')):
            (root / 'requirements.txt').write_text('anndata>=0.11,<0.13\nnumpy>=2,<3\nscipy>=1.14,<2\npandas>=2.2,<3\nzarr>=3,<4\n')
        # This describes only the generated immutable artifact, not the source repository.
        manifest['execution'] = {'protocol': 1, 'manifest': 'fleet.json'}
        (root / manifest_path.name).write_text(json.dumps(manifest))
        yield root
