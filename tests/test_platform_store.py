"""Store content survives Agent removal and cannot drift between projects."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.platform.service import PlatformService
from pantheon.skills.store import SkillStore
from pantheon.store.local import LocalPackages


def package(version='1', name='example'):
    return {'type': 'skill', 'name': name, 'version': version,
            'content': f'---\nname: {name}\ndescription: Example\n---\nVersion {version}'}


@pytest.fixture
def homes(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir(); b.mkdir()
    return home, a, b


def test_same_package_can_have_distinct_project_versions(homes):
    _, a, b = homes
    first, second = LocalPackages(a), LocalPackages(b)
    first.install('pkg', package('1'))
    second.install('pkg', package('2'))
    assert first.list()['installs']['pkg']['version'] == '1'
    assert second.list()['installs']['pkg']['version'] == '2'
    second.uninstall('pkg')
    assert second.list()['installs'] == {}
    assert first.list()['installs']['pkg']['version'] == '1'
    assert (a / '.pantheon/skills/example/SKILL.md').exists()
    assert not (b / '.pantheon/skills/example').exists()


def test_listing_other_projects_does_not_prune_records(homes):
    _, a, b = homes
    first = LocalPackages(a)
    first.install('pkg', package())
    before = first.manifest.read_bytes()
    assert LocalPackages(b).list()['installs'] == {}
    assert first.manifest.read_bytes() == before
    (a / '.pantheon/skills/example/SKILL.md').unlink()
    assert first.list()['installs']['pkg']['available'] is False
    assert first.manifest.read_bytes() == before


def test_legacy_listing_and_uninstall_are_scoped_and_leave_other_roots(homes):
    home, a, b = homes
    legacy = {'pkg': {k: v for k, v in package().items() if k != 'content'}}
    manifest = home / '.pantheon/store_installs.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps(legacy))
    for root in (a, b):
        target = root / '.pantheon/skills/example/SKILL.md'
        target.parent.mkdir(parents=True)
        target.write_text('existing content')
    assert LocalPackages(a).list()['installs']['pkg']['legacy_unscoped'] is True
    LocalPackages(a).uninstall('pkg')
    assert LocalPackages(a).list()['installs'] == {}
    assert LocalPackages(b).list()['installs']['pkg']['legacy_unscoped'] is True
    assert manifest.exists()


def test_ambiguous_legacy_owner_does_not_delete_either_copy(homes):
    home, a, _ = homes
    local = LocalPackages(a)
    local.manifest.parent.mkdir()
    local.manifest.write_text(json.dumps({'pkg': package()}))
    for root in (home, a):
        target = root / '.pantheon/skills/example/SKILL.md'
        target.parent.mkdir(parents=True)
        target.write_text('keep')
    with pytest.raises(ValueError, match='both project and global'):
        local.uninstall('pkg')
    for root in (home, a):
        assert (root / '.pantheon/skills/example/SKILL.md').read_text() == 'keep'


def test_corrupt_manifest_prevents_writes(homes):
    _, a, _ = homes
    local = LocalPackages(a)
    local.manifest.parent.mkdir()
    local.manifest.write_text('{broken')
    with pytest.raises(ValueError):
        local.install('pkg', package())
    assert not (a / '.pantheon/skills').exists()
    assert local.manifest.read_text() == '{broken'


def test_atomic_manifest_failure_preserves_previous_records(homes, monkeypatch):
    _, a, _ = homes
    local = LocalPackages(a)
    local.install('first', package())
    before = local.manifest.read_bytes()
    def failed(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr('pantheon.store.local.os.replace', failed)
    with pytest.raises(RuntimeError, match='files were installed.*record could not be saved'):
        local.install('second', package(name='second'))
    assert local.manifest.read_bytes() == before
    assert not list(local.manifest.parent.glob('*.tmp'))


@pytest.mark.parametrize('payload', [
    {'name': '../../outside'}, {'path': '../outside'},
    {'files': {'skills/example/ok.txt': 'ok', '../outside': 'bad'}},
    {'files': {'store_installs.json': '{}'}},
])
def test_invalid_package_rejected_before_any_content_write(homes, payload):
    _, a, _ = homes
    with pytest.raises(ValueError):
        LocalPackages(a).install('pkg', {**package(), **payload})
    assert not (a / '.pantheon/skills').exists()


def test_concurrent_hosts_do_not_lose_install_records(homes):
    from concurrent.futures import ThreadPoolExecutor
    _, a, _ = homes
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: LocalPackages(a).install(str(i), package(name=f'skill-{i}')), range(8)))
    assert all(result['success'] for result in results)
    assert len(LocalPackages(a).list()['installs']) == 8


def test_install_pins_project_before_download_and_does_not_block_loop(homes, monkeypatch):
    _, a, b = homes
    async def scenario():
        host = PlatformService(workspace_path=a)
        entered, release = asyncio.Event(), asyncio.Event()
        async def download(*args):
            entered.set()
            await release.wait()
            return package()
        client = SimpleNamespace(download=download, auth=SimpleNamespace(is_logged_in=False))
        monkeypatch.setattr('pantheon.store.client.StoreClient', lambda: client)
        pending = asyncio.create_task(host.install_store_package('pkg'))
        await asyncio.wait_for(entered.wait(), 2)
        await host.set_active_project(str(b))
        release.set()
        assert (await pending)['success']
        assert (a / '.pantheon/skills/example/SKILL.md').exists()
        assert not (b / '.pantheon/skills').exists()
        assert (await host.get_installed_store_packages())['installs'] == {}
        await host.cleanup()
    asyncio.run(scenario())


def test_slow_filesystem_install_does_not_block_platform_ping(homes, monkeypatch):
    _, a, _ = homes
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        def install(*args):
            entered.set()
            assert release.wait(3)
            return {'success': True}
        monkeypatch.setattr(LocalPackages, 'install', install)
        monkeypatch.setattr('pantheon.store.client.StoreClient', lambda: SimpleNamespace(
            download=AsyncMock(return_value=package()), auth=SimpleNamespace(is_logged_in=False)))
        host = PlatformService(workspace_path=a)
        task = asyncio.create_task(host.install_store_package('pkg'))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert (await asyncio.wait_for(host.platform_info(), .2))['service'] == 'pantheon-platform'
        finally:
            release.set()
            await task
            await host.cleanup()
    asyncio.run(scenario())


def test_skills_catalog_has_no_agent_initialization_side_effects(homes, monkeypatch):
    home, a, _ = homes
    factory = home / 'factory-skills'
    def skill(root, name, content):
        path = root / name / 'SKILL.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\nname: {name}\ndescription: Example\n---\n{content}')
    skill(factory, 'same', 'original')
    skill(home / '.pantheon/skills', 'same', 'modified')
    skill(factory, 'excluded', 'hidden')
    (factory / 'excluded/guide.md').write_text('also hidden')
    skill(factory, 'bundle', 'main')
    (factory / 'bundle/guide.md').write_text('resource')
    for i in range(205):
        skill(factory, f'extra-{i}', 'content')
    host = PlatformService(workspace_path=a)
    monkeypatch.setattr(host, '_skill_catalog', lambda: SkillStore(
        a / '.pantheon/skills', a / '.pantheon/skills-runtime',
        global_skills_dir=home / '.pantheon/skills', factory_skills_dir=factory,
        excluded_skills=['excluded'], create_dirs=False))
    result = asyncio.run(host.get_local_skills())
    assert result['success']
    by_path = {entry['path']: entry for entry in result['skills']}
    assert by_path['same']['scope'] == 'global'
    assert by_path['same']['modified'] is True
    assert by_path['bundle/guide']['scope'] == 'factory'
    assert 'excluded' not in by_path and 'excluded/guide' not in by_path
    assert len(by_path) == 208  # no Agent prompt-injection index limit
    assert not (a / '.pantheon').exists()


def test_store_runs_when_agent_and_learning_imports_are_forbidden(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = '''
import asyncio, importlib.abc, sys
from pathlib import Path
class NoAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if any(fullname == p or fullname.startswith(p + '.') for p in
               ('pantheon.agent', 'pantheon.chatroom', 'pantheon.team', 'pantheon.factory', 'pantheon.internal.learning_system', 'pantheon.internal.memory', 'pantheon.internal.memory_system')):
            raise AssertionError('Store imported Agent: ' + fullname)
sys.meta_path.insert(0, NoAgent())
from pantheon.platform.service import PlatformService
from pantheon.store.local import LocalPackages
async def check():
    host = PlatformService(workspace_path=Path.cwd())
    local = LocalPackages(Path.cwd())
    local.install('demo', {'type':'agent', 'name':'demo', 'version':'1', 'content':'# recipe'})
    assert (await host.get_installed_store_packages())['installs']['demo']['version'] == '1'
    skills = await host.get_local_skills()
    assert skills['success'], skills
    assert skills['skills']
    assert (await host.uninstall_store_package('demo'))['success']
    assert not Path('.pantheon/agents/demo.md').exists()
    await host.cleanup()
asyncio.run(check())
'''
    env = {k:v for k,v in os.environ.items() if not k.startswith(('PANTHEON_', 'FLEET_', 'NATS_'))}
    env.update(HOME=str(tmp_path), PYTHONPATH=str(root))
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_global_install_is_visible_without_duplicating_project_files(homes):
    home, a, b = homes
    LocalPackages(home).install('global', package())
    for root in (a, b):
        info = LocalPackages(root).list()['installs']['global']
        assert info['work_dir'] == str(home.resolve())
        assert not (root / '.pantheon/skills').exists()
    LocalPackages(a).uninstall('global')
    assert LocalPackages(b).list()['installs'] == {}


def test_rewritten_skill_bundle_cannot_follow_symlink_outside_root(homes):
    _, a, b = homes
    scripts = a / '.pantheon/skills/source/example/scripts'
    scripts.parent.mkdir(parents=True)
    scripts.symlink_to(b, target_is_directory=True)
    download = {**package(), 'path': 'source/example',
                'files': {'skills/example/scripts/escape.py': 'bad'}}
    with pytest.raises(ValueError, match='escapes'):
        LocalPackages(a).install('pkg', download)
    assert not (b / 'escape.py').exists()
    assert not (scripts.parent / 'SKILL.md').exists()


def test_skill_storage_compatibility_alias_preserves_type_identity():
    from pantheon.internal.learning_system.store import SkillStore as OldStore
    from pantheon.internal.learning_system.types import SkillHeader as OldHeader
    from pantheon.skills.types import SkillHeader
    assert OldStore is SkillStore
    assert OldHeader is SkillHeader
