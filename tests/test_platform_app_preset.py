"""Platform-owned startup uses ordinary deployment checkpoints, not an Agent child."""
import asyncio
import json

import pytest

from pantheon.platform.app_preset import AppPreset, read_preset
from pantheon.platform.service import PlatformService
from test_app_deployment import Nodes, Authority, apps, coordinator


def preset(tmp_path):
    path = tmp_path / 'preset.json'
    path.write_text(json.dumps(dict(owner='owner', operation_id='deployment-one', apps=apps())))
    path.chmod(0o600)
    return path


async def settled(driver):
    async def wait():
        while driver.status()['state'] == 'pending':
            await asyncio.sleep(.001)
        return driver.status()
    return await asyncio.wait_for(wait(), 5)


@pytest.mark.asyncio
async def test_platform_startup_advances_original_recipe_and_does_not_respawn_stopped_app(tmp_path):
    nodes = Nodes()
    deploy = coordinator(tmp_path / 'owner', nodes, Authority(nodes))
    path = preset(tmp_path)
    service = PlatformService(app_preset=path)
    service._app_deployments = lambda: deploy
    service._app_preset.interval = .001
    # The real platform method is used. These fake nodes finish asynchronous
    # Fleet operations when the driver observes their next status.
    original = nodes.status
    async def status(node):
        nodes.finish()
        return await original(node)
    nodes.status = status
    try:
        await service.run(remote=False)
        assert (await service.platform_info())['service'] == 'pantheon-platform'
        result = await settled(service._app_preset)
        assert result['state'] == 'ready', result
        assert len(nodes.calls) == 6
        assert 'prepared' not in result and 'components' not in json.dumps(result)
    finally:
        await service.cleanup()
    # A new platform process verifies the same intent, retaining all original
    # operation identities, rather than creating a second Agent generation.
    driver = AppPreset(path, advance=service.fleet_app_deploy, interval=.001)
    driver.start()
    assert (await settled(driver))['state'] == 'ready'
    await driver.stop()
    assert len(nodes.calls) == 6
    for instance in nodes.states['worker']['instances'].values():
        instance['state'] = 'stopped'
    driver = AppPreset(path, advance=service.fleet_app_deploy, interval=.001)
    driver.start()
    assert (await settled(driver))['state'] == 'needs_attention'
    await driver.stop()
    await service.cleanup()
    assert len(nodes.calls) == 6


@pytest.mark.asyncio
async def test_unknown_outcome_requires_owner_recovery_with_original_operation(tmp_path):
    nodes = Nodes()
    nodes.loss = 'install'
    deploy = coordinator(tmp_path / 'owner', nodes, Authority(nodes))
    calls = []
    async def advance(**recipe):
        calls.append(recipe)
        return {'success': True, **await deploy.advance(**recipe)}
    path = preset(tmp_path)
    driver = AppPreset(path, advance=advance, interval=.001)
    driver.start()
    result = await settled(driver)
    assert result['reason'] == 'inspect_deployment'
    assert len(calls) == 1 and len(nodes.calls) == 1
    await driver.stop()
    # Explicit recovery resumes the original journal/operation, not a new ID.
    original_id = nodes.calls[0][2]
    nodes.finish()
    result = await deploy.advance(**read_preset(path))
    assert result['state'] == 'pending'
    assert nodes.calls[0][2] == original_id


@pytest.mark.asyncio
async def test_shutdown_drains_accepted_advance_before_returning(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def advance(**recipe):
        entered.set()
        await release.wait()
        calls.append(recipe['operation_id'])
        return dict(success=True, state='pending', phase='installing', app='agent',
                    operation_id=recipe['operation_id'])
    driver = AppPreset(preset(tmp_path), advance=advance, interval=30)
    driver.start()
    await asyncio.wait_for(entered.wait(), 3)
    stopping = asyncio.create_task(driver.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await asyncio.wait_for(stopping, 3)
    assert calls == ['deployment-one']
    assert driver.status()['state'] == 'paused'


@pytest.mark.asyncio
async def test_invalid_preset_does_not_block_platform_or_expose_contents(tmp_path):
    path = preset(tmp_path)
    path.write_text('private-credential-not-json')
    service = PlatformService(app_preset=path)
    try:
        await service.run(remote=False)
        assert await settled(service._app_preset) == {'state': 'needs_attention', 'reason': 'invalid_preset'}
        assert (await service.platform_info())['api_version'] == 1
        assert 'chat' not in service.functions
    finally:
        await service.cleanup()


@pytest.mark.asyncio
async def test_bounded_startup_does_not_turn_into_app_health_supervisor(tmp_path):
    async def forbidden(**kwargs):
        pytest.fail('Startup deadline was ignored')
    driver = AppPreset(preset(tmp_path), advance=forbidden, duration=0)
    driver.start()
    assert (await settled(driver))['reason'] == 'startup_deadline'
    await driver.stop()


@pytest.mark.parametrize('kind', ['public', 'symlink', 'oversized', 'directory', 'duplicate', 'extra'])
def test_only_bounded_private_exact_recipes_are_loaded(tmp_path, kind):
    path = preset(tmp_path)
    if kind == 'public':
        path.chmod(0o644)
    elif kind == 'symlink':
        target = tmp_path / 'link.json'; target.symlink_to(path); path = target
    elif kind == 'oversized':
        path.write_bytes(b' ' * (64 * 1024 + 1))
    elif kind == 'directory':
        path = tmp_path
    elif kind == 'duplicate':
        path.write_text('{"owner":"owner","owner":"another"}')
    else:
        spec = json.loads(path.read_text()); spec['extra'] = True; path.write_text(json.dumps(spec))
    with pytest.raises((ValueError, OSError)):
        read_preset(path)


@pytest.mark.parametrize('source', ['flag', 'environment', 'legacy'])
def test_cli_passes_explicit_preset_and_keeps_legacy_entry(monkeypatch, tmp_path, source):
    from pantheon.platform import __main__ as cli
    captured = {}
    path = str(preset(tmp_path))
    monkeypatch.delenv('PANTHEON_APP_PRESET', raising=False)
    argv = ['platform', '--deployment-id', 'user']
    if source == 'flag':
        argv += ['--app-preset', path]
    elif source == 'environment':
        monkeypatch.setenv('PANTHEON_APP_PRESET', path)
    else:
        argv += ['--legacy-agent', '--', '--debug']
    monkeypatch.setattr('sys.argv', argv)
    def service(**kwargs):
        captured.update(kwargs)
        return object()
    async def serve(service, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(cli, 'PlatformService', service)
    monkeypatch.setattr(cli, 'serve', serve)
    cli.main()
    assert captured['id_hash'] == 'platform:user'
    assert captured['app_preset'] == (None if source == 'legacy' else path)
    if source == 'legacy':
        assert captured['agent_command'][-2:] == ['--id_hash=user', '--debug']
    else:
        assert captured['agent_command'] is None
