"""Imported Agent history uses retained workspace through ordinary native Apps.

The owner harness imports at the prepared/start boundary. This is local POSIX
acceptance, not a shipped migration UI or cross-node environment relocation.
Only model responses are fixtures; Fleet, Agent, Files and Shell are packaged.
"""
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys

import nats
import pytest

from pantheon.apps.builtin.file.build_managed import build as build_files
from pantheon.apps.local_agent import native_platform
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_import import import_backup
from pantheon.chatroom.migration_workspaces import RetainedWorkspaceConversion
from pantheon.chatroom.package import build_package
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
@pytest.mark.parametrize('retain_environment', [False, True])
async def test_imported_agent_native_tools_keep_original_workspace_after_reopen(
        tmp_path, legacy, binaries, release, model_endpoint, monkeypatch, retain_environment):
    files = tmp_path/'files'
    await asyncio.to_thread(build_files, files, native_platform())
    # The GUI's direct Files grant is a declared startup dependency. Build a
    # paired release that declares it, rather than patching an installed App.
    agent_package = await asyncio.to_thread(build_package, tmp_path/'workspace-agent', native_platform(),
        version='0.7.0', frontend=release[0]/'frontend',
        transport=release[0]/'backend/_vendor/pantheon/models/fleet-app-transport', dependencies={
            'shell': {'range': '^0.6.0', 'uses': ['shell@1'], 'binding': 'runtime'},
            'file-manager': {'range': '^0.6.0', 'uses': ['fs@1']}})
    release = agent_package, release[1]
    workspace = Path(legacy['projects'][0]['path'])
    config = Path(legacy['project_config'])
    retained_roots = []
    if retain_environment:
        environment = config/'brain/chat-b/environment'
        # A real pre-existing venv with its own module, not the App interpreter.
        # Retention must keep its absolute interpreter links usable in place.
        await asyncio.to_thread(subprocess.run, [sys.executable, '-m', 'venv', '--without-pip', str(environment)],
                                check=True, capture_output=True, timeout=30)
        library = await asyncio.to_thread(subprocess.check_output, [str(environment/'bin/python'), '-c',
            'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True, timeout=10)
        (Path(library.strip())/'retained_fixture.py').write_text('VALUE = "RETAINED_ENVIRONMENT"\n')
        script = environment/'run.sh'
        script.write_text('#!/bin/sh\n.pantheon/brain/chat-b/environment/bin/python -B -c '
                          "'import retained_fixture; print(retained_fixture.VALUE)'\n")
        script.chmod(0o700)
        (environment/'current').symlink_to('run.sh')
        (environment.parent/'task_state.json').write_text(json.dumps({'state': {'task_dirs': {'job': str(environment)}}}))
        (config/'workspaces').mkdir()
        retained_roots = [str(environment), str(config/'workspaces')]
    artifact_path = '.pantheon/workspaces/retained.txt' if retain_environment else 'retained.txt'
    artifact = workspace/artifact_path
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
        profiles = agent['dependencies']['profiles']['toolsets']
        profiles['shell']['provider'] = {'$app': 'shell', 'component': 'backend', 'port': 'http'}
        profiles['file_manager'] = {'alias': 'files', 'provider': {
            '$app': 'files', 'component': 'backend', 'port': 'http'}, 'functions': [{
                'name': 'read_file', 'description': 'Read a workspace file', 'parameters': {
                    'type': 'object', 'properties': {'file_path': {'type': 'string'}},
                    'required': ['file_path'], 'additionalProperties': False}}]}
        files_policy = {'app_id': 'file-manager', 'provider': {
            '$app': 'files', 'component': 'backend', 'port': 'http'},
            'methods': {'read_file': {'arguments': ['file_path'], 'bound': {}}}}
        setup['tools']['files'] = files_policy
        setup['extra_bindings'] = {'files': {**files_policy, 'component': 'backend'}}
        agent['view_dependencies'] = {legacy['projects'][0]['id']: {'toolsets': {
            'file_manager': {'credential': 'files', 'profile': 'file_manager'}}}}

    _, _, bundled, spec = await product_configuration(tmp_path, binaries, release,
        model_endpoint, monkeypatch, provider_packages={'files': files}, configure=configure)
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': ['shell'], 'model': 'normal'}]}
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
                        conversion = None
                        if retain_environment:
                            profiles = kwargs['components']['backend']['values']['agent']['dependencies']['profiles']['toolsets']
                            conversion = RetainedWorkspaceConversion(backup['directory'], digest=backup['sha256'],
                                fence=guard, owner=info.fleet_id, source_node_id=info.node_id, roots=retained_roots,
                                providers={name: {key: profiles[name][key] for key in ('alias', 'provider')}
                                           for name in ('file_manager', 'shell')})
                        receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=guard,
                                                retained_workspaces=conversion)
                        imported.update(root=root, receipt=receipt, conversion=conversion, fence=guard, backup=backup)
                    return await start(**kwargs)

                session.deploy.starter.start = import_before_start
                try:
                    assert (await settle(session, 'advance'))['state'] == 'ready'
                    agent = await session.bind_rpc('agent', 'agent')
                    files_rpc = await session.bind_rpc('files', 'file-manager')

                    async def invoke(rpc_method, **args):
                        result = await agent(rpc_method, args, 60)
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
                        ('.pantheon/brain/chat-b/environment/current; ' if retain_environment else '') +
                        "cat original.txt; printf 'NATIVE_TURN_%s\\n' " + str(cycle) +
                        ' >> ' + artifact_path + '; cat ' + artifact_path)
                    await invoke('chat', chat_id='chat-b',
                        message=[{'role': 'user', 'content': 'workspace turn ' + str(cycle)}])
                    read = await files_rpc('read_file', {'file_path': artifact_path}, 30)
                    assert 'BEFORE_MIGRATION' in json.dumps(read) and 'NATIVE_TURN_' + str(cycle) in json.dumps(read)
                    viewed = await invoke('call_view_service', workspace_path=str(workspace), service='file_manager',
                                          method='read_file', args={'file_path': artifact_path})
                    assert 'NATIVE_TURN_' + str(cycle) in json.dumps(viewed)
                    snapshot = await invoke('open_agent_history', chat_id='chat-b')
                    history = await invoke('read_agent_history', chat_id='chat-b',
                        snapshot_id=snapshot['snapshot_id'], part=0)
                    for marker in ('saved answer', 'workspace turn 1', 'ORIGINAL_WORKSPACE', 'NATIVE_TURN_' + str(cycle)):
                        assert marker in history['json_fragment']
                    if retain_environment:
                        assert 'RETAINED_ENVIRONMENT' in history['json_fragment']
                        assert not (imported['root']/'configuration/.pantheon/brain/chat-b/environment').exists()
                        assert (imported['root']/'configuration/.pantheon/brain/chat-b/task_state.json').exists()
                    await invoke('release_agent_history', chat_id='chat-b', snapshot_id=snapshot['snapshot_id'])
                    assert (await settle(session, 'stop'))['state'] == 'stopped'
                finally:
                    await resolver.close()
            assert_stopped(children, info)
            assert artifact.stat().st_ino == artifact_inode
            assert artifact.read_text() == 'BEFORE_MIGRATION\n' + ''.join(
                'NATIVE_TURN_' + str(i) + '\n' for i in range(1, cycle + 1))
            assert original.read_text() == 'ORIGINAL_WORKSPACE\n'
            after = user_tree(config)
            if retain_environment:
                assert after.pop('workspaces/retained.txt') == artifact.read_bytes()
                assert after == {key: value for key, value in old_config.items() if key != 'workspaces/retained.txt'}
                backup = imported['backup']
                assert import_backup(backup['directory'], digest=backup['sha256'], fence=imported['fence'],
                    retained_workspaces=imported['conversion']) == imported['receipt']
            else:
                assert after == old_config
            assert not list(imported['root'].rglob('retained.txt'))
    assert len([row for row in model_endpoint.requests if row[0] == '/v1/chat/completions']) == 4
