"""Real offline environment reuse and conservative dependency invalidation."""
import json
import os
import shutil
import subprocess
import sys
import io
import tarfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pantheonos.apps.portable_runtime import install


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
    monkeypatch.undo()
    # The same interpreter installed again (a rebuilt image) keeps the key;
    # another interpreter at that path does not.
    copy = tmp_path / 'python'
    shutil.copy2(sys.executable, copy)
    monkeypatch.setattr(install.sys, 'executable', str(copy))
    rebuilt = key(a)
    os.utime(copy, (1, 1))
    assert key(a) == rebuilt
    with copy.open('ab') as stream:
        stream.write(b'another build')
    assert key(a) != rebuilt


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


def test_compatible_wheel_preferred_over_newer_source_release(tmp_path):
    # Offline reproduction of older nodes lacking a wheel for the latest
    # scientific package: pip must select the compatible release instead.
    index = tmp_path / 'index'
    index.mkdir()
    with zipfile.ZipFile(index / 'fleet_fixture-1.0-py3-none-any.whl', 'w') as wheel:
        wheel.writestr('fleet_fixture.py', 'VERSION = "1.0"\n')
        wheel.writestr('fleet_fixture-1.0.dist-info/METADATA',
                       'Metadata-Version: 2.1\nName: fleet-fixture\nVersion: 1.0\n')
        wheel.writestr('fleet_fixture-1.0.dist-info/WHEEL',
                       'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        wheel.writestr('fleet_fixture-1.0.dist-info/RECORD', '')
    with tarfile.open(index / 'fleet_fixture-2.0.tar.gz', 'w:gz') as source:
        data = b'raise RuntimeError("No compatible compiler on this node")\n'
        member = tarfile.TarInfo('fleet_fixture-2.0/setup.py')
        member.size = len(data)
        source.addfile(member, io.BytesIO(data))
    _, binding = run_install(*app(tmp_path, 'wheel-preference',
                                 f'--no-index\n--find-links {index}\nfleet-fixture>=1,<3\n'))
    subprocess.run([binding['python'], '-I', '-c',
                    'import fleet_fixture; assert fleet_fixture.VERSION == "1.0"'],
                   check=True, timeout=10)


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
    monkeypatch.delenv('PANTHEON_PYTHON_CACHE', raising=False)
    candidate = tmp_path / 'python3'
    candidate.touch()
    monkeypatch.setattr(install, 'remote_filesystem', lambda p: str(p) == sys.base_prefix)
    monkeypatch.setattr(install.os, 'get_exec_path', lambda: [str(tmp_path)])
    monkeypatch.setattr(install, 'COMMON_BIN', ())
    monkeypatch.setattr(install.subprocess, 'run', lambda *a, **k:
        subprocess.CompletedProcess(a, 0, json.dumps([[3, 12], '/node-local-python'])))
    assert install.local_interpreter() == str(candidate)
    candidate.unlink()
    with pytest.raises(RuntimeError, match='node-local Python'):
        install.local_interpreter()


def test_a_python_too_old_finds_the_nodes_newer_one_off_path(tmp_path, monkeypatch):
    # A macOS node opened from Finder sees only /usr/bin, whose python3 is
    # 3.9; Homebrew's Python was never found and every install failed.
    monkeypatch.setenv('PANTHEON_PYTHON_CACHE', str(tmp_path / 'cache'))
    system, brew = tmp_path / 'usr-bin', tmp_path / 'homebrew-bin'
    system.mkdir(), brew.mkdir()
    (system / 'python3').touch()
    (brew / 'python3.14').touch()
    monkeypatch.setattr(install, 'remote_filesystem', lambda _: False)
    monkeypatch.setattr(install.os, 'get_exec_path', lambda: [str(system)])
    monkeypatch.setattr(install, 'COMMON_BIN', (str(brew),))
    versions = {str(system / 'python3'): [3, 9], str(brew / 'python3.14'): [3, 14]}
    monkeypatch.setattr(install.subprocess, 'run', lambda argv, *a, **k:
        subprocess.CompletedProcess(argv, 0, json.dumps([versions[argv[0]], '/local'])))
    assert install.local_interpreter((3, 9)) == str(brew / 'python3.14')
    assert install.local_interpreter((3, 12)) is None
    (brew / 'python3.14').unlink()
    with pytest.raises(RuntimeError, match=r'found Python 3\.9'):
        install.local_interpreter((3, 9))


def test_explicit_node_cache_preserves_shared_module_python(tmp_path, monkeypatch):
    monkeypatch.setenv('PANTHEON_PYTHON_CACHE', str(tmp_path / 'shared-cache'))
    monkeypatch.setattr(install, 'remote_filesystem', lambda _: True)
    monkeypatch.setattr(install.os, 'get_exec_path',
                        lambda: pytest.fail('Must retain the node-selected interpreter'))
    assert install.local_interpreter() is None


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


@pytest.mark.parametrize('native', [True, False])
def test_recreated_cloud_sandbox_restores_dependencies_from_the_durable_snapshot(tmp_path, monkeypatch, native):
    """A Workspace sandbox (and its /tmp) is recreated after idle; its volume is not."""
    if native and not install.native_tar():
        pytest.skip('no system tar')
    if not native:
        monkeypatch.setattr(install, 'native_tar', lambda: None)
    package, target = app(tmp_path, 'notebook')
    cache = tmp_path / 'tmp-of-this-sandbox'
    snapshots = tmp_path / 'volume' / 'python-environment-snapshots'
    monkeypatch.setattr(install, 'dependency_cache', lambda _: cache)
    monkeypatch.setattr(install, 'durable_snapshots', lambda _: snapshots)

    def prepare():
        with (target / 'dependencies.log').open('w') as log:
            reused = install.prepare(package, target, log)
        return reused, (target / 'dependencies.log').read_text()

    reused, log = prepare()
    assert not reused and 'Saved a Workspace snapshot' in log
    assert len(list(snapshots.glob('*.tar.gz'))) == 1
    shutil.rmtree(cache)  # the sandbox was recreated
    reused, log = prepare()
    assert reused and 'Restored Python dependencies from the Workspace snapshot' in log
    python = json.loads((target / 'python-environment.json').read_text())['python']
    subprocess.run([python, '-I', '-c', 'import sys; assert sys.prefix != sys.base_prefix'], check=True, timeout=10)
    # A damaged snapshot is ignored and the environment rebuilt (and saved again).
    shutil.rmtree(cache)
    next(snapshots.glob('*.tar.gz')).write_bytes(b'not a tarball')
    reused, log = prepare()
    assert not reused and 'Ignoring unusable dependency snapshot' in log and 'Saved a Workspace snapshot' in log


def test_a_sandbox_back_from_idle_restores_its_running_apps_environments_at_boot(tmp_path, monkeypatch):
    """The node's ledger says which Apps ran; their environments come back before their starts."""
    import os
    import tempfile
    import time
    volume = tmp_path / 'volume'
    node = volume / 'apps' / 'f_1'
    monkeypatch.setattr(install, 'remote_filesystem', lambda path: Path(path).is_relative_to(volume))
    (tmp_path / 'tmp').mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path / 'tmp'))

    def installed(digest, app_id):
        package, target = node / 'packages' / digest, node / 'installations' / digest
        package.mkdir(parents=True)
        target.mkdir(parents=True)
        (package / 'app.json').write_text(json.dumps({'id': app_id, 'version': '1'}))
        (package / 'requirements.txt').write_text('')
        with (target / 'dependencies.log').open('w') as log:
            assert not install.prepare(package, target, log)
        return package, target, json.loads((target / 'python-environment.json').read_text())

    running, stopped = installed('d1', 'notebook'), installed('d2', 'office')
    snapshots = node / 'python-environment-snapshots'
    assert len(list(snapshots.glob('*.tar.gz'))) == 2
    (node / 'ledger.json').write_text(json.dumps({'instances': {
        'i1': {'digest': 'd1', 'state': 'ready'}, 'i2': {'digest': 'd2', 'state': 'stopped'}}}))
    # Snapshots no installation uses: an old one goes, a recent one stays a while.
    old, recent = snapshots / ('a' * 64 + '.tar.gz'), snapshots / ('b' * 64 + '.tar.gz')
    old.write_bytes(b'old')
    recent.write_bytes(b'recent')
    os.utime(old, (time.time() - 3 * 86400,) * 2)
    shutil.rmtree(tmp_path / 'tmp')  # the sandbox was recreated
    (tmp_path / 'tmp').mkdir()

    log = io.StringIO()
    # A start already preparing an environment keeps it; warming moves on.
    root = Path(running[2]['python']).parent.parent
    with install.environment_lock(root.parent / (root.name + '.lock')):
        assert install.warm(volume, log) == 0
    assert not root.exists()
    assert install.warm(volume, log) == 1
    assert (root / '.fleet-ready.json').is_file()
    assert not Path(stopped[2]['python']).exists()
    assert not old.exists() and recent.exists()
    with (running[1] / 'dependencies.log').open('w') as start:
        assert install.prepare(running[0], running[1], start)
    assert 'Restored' not in (running[1] / 'dependencies.log').read_text()
    # Nothing left to do: a second boot restores nothing.
    assert install.warm(volume, io.StringIO()) == 0
    command = subprocess.run([sys.executable, install.__file__, '--warm', str(tmp_path / 'nothing')],
                             capture_output=True, text=True, timeout=30)
    assert command.returncode == 0 and 'Restored 0 environment(s)' in command.stdout


def test_node_configured_shared_cache_reuses_dependencies_across_jobs(tmp_path, monkeypatch):
    cache = tmp_path / 'shared' / 'python-environments'
    monkeypatch.setenv('PANTHEON_PYTHON_CACHE', str(cache))
    a = app(tmp_path / 'job-a', '1')
    b = app(tmp_path / 'job-b', '2')
    first, second = run_install(*a), run_install(*b)
    assert first[1] == second[1]
    assert 'Reused' in second[0]['message']
    assert Path(first[1]['python']).is_relative_to(cache)
    assert install.durable_snapshots(a[1]) is None


@pytest.mark.parametrize('unsafe', ['relative', 'shared', 'symlink'])
def test_node_configured_cache_rejects_unsafe_paths(tmp_path, monkeypatch, unsafe):
    directory = tmp_path / 'cache'
    directory.mkdir(mode=0o700)
    if unsafe == 'relative':
        configured = 'relative-cache'
    elif unsafe == 'shared':
        directory.chmod(0o755)
        configured = str(directory)
    else:
        link = tmp_path / 'link'
        link.symlink_to(directory)
        configured = str(link)
    monkeypatch.setenv('PANTHEON_PYTHON_CACHE', configured)
    with pytest.raises(RuntimeError):
        install.dependency_cache(tmp_path / 'installation')


@pytest.mark.parametrize('value', [[], {}, {'protocol': True, 'playwright': ['chromium']},
    {'protocol': 2, 'playwright': ['chromium']}, {'protocol': 1, 'playwright': []},
    {'protocol': 1, 'playwright': ['chromium', 'chromium']},
    {'protocol': 1, 'playwright': ['--with-deps']}, {'protocol': 1, 'playwright': [None]},
    {'protocol': 1, 'playwright': ['chromium'], 'env': {}}])
def test_runtime_resource_declaration_rejected_before_environment_creation(tmp_path, value):
    package, target = app(tmp_path, 'invalid-resources')
    (package / 'runtime-resources.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):
        install.prepare(package, target, io.StringIO())
    assert not (tmp_path / 'python-environments').exists()
    assert not (target / 'python-environment.json').exists()


def test_resource_identity_reuse_snapshot_recovery_and_launch_path(tmp_path, monkeypatch):
    import os
    pairs = [app(tmp_path, str(index)) for index in (1, 2)]
    old_key = install.environment_key(*pairs[0], pairs[0][0] / 'requirements.txt')
    for package, _ in pairs:
        (package / 'runtime-resources.json').write_text('{"protocol":1,"playwright":["chromium"]}')
    assert old_key != install.environment_key(*pairs[0], pairs[0][0] / 'requirements.txt')
    snapshots = tmp_path / 'snapshots'
    monkeypatch.setattr(install, 'durable_snapshots', lambda _: snapshots)
    downloaded = []
    def prepare_browser(python, browsers, log):
        assert browsers == ['chromium']
        marker = python.parent.parent / 'browsers/fixture'
        if marker.exists():
            return False
        marker.parent.mkdir()
        marker.write_text('browser binary fixture')
        downloaded.append(marker)
        return True
    monkeypatch.setattr(install, 'prepare_playwright', prepare_browser)
    def prepare(pair):
        with (pair[1] / 'dependencies.log').open('w') as log:
            return install.prepare(*pair, log)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(prepare, pairs))
    assert sorted(results) == [False, True] and len(downloaded) == 1
    bindings = [json.loads((target / 'python-environment.json').read_text()) for _, target in pairs]
    assert bindings[0] == bindings[1]
    browser_dir = downloaded[0].parent
    launch = Path(install.__file__).with_name('launch.py')
    result = subprocess.run([sys.executable, str(launch), '--install', str(pairs[1][1]), '-c',
                             'import os; print(os.environ["PLAYWRIGHT_BROWSERS_PATH"])'],
                            env={**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': '/foreign'},
                            capture_output=True, text=True, check=True, timeout=10)
    assert result.stdout.strip() == str(browser_dir)
    shutil.rmtree(tmp_path / 'python-environments')
    assert prepare(pairs[1]) and len(downloaded) == 1
    assert downloaded[0].read_text() == 'browser binary fixture'


def test_browser_preparation_failure_never_publishes_ready_environment(tmp_path, monkeypatch):
    package, target = app(tmp_path, 'browser-failed')
    (package / 'runtime-resources.json').write_text('{"protocol":1,"playwright":["chromium"]}')
    def fail(*_):
        raise RuntimeError('download interrupted')
    monkeypatch.setattr(install, 'prepare_playwright', fail)
    with (target / 'dependencies.log').open('w') as log, pytest.raises(RuntimeError, match='download interrupted'):
        install.prepare(package, target, log)
    assert not list((tmp_path / 'python-environments').rglob('.fleet-ready.json'))
    assert not (target / 'python-environment.json').exists()


@pytest.mark.parametrize('probe_status', [0, 1, 2])
def test_browser_probe_downloads_only_missing_executable(tmp_path, monkeypatch, probe_status):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        assert kwargs['env']['PLAYWRIGHT_BROWSERS_PATH'] == str(tmp_path / 'browsers')
        assert '--with-deps' not in command
        return subprocess.CompletedProcess(command, probe_status if len(commands) == 1 else 0)
    monkeypatch.setattr(install.subprocess, 'run', run)
    if probe_status == 1:
        with pytest.raises(RuntimeError, match='node requirements'):
            install.prepare_playwright(tmp_path / 'bin/python', ['chromium'], io.StringIO())
        assert len(commands) == 1
    else:
        assert install.prepare_playwright(tmp_path / 'bin/python', ['chromium'], io.StringIO()) == (probe_status == 2)
        assert len(commands) == (3 if probe_status == 2 else 1)
