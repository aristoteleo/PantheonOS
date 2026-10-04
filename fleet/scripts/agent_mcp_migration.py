"""Real legacy MCP capture and ordinary App conversion for native acceptance.

Everything lives inside the caller's isolated fixture root. The source and
relocated child are real stdio servers; only their business logic is synthetic.
"""
import json
import os
from pathlib import Path
import shlex
import sys

from pantheon.apps.builtin.mcp import MCPGatewayToolSet
from pantheon.apps.builtin.mcp.manager import MCPManager, MCPServerConfig, MCPServerInstance
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_mcp_configuration import MCPConfigurationConversion
from pantheon.models.credentials import LocalModelCredentialVault
from pantheon.settings import Settings


async def prepare(root, *, owner, platform, fences):
    project = root/'legacy-project'
    config = project/'.pantheon'
    memory = config/'memory'; memory.mkdir(parents=True)
    user = root/'legacy-user'; user.mkdir()
    (config/'settings.json').write_text('{}')
    settings = Settings(project, user_home=user, isolated_env=True, environment={})
    source = root/'original mcp'; source.mkdir()
    (source/'marker.txt').write_text('preserved MCP asset')
    script = source/'server.py'
    script.write_text('''from fastmcp import FastMCP
from pathlib import Path
import os
mcp = FastMCP("native-capture")
count = 0
@mcp.tool
def check() -> dict:
    """Inspect the shared process and reviewed launch environment."""
    global count
    count += 1
    return {"pid": os.getpid(), "count": count, "cwd": str(Path.cwd()),
            "marker": Path("marker.txt").read_text(), "mode": os.environ.get("MODE"),
            "key_matches": os.environ.get("MCP_KEY") == "native-mcp-synthetic-key",
            "owner_present": "FLEET_KEY" in os.environ}
mcp.run(transport="stdio", show_banner=False)
''')
    command = shlex.join([sys.executable, str(script)])
    env = {'MODE':'read', 'MCP_KEY':'native-mcp-synthetic-key'}
    (config/'mcp.json').write_text(json.dumps({'servers':{'docs':{
        'type':'stdio', 'command':command, 'env':env}}}))
    (config/'mcp.json').chmod(0o600)
    manager = MCPManager(log_dir=str(root/'legacy-mcp-logs'), config_path=config/'mcp.json')
    gateway = object.__new__(MCPGatewayToolSet)
    gateway._manager, gateway._migration_settings = manager, settings
    instance = MCPServerInstance(MCPServerConfig(name='docs', type='stdio', command=command, env=env))
    manager.instances['docs'] = instance
    previous = Path.cwd()
    try:
        os.chdir(source)
        assert await instance.start()
        async with instance.stdio_client as client:
            before = (await client.call_tool('check')).structured_content
            instance.status = 'running'
            await manager._mount_to_gateway('docs', instance)
            captured = await gateway.export_migration_configuration('native', {'docs':[]}, ['mcp'])
            assert captured['success'], captured
    finally:
        await instance.stop()
        os.chdir(previous)
    # Relocation is explicit; source-machine paths are never assumed portable.
    selected = root/'selected mcp'; selected.mkdir()
    (selected/'marker.txt').write_bytes((source/'marker.txt').read_bytes())
    selected_script = selected/'server.py'; selected_script.write_bytes(script.read_bytes())
    spec = dict(projects=[dict(id='legacy',name='Legacy',path=str(project))],
        active_project='legacy',default_project='legacy',home_memory=str(memory),
        global_config=str(user),project_config=str(config),mcp_configuration_file=captured['source'])
    fence = fences.enter_context(fence_legacy(spec, operation='native-mcp',
        target=root/'migration-target', namespace='native-migration'))
    saved = backup_legacy(spec, fence=fence, directory=root/'mcp-backup')
    vault = LocalModelCredentialVault(root.parent/'fleet-credential-reader',
        state_dir=root.parent/'provider-node',owner=owner,node_id='provider-node')
    plan = MCPConfigurationConversion(saved['directory'],digest=saved['sha256'],fence=fence,vault=vault,
        targets={'docs':{'command':[sys.executable,str(selected_script)],'cwd':str(selected)}},
        environments={'docs':{'literals':['MODE'],'credentials':{'MCP_KEY':{
            'source':captured['source'],'alias':'mcp-key','ref':'node-secret://native-mcp',
            'endpoint':'https://mcp-fixture.invalid/v1'}}}})
    candidate = plan.prepare_deployment(root/'mcp-provider',platform,name='mcp-provider',
        target={'node_id':'provider-node','scope':'native-mcp-provider','generation':0},
        aliases={'mcp':'mcp-shared'})
    plan.provision()
    plan.provision()
    assert env['MCP_KEY'] not in json.dumps(candidate)
    assert all(env['MCP_KEY'].encode() not in p.read_bytes()
               for p in (root/'mcp-provider').rglob('*') if p.is_file())
    expected = {k:before[k] for k in ('marker','mode','key_matches')}
    expected.update(cwd=str(selected.resolve()),owner_present=False)
    return candidate, expected
