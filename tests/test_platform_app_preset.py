"""Platform-owned startup uses ordinary deployment checkpoints, not an Agent child."""
import asyncio
import json

import httpx
import pytest

from pantheon.platform.app_preset import AppPreset, read_preset, fetch_hub_preset
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
@pytest.mark.parametrize('source', ['file', 'hub'])
async def test_platform_startup_advances_original_recipe_and_does_not_respawn_stopped_app(tmp_path, source):
    nodes = Nodes()
    deploy = coordinator(tmp_path / 'owner', nodes, Authority(nodes))
    path = preset(tmp_path)
    reads = []
    def response(request):
        reads.append(request)
        assert request.headers['authorization'] == 'Bearer fixture-key'
        return httpx.Response(200, json={'protocol': 1, 'revision': 1, 'recipe': read_preset(path)})
    async def load():
        return await fetch_hub_preset('https://hub.test/api/fleet/apps/startup/default',
            hub='https://hub.test', token='fixture-key', owner='owner', transport=httpx.MockTransport(response))
    service = PlatformService(app_preset=path if source == 'file' else None,
                              app_preset_source=load if source == 'hub' else None)
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
        assert len(reads) == (1 if source == 'hub' else 0)
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
    monkeypatch.delenv('PANTHEON_APP_PRESET_URL', raising=False)
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


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['foreign-host', 'http', 'credentials', 'query', 'wrong-path', 'missing-token',
    'redirect', 'forbidden', 'owner', 'revision', 'protocol', 'oversized', 'duplicate', 'cycle', 'malformed'])
async def test_hub_preset_rejects_untrusted_source_or_invalid_recipe_before_app_operations(tmp_path, kind):
    url = 'https://hub.test/api/fleet/apps/startup/default'
    token = 'fixture-key'
    envelope = {'protocol': 1, 'revision': 1, 'recipe': read_preset(preset(tmp_path))}
    status, content = 200, None
    bad_sources = {'foreign-host': 'https://other.test/api/fleet/apps/startup/default',
        'http': url.replace('https:', 'http:'), 'credentials': url.replace('hub.test', 'user@hub.test'),
        'query': url + '?redirect=another', 'wrong-path': 'https://hub.test/api/users'}
    url = bad_sources.get(kind, url)
    if kind == 'missing-token': token = ''
    elif kind == 'redirect': status = 307
    elif kind == 'forbidden': status = 403
    elif kind == 'owner': envelope['recipe']['owner'] = 'another-owner'
    elif kind == 'revision': envelope['revision'] = True
    elif kind == 'protocol': envelope['protocol'] = 2
    elif kind == 'oversized': content = b'x' * (128 * 1024 + 1)
    elif kind == 'duplicate': content = b'{"protocol":2,"protocol":1,"revision":0,"recipe":null}'
    elif kind == 'malformed': content = b'private invalid response'
    elif kind == 'cycle':
        name = next(iter(envelope['recipe']['apps']))
        envelope['recipe']['apps'][name]['bindings'] = {'loop': {'$app': name}}
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(status, content=content, json=envelope if content is None else None,
                              headers={'Location': 'https://other.test/private'})
    async def load():
        return await fetch_hub_preset(url, hub='https://hub.test', token=token, owner='owner',
                                      transport=httpx.MockTransport(response))
    async def forbidden(**kwargs): pytest.fail('Invalid Hub recipe reached deployment')
    driver = AppPreset(None, load=load, advance=forbidden)
    driver.start()
    assert await settled(driver) == {'state': 'needs_attention', 'reason': 'preset_unavailable'}
    await driver.stop()
    assert len(requests) == (0 if kind in bad_sources or kind == 'missing-token' else 1)


@pytest.mark.asyncio
async def test_hub_disabled_recipe_keeps_platform_serving_without_starting_apps():
    async def load():
        return await fetch_hub_preset('https://hub.test/api/fleet/apps/startup/default',
            hub='https://hub.test', token='fixture-key', owner='owner',
            transport=httpx.MockTransport(lambda _: httpx.Response(200,
                json={'protocol': 1, 'revision': 3, 'recipe': None})))
    service = PlatformService(app_preset_source=load)
    try:
        await service.run(remote=False)
        assert await settled(service._app_preset) == {'state': 'disabled'}
        assert (await service.platform_info())['service'] == 'pantheon-platform'
    finally:
        await service.cleanup()


@pytest.mark.parametrize('source', ['flag', 'environment'])
def test_cli_passes_hub_source_with_existing_fleet_identity(monkeypatch, source):
    import hashlib
    from pantheon.platform import __main__ as cli
    import pantheon.platform.app_preset as module
    url = 'https://hub.test/api/fleet/apps/startup/default'
    monkeypatch.delenv('PANTHEON_APP_PRESET', raising=False)
    monkeypatch.delenv('PANTHEON_APP_PRESET_URL', raising=False)
    monkeypatch.setenv('PANTHEON_HUB_URL', 'https://hub.test')
    monkeypatch.setenv('FLEET_KEY', 'fixture-key')
    monkeypatch.setenv('USER_ID', 'alice')
    argv = ['platform', '--deployment-id', 'deployment']
    if source == 'flag': argv += ['--app-preset-url', url]
    else: monkeypatch.setenv('PANTHEON_APP_PRESET_URL', url)
    monkeypatch.setattr('sys.argv', argv)
    captured = {}
    async def fetch(url, **kwargs): captured.update(url=url, **kwargs)
    async def serve(service, **kwargs):
        assert kwargs['agent_command'] is None
        await service['app_preset_source']()
    monkeypatch.setattr(module, 'fetch_hub_preset', fetch)
    monkeypatch.setattr(cli, 'PlatformService', lambda **kwargs: kwargs)
    monkeypatch.setattr(cli, 'serve', serve)
    cli.main()
    assert captured == dict(url=url, hub='https://hub.test', token='fixture-key',
                            owner='f_' + hashlib.sha256(b'alice').hexdigest()[:16])
