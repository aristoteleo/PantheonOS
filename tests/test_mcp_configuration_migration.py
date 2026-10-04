"""Original MCP launch -> private backup -> ordinary App on reviewed coordinates."""
from contextlib import asynccontextmanager
import asyncio
import json
from pathlib import Path
import shlex
import socket
import sys

from fastmcp import FastMCP
import pytest
import uvicorn

from pantheon.apps.builtin.mcp import MCPGatewayToolSet
from pantheon.apps.builtin.mcp.manager import MCPManager, MCPServerConfig, MCPServerInstance
from pantheon.apps.builtin.mcp.scoped import ScopedMCP
from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.chatroom.migration import inspect_legacy, fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_mcp_configuration import MCPConfigurationConversion
from pantheon.chatroom.migration_import import import_backup
from pantheon.settings import Settings
from test_agent_migration import legacy
from test_agent_migration_credentials import vault, read_key
from test_mcp_environment_credentials import local_vault
from test_scoped_mcp_app import config


def original(legacy, tmp_path):
    project = Path(legacy['project_config'])
    (project/'settings.json').write_text('{}')
    settings = Settings(project.parent, user_home=Path(legacy['global_config']), isolated_env=True, environment={})
    manager = MCPManager(log_dir=str(tmp_path/'logs'), config_path=project/'mcp.json')
    gateway = object.__new__(MCPGatewayToolSet)
    gateway._manager, gateway._migration_settings = manager, settings
    return settings, manager, gateway


@pytest.fixture
async def captured(legacy, tmp_path, monkeypatch, request):
    settings, manager, gateway = original(legacy, tmp_path)
    selected = getattr(request, 'param', {'MCP_KEY': '${ORIGINAL_MCP_KEY}', 'MODE': 'read'})
    if 'environment' in selected:
        (settings.pantheon_dir/'settings.json').write_text(json.dumps({
            'enable_mcp_tools': selected['enable_mcp_tools']}))
        settings = Settings(settings.pantheon_dir.parent, user_home=settings.user_home,
                            isolated_env=True, environment={})
        gateway._migration_settings = settings
        env = selected['environment']
    else:
        env = selected
    source = tmp_path/'source with spaces'; source.mkdir()
    (source/'marker.txt').write_text('source marker')
    script = source/'server.py'
    script.write_text('''from fastmcp import FastMCP
from pathlib import Path
import os
mcp = FastMCP("captured")
@mcp.tool
def check() -> dict:
    """Check explicit launch coordinates and private environment."""
    return {"cwd": str(Path.cwd()), "marker": Path("marker.txt").read_text(),
            "key_matches": os.environ.get("MCP_KEY") == "original-key",
            "mode": os.environ.get("MODE"), "owner_present": "FLEET_KEY" in os.environ}
mcp.run(transport="stdio", show_banner=False)
''')
    command = shlex.join([sys.executable, str(script)])
    (settings.pantheon_dir/'mcp.json').write_text(json.dumps({**selected.get('mcp_fields', {}), 'servers': {'docs': {
        'type': 'stdio', 'command': command, 'env': env}}}))
    instance = MCPServerInstance(MCPServerConfig(name='docs', type='stdio', command=command, env=env))
    manager.instances['docs'] = instance
    monkeypatch.setenv('ORIGINAL_MCP_KEY', 'original-key')
    monkeypatch.setenv('FLEET_KEY', 'never-copy-owner-key')
    previous = Path.cwd()
    monkeypatch.chdir(source)
    assert await instance.start()
    try:
        async with instance.stdio_client as client:
            await client.list_tools()
            instance.status = 'running'
            before = (await client.call_tool('check')).structured_content
            await manager._mount_to_gateway('docs', instance)
            monkeypatch.chdir(tmp_path)
            monkeypatch.setenv('ORIGINAL_MCP_KEY', 'changed-parent-key')
            result = await gateway.export_migration_configuration('runtime', {'docs': []}, ['mcp'])
            assert result['success'], result
    finally:
        await instance.stop()
        monkeypatch.chdir(previous)
    legacy['mcp_configuration_file'] = result['source']
    raw = Path(result['source']).read_text()
    assert ('original-key' in raw) == ('MCP_KEY' in env)
    assert 'never-copy-owner-key' not in raw
    assert 'original-key' not in json.dumps(result)
    assert not Path(result['source']).stat().st_mode & 0o077
    doc = json.loads(raw)
    assert doc['selection']['enable_mcp_tools'] == settings.enable_mcp_tools
    assert doc['servers']['docs']['command'] == [sys.executable, str(script)]
    assert doc['servers']['docs']['cwd'] == str(source)
    return result, before, script


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{}, {'MODE': 'read'}], indirect=True)
async def test_stdio_without_credentials_builds_and_runs_with_reviewed_environment(legacy, captured, tmp_path):
    result, before, script = captured
    fence, saved = backup(legacy, tmp_path)
    try:
        fields = list(json.loads(Path(result['source']).read_text())['servers']['docs']['environment']['values'])
        plan = MCPConfigurationConversion(saved['directory'], digest=saved['sha256'], fence=fence,
            vault=local_vault(tmp_path), targets={'docs': {
                'command': [sys.executable, str(script)], 'cwd': str(script.parent)}},
            environments={'docs': {'literals': fields, 'credentials': {}}})
        # The fake executable makes accidental provisioning of any credential fail.
        plan.provision()
        package = plan.build(tmp_path/'literal-package', 'linux-amd64')
        assert json.loads((package/'fleet.json').read_text())['components'][0]['configuration'].get('credentials', {}) == {}
        described = plan.describe()
        host = ScopedMCP(described['contract']['exports'], config(described['values']['mcp']['servers']))
        await host.start()
        try:
            assert await host.call('docs_check', {}) == {**before, 'owner_present': False}
        finally:
            await host.close()
    finally:
        fence.close()


def backup(legacy, tmp_path):
    fence = fence_legacy(legacy, operation='mcp-config', target=tmp_path/'agent-target', namespace='mcp-test')
    try:
        saved = backup_legacy(legacy, fence=fence, directory=tmp_path/'backup')
        return fence, saved
    except BaseException:
        fence.close()
        raise


def environment(source, *, secret=True):
    if not secret:
        return {'docs': {'literals': ['MODE', 'MCP_KEY'], 'credentials': {}}}
    return {'docs': {'literals': ['MODE'], 'credentials': {'MCP_KEY': {
        'source': source, 'alias': 'mcp-key', 'ref': 'node-secret://mcp-key', 'endpoint': 'https://api.test/v1'}}}}


def convert(saved, fence, vault, targets, source, *, secret=True):
    return MCPConfigurationConversion(saved['directory'], digest=saved['sha256'], fence=fence, vault=vault,
        targets=targets, environments=environment(source, secret=secret))


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'MODE':'read'}], indirect=True)
async def test_old_capture_can_build_provider_but_cannot_guess_agent_defaults(legacy, captured, tmp_path):
    result, _, script = captured
    path = Path(result['source'])
    document = json.loads(path.read_text())
    document.pop('selection')
    path.write_text(json.dumps(document))
    fence, saved = backup(legacy, tmp_path)
    try:
        vault = local_vault(tmp_path)
        plan = MCPConfigurationConversion(saved['directory'], digest=saved['sha256'], fence=fence, vault=vault,
            targets={'docs':{'command':[sys.executable,str(script)],'cwd':str(script.parent)}},
            environments={'docs':{'literals':['MODE'],'credentials':{}}})
        plan.build(tmp_path/'provider-only', 'linux-amd64')
        with pytest.raises(AssemblyError, match='Recapture'):
            plan.prepare_deployment(tmp_path/'agent-candidate', 'linux-amd64', name='mcp-provider',
                target={'node_id':vault.node_id,'scope':'migration','generation':0}, aliases={'mcp':'mcp'})
        assert not (tmp_path/'agent-candidate').exists()
    finally:
        fence.close()


@pytest.mark.asyncio
async def test_captured_stdio_relocates_with_native_vault_and_preserves_behaviour(legacy, captured, tmp_path, vault):
    result, before, script = captured
    target = tmp_path/'selected node work'; target.mkdir()
    (target/'marker.txt').write_text(before['marker'])
    target_script = target/'relocated.py'; target_script.write_bytes(script.read_bytes())
    fence, saved = backup(legacy, tmp_path)
    try:
        plan = convert(saved, fence, vault, {'docs': {'command': [sys.executable, str(target_script)],
            'cwd': str(target)}}, result['source'])
        described = plan.describe()
        assert 'original-key' not in json.dumps(described)
        assert described['owner'] == vault.owner and described['node_id'] == vault.node_id
        package = plan.build(tmp_path/'package', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64')
        assert all(b'original-key' not in p.read_bytes() for p in package.rglob('*') if p.is_file())
        plan.provision(); plan.provision()
        creds = {'mcp-key': RuntimeCredential('https://api.test/v1', read_key(vault, 'node-secret://mcp-key', 'https://api.test/v1'))}
        host = ScopedMCP(described['contract']['exports'], config(described['values']['mcp']['servers'], creds))
        await host.start()
        try:
            assert await host.call('docs_check', {}) == {**before, 'cwd': str(target), 'owner_present': False}
        finally:
            await host.close()
        # A built and provisioned candidate is still not proof of deployment,
        # source retirement, restored Agent defaults or migration admission.
        with pytest.raises(ValueError, match='explicit converter'):
            import_backup(saved['directory'], digest=saved['sha256'], fence=fence)
        assert not (tmp_path/'agent-target').exists()
    finally:
        fence.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['before-backup', 'settings-before-backup', 'after-review', 'relative-target', 'missing-env', 'overlap', 'public'])
async def test_stale_or_incomplete_capture_cannot_build_candidate(legacy, captured, tmp_path, change):
    result, _, script = captured
    config_path = Path(legacy['project_config'])/'mcp.json'
    if change == 'overlap':
        legacy['mcp_environment_file'] = result['source']
        with pytest.raises(ValueError, match='one MCP'):
            inspect_legacy(**legacy)
        return
    if change == 'public':
        Path(result['source']).chmod(0o644)
        with pytest.raises(ValueError, match='private'):
            inspect_legacy(**legacy)
        return
    if change == 'before-backup':
        config_path.write_text('{}')
    if change == 'settings-before-backup':
        (config_path.parent/'settings.json').write_text('{"enable_mcp_tools":true}')
    fence, saved = backup(legacy, tmp_path)
    try:
        targets = {'docs': {'command': [sys.executable, str(script)], 'cwd': str(script.parent)}}
        env = environment(result['source'], secret=False)
        if change == 'relative-target': targets['docs']['command'][0] = 'python'
        if change == 'missing-env': env = {}
        if change == 'after-review':
            plan = convert(saved, fence, local_vault(tmp_path), targets, result['source'], secret=False)
            config_path.write_text('{}')
            with pytest.raises(ValueError): plan.build(tmp_path/'release', 'linux-amd64')
            with pytest.raises(ValueError): plan.provision()
        else:
            with pytest.raises(ValueError):
                MCPConfigurationConversion(saved['directory'], digest=saved['sha256'], fence=fence,
                    vault=local_vault(tmp_path), targets=targets, environments=env)
        assert not (tmp_path/'release').exists()
    finally:
        fence.close()


@asynccontextmanager
async def serve(server):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); sock.listen()
        uv = uvicorn.Server(uvicorn.Config(server.http_app(), log_level='critical', lifespan='on'))
        task = asyncio.create_task(uv.serve(sockets=[sock]))
        try:
            for _ in range(100):
                if uv.started: break
                await asyncio.sleep(.01)
            assert uv.started
            yield f'http://127.0.0.1:{sock.getsockname()[1]}/mcp'
        finally:
            uv.should_exit = True
            await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_http_configuration_reuses_real_transport_without_synthetic_credentials(legacy, tmp_path):
    settings, manager, gateway = original(legacy, tmp_path)
    server = FastMCP('http')
    @server.tool
    def echo(text: str) -> dict:
        """Echo through the selected HTTP service."""
        return {'text': text}
    async with serve(server) as url:
        instance = MCPServerInstance(MCPServerConfig(name='remote', type='http', uri=url))
        manager.instances['remote'] = instance
        assert await instance.start()
        await manager._mount_to_gateway('remote', instance)
        try:
            result = await gateway.export_migration_configuration('http', {'remote': []}, ['mcp'])
            assert result['success'], result
            legacy['mcp_configuration_file'] = result['source']
            fence, saved = backup(legacy, tmp_path)
            try:
                selected_vault = local_vault(tmp_path)
                for invalid in (12, None, 'https://api.test:secret-port/mcp', 'https://user:secret@api.test/mcp'):
                    with pytest.raises(ValueError) as error:
                        MCPConfigurationConversion(saved['directory'], digest=saved['sha256'], fence=fence,
                            vault=selected_vault,
                            targets={'remote': {'url': invalid}}, environments={})
                    assert 'secret' not in str(error.value)
                plan = MCPConfigurationConversion(saved['directory'], digest=saved['sha256'], fence=fence,
                    vault=selected_vault, targets={'remote': {'url': url}}, environments={})
                plan.provision()
                described = plan.describe()
                assert described['credentials'] == {}
                host = ScopedMCP(described['contract']['exports'], config(described['values']['mcp']['servers']))
                await host.start()
                try:
                    assert await host.call('remote_echo', {'text': 'hello'}) == {'text': 'hello'}
                finally:
                    await host.close()
            finally:
                fence.close()
        finally:
            await instance.stop()
