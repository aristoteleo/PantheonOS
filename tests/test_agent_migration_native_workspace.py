"""Imported Agent history uses retained workspace through ordinary native Apps.

The owner harness imports at the prepared/start boundary. This is local POSIX
acceptance, not a shipped migration UI or cross-node environment relocation.
Only model responses are fixtures; Fleet, Agent, Files and Shell are packaged.
"""
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path

import nats
import pytest

from pantheon.apps.builtin.file.build_managed import build as build_files
from pantheon.apps.local_agent import native_platform
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_import import import_backup
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from test_agent_application import TEMPLATE
from test_agent_migration import legacy
from test_agent_migration_backup import user_tree
from test_agent_release import release
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile import settle
from test_local_profile_agent import product_configuration


@pytest.mark.asyncio
async def test_imported_agent_native_tools_keep_original_workspace_after_reopen(
        tmp_path, legacy, binaries, release, model_endpoint, monkeypatch):
    files = tmp_path/'files'
    await asyncio.to_thread(build_files, files, native_platform())
    workspace = Path(legacy['projects'][0]['path'])
    artifact = workspace/'retained.txt'
    artifact.write_text('BEFORE_MIGRATION\n')
    artifact_inode = artifact.stat().st_ino
    original = workspace/'original.txt'
    original.write_text('ORIGINAL_WORKSPACE\n')
    selected = {}

    def configure(setup):
        agent = setup['agent']
        agent.update({key: legacy[key] for key in ('projects', 'active_project', 'default_project')})
        selected.update(agent)
        setup['providers']['files'] = {'scope': 'shared-files', 'bindings': {},
            'components': {'backend': {'values': {'files': {'workspace': {'$local': 'workspace'}}}}}}

    _, _, bundled, spec = await product_configuration(tmp_path, binaries, release,
        model_endpoint, monkeypatch, provider_packages={'files': files}, configure=configure)
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': ['shell'], 'model': 'normal'}]}
    config = Path(legacy['project_config'])
    (config/'settings.json').write_text(json.dumps(selected['settings']))
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(legacy['home_memory'])/name
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = template
        value['extra_data']['project'] = legacy['projects'][0]
        path.write_text(json.dumps(value))
    old_config = user_tree(config)
    imported = {}
    logical_id = None
    with ExitStack() as owners:
        for cycle in (1, 2):
            async with LocalFleet(tmp_path/'profile', bundled, workspace=workspace) as runtime:
                info, children = runtime.coordinates, list(runtime._children)
                nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                    inbox_prefix=('_INBOX_' + info.fleet_id).encode())
                resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id,
                    str(workspace), connection=nc)
                session = LocalAppProfile(runtime, spec, resolver)
                start = session.deploy.starter.start

                async def import_before_start(**kwargs):
                    consumer = kwargs['consumer']
                    if not imported and consumer['revision'] == spec['packages']['agent']['revision']:
                        state = await session.wire.status(info.node_id)
                        assert state['instances'][consumer['instance_id']]['state'] == 'prepared'
                        root = runtime.root/'node/apps'/info.fleet_id/'data'/consumer['instance_id']/'agent'
                        assert not root.exists(), 'Migration must precede any Agent data initialization'
                        guard = owners.enter_context(fence_legacy(legacy, operation='native-workspace-import',
                            target=root, namespace=selected['namespace']))
                        backup = backup_legacy(legacy, fence=guard, directory=tmp_path/'backup')
                        receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=guard)
                        imported.update(root=root, receipt=receipt)
                    return await start(**kwargs)

                session.deploy.starter.start = import_before_start
                try:
                    assert (await settle(session, 'advance'))['state'] == 'ready'
                    agent = await session.bind_rpc('agent', 'agent')
                    files_rpc = await session.bind_rpc('files', 'file-manager')

                    async def invoke(method, **args):
                        result = await agent(method, args, 60)
                        assert result.get('success') is not False and 'error' not in result, result
                        return result

                    members = (await invoke('get_agents', chat_id='chat-b'))['agents']
                    current = members[0]['instance']['instance_id']
                    expected = next(m['instance_id'] for m in imported['receipt']['members']
                                    if m['conversation_id'] == 'chat-b')
                    assert current == expected and logical_id in (None, current)
                    logical_id = current
                    # Relative paths must reach the original workspace, not the
                    # Agent's package, data directory or migration copy.
                    model_endpoint.tool_command = (
                        "cat original.txt; printf 'NATIVE_TURN_%s\\n' " + str(cycle) + " >> retained.txt; cat retained.txt")
                    await invoke('chat', chat_id='chat-b',
                        message=[{'role': 'user', 'content': 'workspace turn ' + str(cycle)}])
                    read = await files_rpc('read_file', {'file_path': 'retained.txt'}, 30)
                    assert 'BEFORE_MIGRATION' in json.dumps(read) and 'NATIVE_TURN_' + str(cycle) in json.dumps(read)
                    snapshot = await invoke('open_agent_history', chat_id='chat-b')
                    history = await invoke('read_agent_history', chat_id='chat-b',
                        snapshot_id=snapshot['snapshot_id'], part=0)
                    for marker in ('saved answer', 'workspace turn 1', 'ORIGINAL_WORKSPACE', 'NATIVE_TURN_' + str(cycle)):
                        assert marker in history['json_fragment']
                    await invoke('release_agent_history', chat_id='chat-b', snapshot_id=snapshot['snapshot_id'])
                    assert (await settle(session, 'stop'))['state'] == 'stopped'
                finally:
                    await resolver.close()
            assert_stopped(children, info)
            assert artifact.stat().st_ino == artifact_inode
            assert artifact.read_text() == 'BEFORE_MIGRATION\n' + ''.join(
                'NATIVE_TURN_' + str(i) + '\n' for i in range(1, cycle + 1))
            assert original.read_text() == 'ORIGINAL_WORKSPACE\n'
            assert user_tree(config) == old_config
            assert not list(imported['root'].rglob('retained.txt'))
    assert len([row for row in model_endpoint.requests if row[0] == '/v1/chat/completions']) == 4
