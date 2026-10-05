"""Real CLI entrypoints against an App-owned store and local model endpoint."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.repl.prepared import select_resume
from test_agent_launch import prepared, snapshot
from test_agent_model_scope import endpoint as model_endpoint
from test_agent_release import release


ROOT = Path(__file__).resolve().parents[1]
BOOT = '''
import importlib.abc, runpy, sys
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if name in ('pantheon.chatroom.room', 'pantheon.chatroom.start',
                    'pantheon.platform.service', 'pantheon.platform.apps_api'):
            raise AssertionError('CLI loaded combined platform: '+name)
sys.meta_path.insert(0, Boundary())
import pantheon.settings
def forbidden(*a, **k): raise AssertionError('prepared CLI used ambient settings')
pantheon.settings.get_settings = forbidden
module = sys.argv.pop(1)
runpy.run_module(module, run_name='__main__')
'''


def launch(root, url, module, *args, generation='1', bundle=None, configuration=None):
    value = prepared(root, url) if configuration is None else configuration
    path = root / 'runtime.json'
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    env = {key: val for key, val in os.environ.items()
           if not key.startswith(('PANTHEON_', 'FLEET_', 'NATS_'))}
    env.update(PYTHONPATH=str(ROOT), HOME=str(root / 'home'),
        OPENAI_API_KEY='ambient-do-not-use', LLM_FORCE_PROXY='true',
        PANTHEON_APP_CONFIG=str(path), PANTHEON_FLEET_ID=value['owner'],
        PANTHEON_NODE_ID=value['node_id'], PANTHEON_INSTANCE_ID=value['instance_id'],
        PANTHEON_APP_REVISION=value['revision'], PANTHEON_INSTANCE_GENERATION=generation,
        PANTHEON_COMPONENT_NAME='backend')
    commands = ['cli'] if module == 'pantheon' else []
    entry = ([str(bundle[1]), '-I', str(bundle[0] / 'cli.py')] if bundle else
             [sys.executable, '-c', BOOT, module, *commands])
    return subprocess.run([*entry, '--app-data', str(root / 'data'), *args], cwd=root, env=env,
        capture_output=True, text=True, timeout=45)


def template(root):
    path = root / 'test-agent.md'
    path.write_text('''---
id: cli-test
name: CLI Test
model: openai/gpt-4o-mini
toolsets: []
---
Reply to the user.
''')
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize('module', ['pantheon', 'pantheon.repl'])
async def test_real_cli_template_oneshot_and_resume_use_only_prepared_app(tmp_path, model_endpoint, module):
    await exercise_cli(tmp_path, model_endpoint, module)


@pytest.mark.asyncio
async def test_release_cli_runs_without_a_pantheon_installation(tmp_path, model_endpoint, release):
    await exercise_cli(tmp_path, model_endpoint, 'package', bundle=release)


async def exercise_cli(tmp_path, model_endpoint, module, bundle=None):
    home = tmp_path / 'home' / '.pantheon'
    home.mkdir(parents=True)
    (home / 'cli_history').write_text('legacy history marker')
    source = template(tmp_path)
    result = await asyncio.to_thread(launch, tmp_path, model_endpoint.url, module,
        '--template', str(source), '-i', 'first prepared CLI prompt', '--model', 'openai/gpt-4o-mini', bundle=bundle)
    assert result.returncode == 0, result.stderr + result.stdout
    assert 'scoped reply' in result.stdout, result.stdout
    result = await asyncio.to_thread(launch, tmp_path, model_endpoint.url, module,
        '-r', '-i', 'second prepared CLI prompt', bundle=bundle)
    assert result.returncode == 0, result.stderr + result.stdout
    assert 'scoped reply' in result.stdout
    assert len(model_endpoint.requests) == 2
    assert all(headers['Authorization'] == 'Bearer process-fixture'
               for _, headers, _ in model_endpoint.requests)
    request = model_endpoint.requests[-1][2]
    assert 'first prepared CLI prompt' in json.dumps(request.get('messages', request.get('input')))
    assert (home / 'cli_history').read_text() == 'legacy history marker'
    assert (tmp_path / 'data' / 'cli' / 'history').is_file()
    assert list((tmp_path / 'data' / 'configuration' / '.pantheon' / 'logs' / 'repl').glob('*'))
    # Open the same versioned App data again; no CLI writer/lock survived exit.
    from pantheon.chatroom.launch import ConfiguredAgentApplication
    app = ConfiguredAgentApplication('agent', data_dir=tmp_path / 'data',
                                    configuration=snapshot(prepared(tmp_path, model_endpoint.url)))
    try:
        rows = (await app.list_chats())['chats']
        assert len(rows) == 1
        history = app.memory_manager.get_memory(rows[0]['id']).get_messages(for_llm=False)
        assert 'first prepared CLI prompt' in repr(history) and 'second prepared CLI prompt' in repr(history)
    finally:
        await app.cleanup()


@pytest.mark.parametrize('selection,expected', [(True, 'b'), ('1', 'b'), ('2', 'a'), ('a', 'a'), ('OLDER', 'a')])
def test_resume_matches_existing_cli_flags(selection, expected):
    rows = [{'id': 'a', 'name': 'Older', 'last_activity_date': '2025-01-01T12:00:00'},
            {'id': 'b', 'name': 'Newer', 'last_activity_date': '2025-01-02T12:00:00'}]
    assert select_resume(rows, selection) == expected
    assert rows[0]['id'] == 'a'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['generation', 'workspace', 'resume', 'model', 'memory'])
async def test_invalid_prepared_launch_never_falls_back_or_calls_a_model(tmp_path, model_endpoint, failure):
    options = {'generation': [], 'workspace': ['--workspace', str(tmp_path / 'wrong')],
               'resume': ['--resume', 'missing-chat'], 'model': ['--model', 'anthropic/unbound'],
               'memory': ['--memory-dir', str(tmp_path / 'legacy')]}[failure]
    result = await asyncio.to_thread(launch, tmp_path, model_endpoint.url, 'pantheon',
        '--template', str(template(tmp_path)), '-i', 'must not execute', *options,
        generation='2' if failure == 'generation' else '1')
    assert result.returncode != 0
    assert not model_endpoint.requests
    assert 'ambient-do-not-use' not in result.stdout + result.stderr


@pytest.mark.asyncio
async def test_prepared_keys_command_does_not_mutate_terminal_credentials(tmp_path, model_endpoint):
    legacy = tmp_path / 'home' / '.pantheon'
    legacy.mkdir(parents=True)
    env = legacy / '.env'
    env.write_text('OPENAI_API_KEY=legacy-owned-key\n')
    result = await asyncio.to_thread(launch, tmp_path, model_endpoint.url, 'pantheon',
        '--template', str(template(tmp_path)), '-i', '/keys openai replacement-key')
    assert result.returncode == 0, result.stderr
    assert 'App model providers: openai' in result.stdout
    assert 'replacement-key' not in env.read_text()
    assert env.read_text() == 'OPENAI_API_KEY=legacy-owned-key\n'
    assert not model_endpoint.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('denied', [False, True])
async def test_prepared_mcp_management_uses_declared_view_binding_without_fallback(denied):
    from pantheon.repl.handlers.builtin.mcp import _GatewayClient
    call = AsyncMock(return_value={'success': True, 'servers': []},
                     side_effect=ValueError('denied') if denied else None)
    room = SimpleNamespace(call_view_service=call,
        app_data=SimpleNamespace(projects=SimpleNamespace(active_project=SimpleNamespace(path='/workspace'))),
        proxy_toolset=AsyncMock(side_effect=AssertionError('ambient fallback')))
    result = await _GatewayClient(room).list_services()
    assert result['success'] is not denied
    call.assert_awaited_once_with('/workspace', 'mcp_gateway', 'list_servers', {})
    room.proxy_toolset.assert_not_awaited()
