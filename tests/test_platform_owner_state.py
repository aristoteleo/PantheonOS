"""Persistent owner journals survive replacement of the process home directory."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pantheon.platform.fleet_api import FleetAPI
from pantheon.platform.owner_state import configured_directory
from pantheon.platform.service import PlatformService
from test_app_deployment import Nodes, Authority, apps


@pytest.mark.asyncio
async def test_owner_journals_resume_on_new_home_without_replaying_operations(tmp_path, monkeypatch):
    nodes = Nodes()
    original = nodes.status
    async def status(node):
        nodes.finish()
        return await original(node)
    nodes.status = status
    seed = 'persistent-platform-identity'
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: SimpleNamespace(_seed=seed))
    monkeypatch.setattr('pantheon.apps.lifecycle.FleetLifecycle', lambda resolver: nodes)
    authority = Authority(nodes)
    state = tmp_path/'volume/platform-private'
    monkeypatch.setenv('PANTHEON_PLATFORM_STATE_DIR', str(state))
    preset = tmp_path/'startup.json'
    preset.write_text(json.dumps(dict(owner='owner', operation_id='durable-startup', apps=apps())))
    preset.chmod(0o600)
    async def settle(service):
        async with asyncio.timeout(5):
            while (await service.platform_app_preset_status())['state'] == 'pending':
                await asyncio.sleep(.001)
        return await service.platform_app_preset_status()
    for cycle in (1, 2):
        home = tmp_path/f'container-{cycle}'
        home.mkdir()
        monkeypatch.setattr(Path, 'home', staticmethod(lambda: home))
        service = PlatformService(app_preset=preset, workspace_path=str(tmp_path))
        factory = service._dependency_starter
        def starter():
            value = factory()
            value.authority = authority
            return value
        service._dependency_starter = starter
        assert service._dependency_starter().root == state/hashlib.sha256(seed.encode()).hexdigest()/'app-dependency-starts'
        if cycle == 1:
            assert not state.exists()  # Construction/read-only discovery creates no journals.
        service._app_preset.interval = .001
        try:
            await service.run(remote=False)
            result = await settle(service)
            assert result['state'] == 'ready', result
            assert result['operation_id'] == 'durable-startup'
            assert len(nodes.calls) == 6
            assert service._app_deployments().inspect(owner='owner', operation_id='durable-startup')['state'] == 'ready'
            assert state.stat().st_mode & 0o777 == 0o700
            assert not list(home.iterdir())
        finally:
            await service.cleanup()


@pytest.mark.parametrize('kind', ['public', 'symlink', 'file'])
@pytest.mark.asyncio
async def test_invalid_private_directory_never_starts_preset(tmp_path, monkeypatch, kind):
    state = tmp_path/'owner'
    if kind == 'public':
        state.mkdir(); state.chmod(0o755)
    elif kind == 'symlink':
        target = tmp_path/'other'; target.mkdir(mode=0o700); state.symlink_to(target)
    else:
        state.write_text('not a directory')
    calls = []
    async def load():
        calls.append('loaded')
    service = PlatformService(owner_state_directory=state, app_preset_source=load)
    with pytest.raises((RuntimeError, OSError)):
        await service.run_setup()
    assert calls == []
    assert service._app_preset._task is None
    assert not hasattr(service, '_dependency_maintenance_task')


@pytest.mark.parametrize('value', ['', 'relative/state', '/volume/../other'])
def test_state_path_must_be_explicit_absolute(value):
    with pytest.raises(ValueError, match='absolute persistent'):
        configured_directory(value)


def test_legacy_mixin_and_unconfigured_platform_keep_home_location(tmp_path, monkeypatch):
    monkeypatch.delenv('PANTHEON_PLATFORM_STATE_DIR', raising=False)
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: tmp_path))
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: SimpleNamespace(_seed='legacy'))
    expected = tmp_path/'.pantheon/platform-private'/hashlib.sha256(b'legacy').hexdigest()/'app-dependency-starts'
    assert FleetAPI()._dependency_starter().root == expected
    assert PlatformService()._dependency_starter().root == expected
    assert not (tmp_path/'.pantheon').exists()


@pytest.mark.parametrize('source', ['flag', 'environment'])
def test_cli_delivers_persistent_directory(tmp_path, monkeypatch, source):
    from pantheon.platform import __main__ as cli
    captured = {}
    state = str(tmp_path/'volume/owner')
    monkeypatch.delenv('PANTHEON_PLATFORM_STATE_DIR', raising=False)
    monkeypatch.delenv('PANTHEON_APP_PRESET', raising=False)
    monkeypatch.delenv('PANTHEON_APP_PRESET_URL', raising=False)
    argv = ['platform', '--deployment-id', 'owner']
    if source == 'flag':
        argv += ['--owner-state-directory', state]
    else:
        monkeypatch.setenv('PANTHEON_PLATFORM_STATE_DIR', state)
    monkeypatch.setattr('sys.argv', argv)
    def create(**kwargs):
        captured.update(kwargs)
    async def serve(*args, **kwargs):
        pass
    monkeypatch.setattr(cli, 'PlatformService', create)
    monkeypatch.setattr(cli, 'serve', serve)
    cli.main()
    assert captured['owner_state_directory'] == state
