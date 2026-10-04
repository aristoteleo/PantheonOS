"""Original MCP launch env survives capture/backup/vault without ambient lookup."""
import json
import os
from pathlib import Path
import shlex
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
import pytest

from pantheon.apps.builtin.mcp import MCPGatewayToolSet
from pantheon.apps.builtin.mcp.manager import MCPManager, MCPServerConfig, MCPServerInstance
from pantheon.apps.builtin.mcp.scoped import ScopedMCP
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.chatroom.migration import inspect_legacy, fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_mcp_handoff import export_mcp_handoff
from pantheon.chatroom.migration_mcp_credentials import MCPEnvironmentConversion
from pantheon.chatroom.migration_import import import_backup
from pantheon.settings import Settings
from test_agent_migration import legacy
from test_agent_migration_credentials import vault, read_key
from test_mcp_environment_credentials import local_vault
from test_scoped_mcp_app import config


def original(spec, tmp_path):
    project = Path(spec['project_config'])
    (project/'settings.json').write_text('{}')
    env = {'MCP_KEY': '${UPSTREAM_KEY}', 'MODE': 'read'}
    (project/'mcp.json').write_text(json.dumps({'servers': {'docs': {'type': 'stdio',
        'command': 'python server.py', 'env': env}}}))
    settings = Settings(project.parent, user_home=Path(spec['global_config']), isolated_env=True, environment={})
    manager = MCPManager(log_dir=str(tmp_path/'logs'), config_path=project/'mcp.json')
    instance = MCPServerInstance(MCPServerConfig(name='docs', type='stdio', command='python server.py', env=env))
    # A saved transport is enough for parser tests. The acceptance test below
    # uses the real legacy start path and completes a real MCP handshake.
    instance.stdio_transport = StdioTransport(command=sys.executable, args=[],
        env={'MCP_KEY': 'original-key', 'MODE': 'read', 'INHERITED': 'original-inherited',
             'FLEET_KEY': 'owner-must-not-copy'})
    instance.status = 'running'
    manager.instances['docs'] = instance
    return settings, manager, instance


def capture(spec, settings, manager, *, extra=None):
    result = export_mcp_handoff(settings, manager, operation_id='original', servers={'docs': extra or []})
    spec['mcp_environment_file'] = result['source']
    return Path(result['source']), result


def selection(source, extra=None):
    return {'docs': {'literals': ['MODE', *(extra or [])], 'credentials': {'MCP_KEY': {
        'source': str(source), 'alias': 'mcp-key', 'ref': 'node-secret://mcp-key',
        'endpoint': 'https://models.test/v1'}}}}


def conversion(spec, tmp_path, source, vault, *, extra=None):
    fence = fence_legacy(spec, operation='capture', target=tmp_path/'target', namespace='mcp')
    try:
        backup = backup_legacy(spec, fence=fence, directory=tmp_path/'backup')
        value = MCPEnvironmentConversion(backup['directory'], digest=backup['sha256'],
            fence=fence, vault=vault, servers=selection(source, extra))
        return fence, backup, value
    except BaseException:
        fence.close()
        raise


@pytest.mark.asyncio
async def test_original_stdio_child_to_captured_env_to_vault_to_prepared_child(legacy, tmp_path, vault, monkeypatch):
    settings, manager, instance = original(legacy, tmp_path)
    server = tmp_path/'server.py'
    server.write_text('''from fastmcp import FastMCP
import os
mcp = FastMCP("handoff")
@mcp.tool
def check() -> dict:
    return {"key_matches": os.environ.get("MCP_KEY") == "key-at-launch",
            "mode": os.environ.get("MODE"), "inherited": os.environ.get("INHERITED"),
            "owner_present": "FLEET_KEY" in os.environ}
mcp.run(transport="stdio", show_banner=False)
''')
    instance.config.command = shlex.join([sys.executable, str(server)])
    instance.status = 'stopped'
    instance.stdio_transport = None
    monkeypatch.setenv('UPSTREAM_KEY', 'key-at-launch')
    monkeypatch.setenv('INHERITED', 'inherited-at-launch')
    monkeypatch.setenv('FLEET_KEY', 'owner-must-not-copy')
    assert await instance.start()
    try:
        async with instance.stdio_client as client:
            tools = await client.list_tools()
            instance.status = 'running'
            before = (await client.call_tool('check')).structured_content
            assert before == {'key_matches': True, 'mode': 'read',
                              'inherited': 'inherited-at-launch', 'owner_present': True}
            monkeypatch.setenv('UPSTREAM_KEY', 'different-current-key')
            monkeypatch.setenv('MCP_KEY', 'migrator-key')
            monkeypatch.setenv('INHERITED', 'different-current-value')
            gateway = object.__new__(MCPGatewayToolSet)
            gateway._manager, gateway._migration_settings = manager, settings
            result = await gateway.export_migration_environment('real-child', {'docs': ['INHERITED']})
            assert result['success']
            legacy['mcp_environment_file'] = result['source']
            source = Path(result['source'])
    finally:
        await instance.stop()
    assert not source.stat().st_mode & 0o077
    assert 'key-at-launch' not in json.dumps(result)
    assert 'FLEET_KEY' not in source.read_text() and 'owner-must-not-copy' not in source.read_text()
    fence, backup, plan = conversion(legacy, tmp_path, source, vault, extra=['INHERITED'])
    try:
        plan.provision()
        plan.provision()
        described = plan.describe()
        assert not any(key in json.dumps(described) for key in ('key-at-launch', 'different-current-key', 'migrator-key'))
        item = tools[0]
        exports = {'check': {'server': 'docs', 'tool': item.name, 'description': item.description or '',
                             'parameters': item.inputSchema}}
        prepared = {'transport': 'stdio', 'command': [sys.executable, str(server)], 'cwd': str(tmp_path),
                    **described['servers']['docs']}
        host = ScopedMCP(exports, config({'docs': prepared}, {'mcp-key': RuntimeCredential(
            'https://models.test/v1', read_key(vault, 'node-secret://mcp-key', 'https://models.test/v1'))}))
        await host.start()
        try:
            assert (await host.call('check', {}))['structuredContent'] == {**before, 'owner_present': False}
        finally:
            await host.close()
        # Environment conversion alone does not authorize importing MCP config
        # or replacing the entire Agent deployment.
        with pytest.raises(ValueError, match='explicit converter'):
            import_backup(backup['directory'], digest=backup['sha256'], fence=fence)
        assert not (tmp_path/'target').exists()
    finally:
        fence.close()


def test_inventory_never_reads_or_hashes_private_capture(legacy, tmp_path, monkeypatch):
    settings, manager, _ = original(legacy, tmp_path)
    source, result = capture(legacy, settings, manager)
    actual_open = os.open
    def guarded(path, *args, **kwargs):
        if Path(path) == source:
            raise AssertionError('Dry run opened secret environment capture')
        return actual_open(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', guarded)
    inventory = inspect_legacy(**legacy)
    assert inventory['mcp_environment'] == {'source': str(source), 'exists': True}
    assert not any(item['source'] == str(source) for item in inventory['files'])
    assert 'original-key' not in json.dumps(result) + json.dumps(inventory)


@pytest.mark.parametrize('change', ['stopped', 'starting', 'missing', 'http', 'scope', 'invalid-name', 'too-many', 'bad-value'])
def test_no_launch_or_file_when_original_environment_unavailable(legacy, tmp_path, change):
    settings, manager, instance = original(legacy, tmp_path)
    servers = {'docs': []}
    if change in ('stopped', 'starting'): instance.status = change
    elif change == 'missing': instance.stdio_transport = None
    elif change == 'http': instance.config.type = 'http'
    elif change == 'scope': manager._config_path = tmp_path/'other/mcp.json'
    elif change == 'invalid-name': servers['docs'] = ['NOT A VARIABLE']
    elif change == 'too-many': servers['docs'] = ['VAR_' + str(i) for i in range(65)]
    elif change == 'bad-value': instance.stdio_transport.env['MCP_KEY'] = 'private\0key'
    with pytest.raises(ValueError) as error:
        export_mcp_handoff(settings, manager, operation_id='failed', servers=servers)
    assert 'private' not in str(error.value) and 'original-key' not in str(error.value)
    assert not (settings.user_home/'fleet-node').exists()


def test_capture_is_immutable_and_idempotent(legacy, tmp_path):
    settings, manager, instance = original(legacy, tmp_path)
    source, first = capture(legacy, settings, manager)
    assert capture(legacy, settings, manager)[1] == first
    old = source.read_bytes()
    instance.stdio_transport.env['MCP_KEY'] = 'different-key'
    with pytest.raises(ValueError, match='new operation'):
        capture(legacy, settings, manager)
    assert source.read_bytes() == old


def test_original_factory_fields_and_explicit_inherited_values_are_convertible(legacy, tmp_path):
    settings, manager, _ = original(legacy, tmp_path)
    # No overrides on disk: the gateway's live effective config may contain
    # factory defaults. The migrator must not load its own factory defaults.
    (Path(legacy['project_config'])/'mcp.json').unlink()
    source, _ = capture(legacy, settings, manager, extra=['INHERITED'])
    fence, _, plan = conversion(legacy, tmp_path, source, local_vault(tmp_path), extra=['INHERITED'])
    try:
        assert plan.describe()['servers']['docs']['env'] == {'MODE': 'read', 'INHERITED': 'original-inherited'}
        assert plan.describe()['sources'][0]['source'] == str(source)
    finally:
        fence.close()


def test_changed_handoff_after_backup_cannot_write_vault(legacy, tmp_path):
    settings, manager, _ = original(legacy, tmp_path)
    source, _ = capture(legacy, settings, manager)
    fence, _, plan = conversion(legacy, tmp_path, source, local_vault(tmp_path))
    try:
        source.write_text('{}')
        with pytest.raises(ValueError, match='changed after backup'):
            plan.provision()
        assert not (tmp_path/'unused/apps').exists()
    finally:
        fence.close()


@pytest.mark.asyncio
async def test_legacy_export_failure_is_sanitized_and_not_an_agent_tool(legacy, tmp_path):
    settings, manager, instance = original(legacy, tmp_path)
    gateway = object.__new__(MCPGatewayToolSet)
    gateway._manager, gateway._migration_settings = manager, settings
    instance.status = 'stopped'
    result = await gateway.export_migration_environment('failure', {'docs': []})
    assert result['success'] is False and 'original-key' not in json.dumps(result)
    assert MCPGatewayToolSet.export_migration_environment._exclude is True
    from pantheon.apps.registry import reflected_tools
    from pantheon.apps.schema import parse_manifest
    from pantheon.apps.reflect import signature_diff
    manifest = parse_manifest(json.loads((Path(__file__).parents[1]/'apps/mcp/app.json').read_text()))
    assert signature_diff(manifest.provides.tools, reflected_tools(manifest)) == []


@pytest.mark.parametrize('change', ['project-scope', 'kind', 'extra-field', 'missing-declaration', 'absent', 'declaration-mismatch'])
def test_malformed_or_inconsistent_capture_blocks_before_vault_writes(legacy, tmp_path, change):
    settings, manager, _ = original(legacy, tmp_path)
    source, _ = capture(legacy, settings, manager)
    value = json.loads(source.read_text())
    row = value['servers']['docs']
    if change == 'project-scope': value['project_config'] = str(tmp_path/'other')
    elif change == 'kind': value['kind'] = 'agent-model-environment'
    elif change == 'extra-field': value['private-key'] = 'must-not-return'
    elif change == 'missing-declaration': del row['values']['MCP_KEY']
    elif change == 'absent': row['values']['MCP_KEY'] = None
    else: row['declarations']['MODE'] = 'different'
    source.write_text(json.dumps(value))  # Alter input before taking a fresh valid backup.
    with pytest.raises(ValueError) as error:
        conversion(legacy, tmp_path, source, local_vault(tmp_path))
    assert not any(s in str(error.value) for s in ('original-key', 'must-not-return'))
    assert not (tmp_path/'unused/apps').exists() and not (tmp_path/'target').exists()


@pytest.mark.parametrize('change', ['public', 'symlink', 'history', 'same-as-model'])
def test_capture_requires_private_distinct_non_agent_data_path(legacy, tmp_path, change):
    settings, manager, _ = original(legacy, tmp_path)
    source, _ = capture(legacy, settings, manager)
    if change == 'public': source.chmod(0o644)
    elif change == 'symlink':
        link = tmp_path/'linked.json'; link.symlink_to(source)
        legacy['mcp_environment_file'] = str(link)
    elif change == 'history':
        moved = Path(legacy['home_memory'])/'private.json'; source.rename(moved)
        legacy['mcp_environment_file'] = str(moved)
    else: legacy['model_environment_file'] = str(source)
    with pytest.raises(ValueError): inspect_legacy(**legacy)
