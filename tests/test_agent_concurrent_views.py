"""Two independently scoped Agent Apps on one real Fleet and shared providers."""
import asyncio
from contextlib import AsyncExitStack
import json
import os

import nats
import pytest

from pantheon.apps.deployment_stop import AppDeploymentStop
from pantheon.apps.owner_journal import OwnerJournal
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.platform.local_desktop import DesktopView, snapshot_frontend
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from test_agent_application import TEMPLATE
from test_agent_release import release
from test_app_upgrade_native import settled
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile import settle
from test_local_profile_agent import product_configuration


def second_agent(spec):
    """Use ordinary App aliases/scopes; shared provider references stay shared."""
    names = {name: name + '-second' for name in ('agent', 'allocator', 'model-access')}
    def references(value):
        if isinstance(value, dict):
            return {key: names.get(item, item) if key == '$app' else references(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [references(item) for item in value]
        return value
    for name, alias in names.items():
        app = references(spec['apps'][name])
        app['scope'] = alias
        spec['apps'][alias] = app


@pytest.mark.asyncio
async def test_two_agent_views_keep_history_and_survive_sibling_stop(tmp_path, binaries, release, model_endpoint, monkeypatch):
    script = os.environ.get('AGENT_CONCURRENT_VIEW_TEST')
    if not script:
        pytest.skip('Supply the real concurrent Agent GUI browser gate')
    _, _, bundled, spec = await product_configuration(tmp_path, binaries, release, model_endpoint, monkeypatch)
    second_agent(spec)
    async with LocalFleet(tmp_path/'profile', bundled, workspace=tmp_path/'workspace') as runtime:
        info = runtime.coordinates; children = list(runtime._children)
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                               inbox_prefix=('_INBOX_' + info.fleet_id).encode())
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(runtime.workspace), connection=nc)
        session = LocalAppProfile(runtime, spec, resolver)
        browser = None
        try:
            await settle(session, 'advance')
            source_id = session._consumer_id(session._record['recipe'])
            ready = session.deploy.inspect(owner=info.fleet_id, operation_id=source_id)
            client = await resolver._ensure_client()
            def binding(name):
                identity = {**ready['prepared'][name], 'generation': ready['prepared'][name]['generation'] + 1}
                identity.pop('node_id')
                async def invoke(method, args, timeout=45):
                    result = await client.invoke(info.node_id, 'agent', identity, method, args, timeout)
                    assert 'error' not in result, result
                    assert result['response'].get('success') is not False, result
                    return result['response']['result']
                return invoke
            calls = [binding('agent'), binding('agent-second')]
            chats = []
            async def history(index):
                invoke = calls[index]; chat_id = chats[index]
                snap = await invoke('open_agent_history', {'chat_id': chat_id})
                try:
                    parts = [await invoke('read_agent_history', {'chat_id': chat_id,
                        'snapshot_id': snap['snapshot_id'], 'part': part}) for part in range(snap['parts'])]
                    return json.loads(''.join(part['json_fragment'] for part in parts))
                finally:
                    await invoke('release_agent_history', {'chat_id': chat_id, 'snapshot_id': snap['snapshot_id']})
            def tool_results(snapshot):
                return [message['raw_content'] for message in snapshot['messages'] if message['role'] == 'tool']
            template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': ['shell'], 'model': 'normal'}]}
            for index, invoke in enumerate(calls):
                chat = await invoke('create_chat', {'chat_name': f'Independent {index}', 'project_name': 'Shared', 'template_obj': template})
                chats.append(chat['chat_id'])
                assert (await invoke('chat', {'chat_id': chats[-1], 'message': [{'role': 'user', 'content': f'PRIVATE_HISTORY_{index}'}]}))['success']
            assert chats[0] != chats[1]
            first_outputs = [tool_results(await history(i)) for i in range(2)]
            assert all(len(outputs) == 1 and outputs[0]['success']
                       and outputs[0]['output'] == 'PROFILE_TOOL_OK' for outputs in first_outputs)
            assert first_outputs[0][0]['shell_id'] != first_outputs[1][0]['shell_id']
            before = (await session.wire.status(info.node_id))['instances']
            sibling_id = ready['prepared']['agent-second']['instance_id']
            async with AsyncExitStack() as stack:
                views = []
                for index, invoke in enumerate(calls):
                    root = tmp_path / f'view-{index}'; root.mkdir()
                    assets = root / 'assets'; assets.mkdir()
                    await asyncio.to_thread(snapshot_frontend, spec['packages']['agent'], assets)
                    state = root / 'state.json'
                    await OwnerJournal(root)._checkpoint(state, {'chatId': chats[index]})
                    views.append(await stack.enter_async_context(DesktopView(assets, invoke, state)))
                browser = await asyncio.create_subprocess_exec('node', script,
                    env={**os.environ, 'AGENT_CONCURRENT_VIEWS': json.dumps([v.url for v in views]),
                         'AGENT_CONCURRENT_SCREENSHOTS': str(tmp_path)},
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                async with asyncio.timeout(100):
                    line = await browser.stdout.readline()
                if line != b'STOP_FIRST\n':
                    if browser.returncode is None: browser.kill()
                    await browser.wait()
                    pytest.fail(line.decode() + (await browser.stderr.read()).decode())
                stopper = AppDeploymentStop(session.deploy, tmp_path/'stops')
                await settled(lambda: stopper.advance(owner=info.fleet_id, operation_id='stop-first-only',
                    source_operation_id=source_id, apps=['agent', 'allocator', 'model-access']), 'stopped')
                after = (await session.wire.status(info.node_id))['instances']
                for name, item in before.items():
                    if name == sibling_id or item['app_id'] in ('shell', 'model-service'):
                        assert after[name]['state'] == 'ready'
                        assert (after[name]['digest'], after[name]['generation']) == (item['digest'], item['generation'])
                browser.stdin.write(b'STOPPED\n'); await browser.stdin.drain()
                async with asyncio.timeout(100): out, err = await browser.communicate()
                assert browser.returncode == 0, out.decode() + err.decode()
                assert b'ISOLATION_OK' in out
            survivor = await history(1)
            serialized = json.dumps(survivor)
            assert 'SURVIVOR_GUI_TURN' in serialized and 'PRIVATE_HISTORY_0' not in serialized
            outputs = tool_results(survivor)
            assert len(outputs) == 2
            assert all(r['success'] and r['output'] == 'PROFILE_TOOL_OK'
                       and r['shell_id'] == first_outputs[1][0]['shell_id'] for r in outputs)
        finally:
            if browser and browser.returncode is None:
                browser.kill(); await browser.wait()
            state = await session.wire.status(info.node_id)
            for item in sorted(state['instances'].values(), key=lambda i: i.get('app_id') != 'agent'):
                if item['state'] == 'stopped': continue
                op = await session.wire.submit(info.node_id, 'stop', item['digest'], scope=item['scope'], generation=item['generation'])
                async def stopped(): return (await session.wire.status(info.node_id))['operations'][op['request']['operation_id']]
                await settled(stopped, 'succeeded')
            await resolver.close()
    assert_stopped(children, info)
