"""An App package built from its repository (docs/agent/agent-app-repository.md §1)."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pantheonos.appkit import app_build

BUILD_PY = '''
import json, os, pathlib, sys
out = pathlib.Path(os.environ["APP_BUILD_OUTPUT"])
if pathlib.Path("fail").exists():
    print("compiler says no"); sys.exit(3)
manifest = json.loads(pathlib.Path("app.json").read_text())
manifest.pop("build")
manifest["built"] = [os.environ["APP_BUILD_PLATFORM"], os.environ["APP_BUILD_REVISION"]]
(out / "app.json").write_text(json.dumps(manifest))
(out / "fleet.json").write_text("{}")
pathlib.Path("scratch").write_text("the build may write in its copy")
pathlib.Path(os.environ["HOME"], ".npm").mkdir(exist_ok=True)
print("built")
'''


def source(tmp_path, fail=False):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'app.json').write_text(json.dumps({'id': 'demo', 'name': 'Demo', 'version': '1.0.0',
        'entry': {'backend': 'backend/__init__.py'}, 'build': {'argv': ['python3', 'build.py'], 'timeout_seconds': 60}}))
    (root / 'build.py').write_text(BUILD_PY)
    if fail:
        (root / 'fail').write_text('')
    return root


def test_declared_validates_the_build():
    assert app_build.declared({'id': 'x'}) is None
    assert app_build.declared({'build': {'argv': ['make']}}) == {'argv': ['make'], 'timeout_seconds': 1800}
    with pytest.raises(ValueError):
        app_build.declared({'build': {'argv': []}})
    with pytest.raises(ValueError):
        app_build.declared({'build': {'argv': ['make'], 'shell': True}})


@pytest.mark.asyncio
async def test_build_runs_in_a_copy_and_produces_the_package(tmp_path):
    root = source(tmp_path)
    manifest = json.loads((root / 'app.json').read_text())
    built = await app_build.run(root, tmp_path / 'out', manifest=manifest, platform='linux-amd64',
                                revision='a' * 40, cache=tmp_path / 'cache', log=tmp_path / 'build.log')
    assert built['built'] == ['linux-amd64', 'a' * 40]
    assert (tmp_path / 'out' / 'fleet.json').is_file()
    assert not (root / 'scratch').exists()  # the snapshot is never modified
    assert 'built' in (tmp_path / 'build.log').read_text()
    # Tool caches stay in the build cache, out of the builder's own data.
    assert (tmp_path / 'cache' / 'home' / '.npm').is_dir()


@pytest.mark.asyncio
async def test_a_failed_build_reports_its_log_and_leaves_no_output(tmp_path):
    root = source(tmp_path, fail=True)
    manifest = json.loads((root / 'app.json').read_text())
    with pytest.raises(RuntimeError, match='compiler says no'):
        await app_build.run(root, tmp_path / 'out', manifest=manifest, platform='linux-amd64',
                            revision='a' * 40, cache=tmp_path / 'cache', log=tmp_path / 'build.log')
    assert not (tmp_path / 'out').exists()


@pytest.mark.asyncio
async def test_desktop_builds_once_per_commit_and_platform(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from apps.desktop.app_builds import AppBuilds
    root = source(tmp_path)
    manifest = json.loads((root / 'app.json').read_text())
    calls = []

    def resolve(app_id, scope, commit, repository_id):
        calls.append(commit)
        return {'manifest': manifest, 'dir': str(root),
                'revision': {'scope': 'user', 'commit': 'c' * 40, 'version': '1.0.0', 'repository_id': 'r1'}}
    manager = SimpleNamespace(records=tmp_path / 'records', versions=SimpleNamespace(resolve=resolve))
    builds = AppBuilds(manager)
    revision = {'scope': 'user', 'commit': 'c' * 40, 'repository_id': 'r1'}
    assert builds.status('demo', revision, 'linux-amd64')['state'] == 'none'
    assert builds.package('demo', revision, 'linux-amd64') is None
    started = await builds.start('demo', revision, 'linux-amd64')
    assert started['state'] == 'running'
    await asyncio.gather(*AppBuilds._tasks.values())
    status = builds.status('demo', revision, 'linux-amd64')
    assert status['state'] == 'succeeded' and 'built' in status['log']
    package = builds.package('demo', revision, 'linux-amd64')
    assert json.loads((package / 'app.json').read_text())['built'][0] == 'linux-amd64'
    again = await builds.start('demo', revision, 'linux-amd64')
    assert again['state'] == 'succeeded'  # reused, not rebuilt
    assert builds.status('demo', revision, 'darwin-arm64')['state'] == 'none'
    with pytest.raises(ValueError):
        builds.status('demo', revision, 'plan9-mips')
