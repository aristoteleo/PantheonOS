"""Real offline environment reuse and conservative dependency invalidation."""
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pantheon.apps.portable_runtime import install


def app(tmp_path, name, requirements=''):
    package = tmp_path / 'packages' / name
    package.mkdir(parents=True)
    (package / 'app.json').write_text(json.dumps({'id': 'example', 'version': name}))
    (package / 'requirements.txt').write_text(requirements)
    target = tmp_path / 'installations' / name
    target.mkdir(parents=True)
    return package, target


def run_install(package, target):
    result = subprocess.run([sys.executable, install.__file__, '--package', str(package),
                             '--install', str(target)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['status'] == 'succeeded', (target / 'dependencies.log').read_text()
    return receipt, json.loads((target / 'python-environment.json').read_text())


def test_concurrent_code_revisions_reuse_environment_and_survive_uninstall(tmp_path):
    # No network: empty requirements. Exercise real process locking, venv paths,
    # launcher and imports, not just mocks of the cache's implementation.
    a, b = app(tmp_path, '1'), app(tmp_path, '2')
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(run_install, *a)
        second = pool.submit(run_install, *b)
        results = [first.result(), second.result()]
    assert results[0][1] == results[1][1]
    assert sum('Reused' in r[0]['message'] for r in results) == 1
    python = Path(results[0][1]['python'])
    # A venv's executable may symlink to system Python: binding must retain the
    # venv path, otherwise all dependency imports silently use system packages.
    assert 'python-environments' in str(python)
    shutil.rmtree(a[1])
    launch = Path(install.__file__).with_name('launch.py')
    check = subprocess.run([sys.executable, str(launch), '--install', str(b[1]),
                            '-c', 'import json,sys; print(json.dumps([sys.prefix,sys.base_prefix]))'],
                           check=True, capture_output=True, text=True, timeout=10)
    prefix, base = json.loads(check.stdout)
    assert prefix != base and 'python-environments' in prefix
    assert 'Reused' in run_install(*b)[0]['message']


def test_key_tracks_dependencies_python_and_app_but_not_ui_version(tmp_path, monkeypatch):
    a, b = app(tmp_path, '1', 'numpy>=2,<3\n'), app(tmp_path, '2', 'numpy>=2,<3\n')
    key = lambda pair: install.environment_key(*pair, pair[0] / 'requirements.txt')
    original = key(a)
    assert key(b) == original
    (b[0] / 'requirements.txt').write_text('numpy>=2.1,<3\n')
    assert key(b) != original
    (b[0] / 'requirements.txt').write_text('numpy>=2,<3\n')
    (b[0] / 'app.json').write_text('{"id":"another-app"}')
    assert key(b) != original
    monkeypatch.setattr(install.sys, 'version', 'different Python ABI')
    assert key(a) != original


@pytest.mark.parametrize('spec', ['-r backend/more.txt', '-e .', 'thing @ https://example.com/a.whl',
                                  './local-package', 'example-1.0-py3-none-any.whl', '--index-url https://example.com'])
def test_local_and_complex_requirements_are_isolated_per_artifact(tmp_path, spec):
    a, b = app(tmp_path, '1', spec), app(tmp_path, '2', spec)
    assert install.environment_key(*a, a[0] / 'requirements.txt') != install.environment_key(*b, b[0] / 'requirements.txt')


def test_failed_dependency_install_is_not_reused(tmp_path, monkeypatch):
    package, target = app(tmp_path, 'broken')
    key = install.environment_key(package, target, package / 'requirements.txt')
    root = tmp_path / 'python-environments' / key
    real_run = install.subprocess.run
    def fail_pip(args, **kwargs):
        if 'install' in args:
            raise subprocess.CalledProcessError(1, args)
        return real_run(args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(install.subprocess, 'run', fail_pip)
        with (target / 'dependencies.log').open('w') as log, pytest.raises(subprocess.CalledProcessError):
            install.prepare(package, target, log)
    assert not (root / '.fleet-ready.json').exists()
    assert not (target / 'python-environment.json').exists()
    assert 'installed and cached' in run_install(package, target)[0]['message']


def test_validation_ignores_source_runtime_packages(tmp_path, monkeypatch):
    # A controller may have an editable pantheon checkout on PYTHONPATH. It
    # must not make pip check demand the controller's deps inside the App venv.
    metadata = tmp_path / 'foreign' / 'foreign_runtime-1.0.dist-info'
    metadata.mkdir(parents=True)
    (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: foreign-runtime\nVersion: 1.0\nRequires-Dist: missing-library\n')
    monkeypatch.setenv('PYTHONPATH', str(metadata.parent))
    assert run_install(*app(tmp_path, 'isolated'))[0]['status'] == 'succeeded'


def test_cloud_mount_detection_respects_nested_local_mounts(tmp_path, monkeypatch):
    cloud = tmp_path / 'cloud volume'
    local = cloud / 'local'
    escaped = str(cloud).replace(' ', r'\040')
    mounts = f'1 0 0:1 / / rw - overlay none rw\n2 1 0:2 / {escaped} rw - 9p none rw\n3 2 0:3 / {escaped}/local rw - ext4 none rw\n'
    read = Path.read_text
    monkeypatch.setattr(install.sys, 'platform', 'linux')
    monkeypatch.setattr(Path, 'read_text', lambda p, *a, **k: mounts if str(p) == '/proc/self/mountinfo' else read(p, *a, **k))
    assert install.remote_filesystem(cloud / 'dependencies')
    assert not install.remote_filesystem(local / 'dependencies')
    assert not install.remote_filesystem(tmp_path / 'unrelated')


def test_local_python_selected_when_standard_library_is_on_network_mount(tmp_path, monkeypatch):
    candidate = tmp_path / 'python3'
    candidate.touch()
    monkeypatch.setattr(install, 'remote_filesystem', lambda p: str(p) == sys.base_prefix)
    monkeypatch.setattr(install.os, 'get_exec_path', lambda: [str(tmp_path)])
    monkeypatch.setattr(install.subprocess, 'run', lambda *a, **k:
        subprocess.CompletedProcess(a, 0, json.dumps([[3, 12], '/node-local-python'])))
    assert install.local_interpreter() == str(candidate)
    candidate.unlink()
    with pytest.raises(RuntimeError, match='node-local Python'):
        install.local_interpreter()


def test_concurrent_starts_of_same_artifact_keep_valid_binding(tmp_path):
    pair = app(tmp_path, 'same-artifact')
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: run_install(*pair), range(2)))
    assert results[0][1] == results[1][1]


def test_rebuild_lost_local_cache_without_touching_app_data(tmp_path, monkeypatch):
    package, target = app(tmp_path, 'cloud')
    durable = tmp_path / 'data' / 'notebook.ipynb'
    durable.parent.mkdir()
    durable.write_text('saved notebook')
    cache = tmp_path / 'ephemeral'
    monkeypatch.setattr(install, 'dependency_cache', lambda _: cache)
    def prepare():
        with (target / 'dependencies.log').open('w') as log:
            return install.prepare(package, target, log)
    assert not prepare()
    binding = (target / 'python-environment.json').read_bytes()
    assert prepare()
    shutil.rmtree(cache)
    assert not prepare()
    assert (target / 'python-environment.json').read_bytes() == binding
    assert durable.read_text() == 'saved notebook'
    python = json.loads(binding)['python']
    subprocess.run([python, '-I', '-c', 'import sys; assert sys.prefix != sys.base_prefix'], check=True, timeout=10)
