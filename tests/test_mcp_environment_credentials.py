"""Backed-up MCP keys reach the existing Fleet vault and a real stdio child.

All data is synthetic and the Fleet root is isolated; no installed node or
external API is used. Full Agent/MCP configuration migration is a separate gate.
"""
import json
from pathlib import Path
import sys

from fastmcp import Client
import pytest

from pantheon.apps.builtin.mcp.scoped import ScopedMCP
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_mcp_credentials import MCPEnvironmentConversion
from pantheon.models.credentials import LocalModelCredentialVault
from test_agent_migration import legacy
from test_agent_migration_credentials import vault, read_key
from test_scoped_mcp_app import config, contract, mcp


def stage(legacy, tmp_path, *, variable='synthetic-mcp-key'):
    user = Path(legacy['global_config']) / 'mcp.json'
    user.write_text(json.dumps({'servers': {'docs': {'type': 'stdio',
        'command': 'python server.py', 'env': {'MCP_API_KEY': 'overwritten-key', 'MODE': 'read'}}}}))
    project = Path(legacy['project_config']) / 'mcp.json'
    project.write_text(json.dumps({'servers': {'docs': {'env': {'MCP_API_KEY': variable}}}}))
    fence = fence_legacy(legacy, operation='mcp-migration', target=tmp_path/'target', namespace='mcp-test')
    backup = backup_legacy(legacy, fence=fence, directory=tmp_path/'backup')
    selection = {'docs': {'literals': ['MODE'], 'credentials': {'MCP_API_KEY': {
        'source': str(project), 'alias': 'mcp-api', 'endpoint': 'https://api.test/v1',
        'ref': 'node-secret://mcp-api'}}}}
    return fence, backup, selection


def convert(backup, fence, selection, vault):
    return MCPEnvironmentConversion(backup['directory'], digest=backup['sha256'], fence=fence,
                                    vault=vault, servers=selection)


def local_vault(tmp_path):
    # Parsing-only tests must never execute this path or touch a real node.
    root = tmp_path/'unused'
    root.mkdir(mode=0o700)
    (root/'node_id').write_text('node\n')
    return LocalModelCredentialVault('/not-executed/fleet', state_dir=root, owner='owner', node_id='node')


@pytest.mark.asyncio
async def test_literal_secret_conversion_provisions_existing_vault_and_stdio_receives_only_its_env(
        legacy, tmp_path, vault, monkeypatch):
    fence, backup, selection = stage(legacy, tmp_path)
    try:
        conversion = convert(backup, fence, selection, vault)
        description = conversion.describe()
        assert 'synthetic-mcp-key' not in json.dumps(description)
        assert 'overwritten-key' not in json.dumps(description)
        assert description['servers']['docs']['env'] == {'MODE': 'read'}
        assert description['servers']['docs']['env_credentials'] == {'MCP_API_KEY': {
            'credential': 'mcp-api', 'endpoint': 'https://api.test/v1'}}
        conversion.provision()
        conversion.provision()  # Resume does not rotate the stored secret.
        assert read_key(vault, 'node-secret://mcp-api', 'https://api.test/v1') == 'synthetic-mcp-key'
        assert not (tmp_path/'target').exists()

        server = tmp_path/'server.py'
        server.write_text('''from fastmcp import FastMCP
import os
mcp = FastMCP("private env")
@mcp.tool
def check() -> dict:
    return {"credential_matches": os.environ.get("MCP_API_KEY") == "synthetic-mcp-key",
            "mode": os.environ.get("MODE"),
            "ambient": any(k in os.environ for k in ("FLEET_KEY", "AMBIENT_KEY", "PANTHEON_APP_CONFIG"))}
mcp.run(transport="stdio", show_banner=False)
''')
        # Obtain the schema without giving that review process any credential.
        from fastmcp.client.transports import StdioTransport
        async with Client(StdioTransport(sys.executable, [str(server)], cwd=str(tmp_path), env={})) as reviewer:
            tool = (await reviewer.list_tools())[0]
        exports = {'check': {'server': 'docs', 'tool': tool.name, 'description': tool.description or '',
                             'parameters': tool.inputSchema}}
        value = {'transport': 'stdio', 'command': [sys.executable, str(server)], 'cwd': str(tmp_path),
                 **description['servers']['docs']}
        monkeypatch.setenv('FLEET_KEY', 'must-not-inherit-owner-key')
        monkeypatch.setenv('AMBIENT_KEY', 'must-not-inherit-other-key')
        monkeypatch.setenv('PANTHEON_APP_CONFIG', '/must-not-inherit-config')
        credentials = {'mcp-api': RuntimeCredential('https://api.test/v1',
            read_key(vault, 'node-secret://mcp-api', 'https://api.test/v1'))}
        host = ScopedMCP(exports, config({'docs': value}, credentials))
        await host.start()
        try:
            assert (await host.call('check', {}))['structuredContent'] == {
                'credential_matches': True, 'mode': 'read', 'ambient': False}
            assert value['env'] == {'MODE': 'read'}  # Constructor did not contaminate prepared values.
        finally:
            await host.close()
    finally:
        fence.close()


@pytest.mark.parametrize('change', ['wrong-source', 'missing-field', 'extra-field', 'ambiguous-field',
    'duplicate-ref', 'bad-endpoint', 'bad-port', 'bad-alias', 'reference', 'empty-secret', 'unsupported-secret'])
def test_conversion_rejects_incomplete_or_unresolved_sources_before_writing(legacy, tmp_path, change):
    value = '${AMBIENT_MCP_KEY}' if change == 'reference' else '' if change == 'empty-secret' else (
        'contains a space' if change == 'unsupported-secret' else 'synthetic-mcp-key')
    fence, backup, selection = stage(legacy, tmp_path, variable=value)
    try:
        entry = selection['docs']['credentials']['MCP_API_KEY']
        if change == 'wrong-source': entry['source'] = str(Path(legacy['global_config'])/'mcp.json')
        elif change == 'missing-field': selection['docs']['literals'] = []
        elif change == 'extra-field': selection['docs']['literals'].append('ABSENT')
        elif change == 'ambiguous-field': selection['docs']['literals'].append('MCP_API_KEY')
        elif change == 'duplicate-ref':
            selection['docs']['literals'] = []
            selection['docs']['credentials']['MODE'] = {**entry, 'alias': 'mode',
                'source': str(Path(legacy['global_config'])/'mcp.json')}
        elif change == 'bad-endpoint': entry['endpoint'] = 'https://api.test/?key=private-value'
        elif change == 'bad-port': entry['endpoint'] = 'https://api.test:private-value/v1'
        elif change == 'bad-alias': entry['alias'] = 'bad alias'
        with pytest.raises(ValueError) as error:
            convert(backup, fence, selection, local_vault(tmp_path))
        assert not any(secret in str(error.value) for secret in ('synthetic-mcp-key', 'private-value', 'overwritten-key'))
        assert not (tmp_path/'unused'/'apps').exists() and not (tmp_path/'target').exists()
    finally:
        fence.close()


@pytest.mark.parametrize('kind', ['missing', 'http'])
def test_transport_requires_backed_up_stdio_declaration(legacy, tmp_path, kind):
    user = Path(legacy['global_config']) / 'mcp.json'
    fence, backup, selection = stage(legacy, tmp_path)
    fence.close()
    # Make a fresh valid snapshot with a different source; never modify a
    # private backup and pretend its digest still authenticates those bytes.
    value = json.loads(user.read_text())
    if kind == 'missing': del value['servers']['docs']['type']
    else: value['servers']['docs']['type'] = 'http'
    user.write_text(json.dumps(value))
    fence = fence_legacy(legacy, operation='mcp-migration', target=tmp_path/'target', namespace='mcp-test')
    try:
        backup = backup_legacy(legacy, fence=fence, directory=tmp_path/'new-backup')
        with pytest.raises(ValueError, match='environment selections'):
            convert(backup, fence, selection, local_vault(tmp_path))
    finally:
        fence.close()


def test_source_change_prevents_vault_write(legacy, tmp_path):
    fence, backup, selection = stage(legacy, tmp_path)
    try:
        conversion = convert(backup, fence, selection, local_vault(tmp_path))
        (Path(legacy['project_config'])/'mcp.json').write_text('{}')
        with pytest.raises(ValueError, match='changed after backup'):
            conversion.provision()
        assert not (tmp_path/'unused'/'apps').exists()
    finally:
        fence.close()


def test_existing_credential_conflict_never_rotates(legacy, tmp_path, vault):
    fence, backup, selection = stage(legacy, tmp_path)
    try:
        vault.ensure('node-secret://mcp-api', 'https://api.test/v1', 'already-used-key')
        conversion = convert(backup, fence, selection, vault)
        with pytest.raises(ValueError, match='conflicts'):
            conversion.provision()
        assert read_key(vault, 'node-secret://mcp-api', 'https://api.test/v1') == 'already-used-key'
    finally:
        fence.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['endpoint', 'overlap', 'missing-slot', 'unused-slot', 'invalid-name', 'nul', 'inline-key'])
async def test_prepared_secret_bindings_fail_before_start(mcp, tmp_path, change):
    server, _ = mcp
    exports = await contract(server)
    spec = {'transport': 'stdio', 'command': [sys.executable, 'unused.py'], 'cwd': str(tmp_path), 'env': {},
            'env_credentials': {'API_KEY': {'credential': 'api', 'endpoint': 'https://api.test/v1'}}}
    credentials = {'api': RuntimeCredential('https://api.test/v1', 'synthetic-key')}
    if change == 'endpoint': credentials['api'] = RuntimeCredential('https://other.test/v1', 'synthetic-key')
    elif change == 'overlap': spec['env']['API_KEY'] = 'inline-value'
    elif change == 'missing-slot': credentials = {'other': credentials['api']}
    elif change == 'unused-slot': credentials['other'] = credentials['api']
    elif change == 'invalid-name': spec['env_credentials']['INVALID-NAME'] = spec['env_credentials'].pop('API_KEY')
    elif change == 'nul': credentials['api'] = RuntimeCredential('https://api.test/v1', 'bad\0key')
    else: spec['env_credentials']['API_KEY']['key'] = 'do-not-print'
    called = []
    with pytest.raises(ValueError, match='prepared MCP') as error:
        ScopedMCP(exports, config({'docs': spec}, credentials), client_factory=lambda _: called.append(True))
    assert not called and 'do-not-print' not in str(error.value)
