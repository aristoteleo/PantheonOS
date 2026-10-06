"""Imported Agent history uses retained workspace through ordinary native Apps.

The product owner command imports and stops its prepared profile, then ordinary
startup reopens it. This is local POSIX acceptance, not a migration GUI or
cross-node environment relocation.
Only model responses are fixtures; Fleet, Agent, Files and Shell are packaged.
"""
import asyncio
import json
from pathlib import Path
import subprocess
import signal
import sys

import nats
import pytest

from pantheon.apps.builtin.file.build_managed import build as build_files
from pantheon.apps.local_agent import native_platform
from pantheon.apps.resolver import AppInstanceResolver
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
@pytest.mark.parametrize('retain_environment,budget_enabled,oauth_enabled', [
    (False, False, False), (True, False, False), (True, True, False), (True, False, True)])
async def test_imported_agent_native_tools_keep_original_workspace_after_reopen(
        tmp_path, legacy, binaries, release, model_endpoint, monkeypatch, retain_environment, budget_enabled, oauth_enabled):
    if oauth_enabled:
        from contextlib import contextmanager
        import shutil
        import pantheon.platform.local_fleet as fleet_module
        original_lock = fleet_module.registry_lock
        @contextmanager
        def observed_lock(path, *args, **kwargs):
            try:
                with original_lock(path, *args, **kwargs) as held:
                    yield held
            except TimeoutError as error:
                diagnostic = shutil.which('lsof')
                if path.name == 'profile.lock' and diagnostic:
                    probe = subprocess.run([diagnostic, str(path)], capture_output=True, text=True, timeout=10)
                    error.add_note('Live profile lock holders: ' + probe.stdout)
                raise
        monkeypatch.setattr(fleet_module, 'registry_lock', observed_lock)
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
    model_ref = 'fleet-model://local/example%3A8b'
    credential_ref = 'node-secret://migrated-provider'

    def configure(setup):
        agent = setup['agent']
        agent.update({key: legacy[key] for key in ('projects', 'active_project', 'default_project')})
        if oauth_enabled:
            agent['models']['oauth'] = ['codex', 'gemini-cli']
        if retain_environment:
            agent['models']['fleet_tiers'] = {tier: model_ref for tier in ('low', 'normal', 'high')}
            setup['model_apps']['connector']['app']['components']['backend']['values']['connector']['secret_ref'] = credential_ref
            model_endpoint.required_key = 'synthetic-migrated-model-key'
        if budget_enabled:
            entry = setup['model_apps']['connector']
            entry['app']['components']['backend']['values']['connector'].update(
                engine='api', endpoint=model_endpoint.url + '/v1')
            entry['models'][0].update(operations=['text'])
            # Capabilities come from authenticated upstream discovery, never
            # from the owner's selection of models to publish.
            model_endpoint.api_model_metadata = {
                'context_length': 8192, 'supported_parameters': ['tools']}
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

    bundle, setup_path, bundled, spec = await product_configuration(tmp_path, binaries, release,
        model_endpoint, monkeypatch, provider_packages={'files': files}, configure=configure)
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': ['shell'], 'model': 'normal'}]}
    settings = dict(selected['settings'])
    if retain_environment and not budget_enabled:
        settings['api_keys'] = {'OPENAI_API_KEY': model_endpoint.required_key, 'OPENAI_API_BASE': model_endpoint.url}
    (config/'settings.json').write_text(json.dumps(settings))
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(legacy['home_memory'])/name
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = template
        value['extra_data']['project'] = legacy['projects'][0]
        path.write_text(json.dumps(value))
    old_config = user_tree(config)
    request = dict(protocol=1, operation='native-workspace-import', app='agent', legacy=legacy,
                   backup=str(tmp_path/'backup'), retained_roots=retained_roots if retain_environment else [])
    if oauth_enabled:
        from pantheon.utils.oauth.storage import OAuthStorage
        import base64
        claims = base64.urlsafe_b64encode(json.dumps({'exp': 9999999999}).encode()).decode().rstrip('=')
        tokens = {'access_token': 'test.' + claims + '.signature', 'refresh_token': 'synthetic-oauth-refresh'}
        source = Path(legacy['global_config'])
        for name, provider, extra in [('codex.json', 'codex', {}),
            ('gemini_cli.json', 'gemini_cli', {'expires_at': 9999999999,
                                            'project_id': 'test-project', 'email': 'test@example.invalid'})]:
            OAuthStorage(source/'oauth'/name, ownership_root=source).save(
                {'provider': provider, 'tokens': {**tokens, **extra}})
        request['oauth_configuration'] = {'providers': ['codex', 'gemini-cli']}
    if retain_environment:
        request['model_selection'] = {
            'selections': [{'conversation_id': cid, 'config_id': template['agents'][0]['id'],
                            'source': 'normal', 'target': model_ref} for cid in ('chat-a', 'chat-b')],
            'fleet_tiers': {tier: model_ref for tier in ('low', 'normal', 'high')}}
        request['model_credentials'] = {'bindings': [{
            'provider': 'openai', 'source': str(config/'settings.json'), 'alias': 'connector',
            'ref': credential_ref, 'endpoint': model_endpoint.url}]}
    if budget_enabled:
        from pantheon.settings import Settings
        from pantheon.chatroom.migration_handoff import export_model_handoff
        old = Settings(workspace, user_home=Path(legacy['global_config']), isolated_env=True, environment={
            'LLM_FORCE_PROXY': 'true', 'PLATFORM_MODEL_MODE': 'direct',
            'PANTHEON_PLATFORM_PROXY_BASE': model_endpoint.url,
            'PANTHEON_PLATFORM_PROXY_KEY': model_endpoint.required_key})
        legacy['model_environment_file'] = export_model_handoff(old, operation_id='budget-import')['source']
        # A paired provisioning receipt names the destination owner/node. The
        # isolated profile creates those identities without starting any Apps.
        async with LocalFleet(tmp_path/'profile', bundled, workspace=workspace) as identity_profile:
            placement = identity_profile.coordinates
            receipt = dict(protocol=1, owner=placement.fleet_id, node_id=placement.node_id,
                source='platform-budget', model_mode='direct', connector={
                    'engine': 'api', 'endpoint': model_endpoint.url + '/v1', 'secret_ref': credential_ref})
        choice = dict(protocol=1, source='legacy-local-browser', service_id='old-desktop', enabled=True)
        request['model_selection'].update(budget_choice=choice, source_service_id='old-desktop')
        request['model_credentials'] = {'bindings': [], 'platform_budget': {
            'choice': choice, 'provisioned': receipt}}
    request_path = tmp_path/'migration-request.json'
    request_path.write_text(json.dumps(request)); request_path.chmod(0o600)
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon.chatroom.migration_profile',
        '--bundle', str(bundle), '--setup', str(setup_path), '--profile', str(tmp_path/'profile'),
        '--workspace', str(workspace), '--request', str(request_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    communication = asyncio.create_task(process.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), 180)
    except TimeoutError:
        process.send_signal(signal.SIGTERM)
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), 30)
        pytest.fail('Migration command did not finish: ' + stderr.decode())
    assert process.returncode == 0, stderr.decode()
    result = json.loads(stdout)
    assert result['state'] == 'imported' and result['conversations'] == 2
    root = Path(result['data_root'])
    imported = dict(root=root, receipt=json.loads((root/'migration-receipt.json').read_text()))
    if oauth_enabled:
        assert imported['receipt']['oauth_bindings']['providers'] == ['codex', 'gemini-cli']
        assert 'synthetic-oauth-refresh' not in stdout.decode() + stderr.decode() + json.dumps(imported['receipt'])
        for provider in ('codex', 'gemini-cli'):
            path = root/'oauth'/(provider + '.json')
            assert json.loads(path.read_text())['tokens']['refresh_token'] == 'synthetic-oauth-refresh'
            assert path.stat().st_mode & 0o077 == 0
    # The owner command stops the prepared profile; it never opens a blank
    # Agent or runs a conversation as a side effect of data initialization.
    checkpoint = json.loads((tmp_path/'profile/app-profile/current.json').read_text())
    assert checkpoint['phase'] == 'stopped' and checkpoint['startup_abort'] is True
    assert not (root/'agent-data-format.json').exists()
    if budget_enabled:
        assert checkpoint['models']['connector']['state'] == 'stopped'
        audit = json.loads((root/'migration-model-selections.json').read_text())
        assert audit['budget_review']['provisioning'] == receipt
    else:
        assert not checkpoint['models'], 'BYOK migration must not start or register a model provider'
    assert not model_endpoint.requests and model_endpoint.unauthorized == 0
    if retain_environment:
        assert model_endpoint.required_key.encode() not in stdout + stderr
        assert model_endpoint.required_key not in json.dumps(imported['receipt'])
        assert 'api_keys' not in json.loads((root/'configuration/.pantheon/settings.json').read_text())
    logical_id = None
    for cycle in (1, 2):
        async with LocalFleet(tmp_path/'profile', bundled, workspace=workspace) as runtime:
            info, children = runtime.coordinates, list(runtime._children)
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                inbox_prefix=('_INBOX_' + info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id,
                str(workspace), connection=nc)
            session = LocalAppProfile(runtime, spec, resolver)
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
        else:
            assert after == old_config
        assert not list(imported['root'].rglob('retained.txt'))
    assert len([row for row in model_endpoint.requests if row[0] == '/v1/chat/completions']) == 4
