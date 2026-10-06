"""Real paired Agent history survives owner SIGKILL and explicit public recovery."""
import asyncio
import json
from pathlib import Path
import sys

import psutil
import pytest

from pantheon.platform.local_desktop import PREFIX
from pantheon.platform.local_launch import LaunchJournal
from test_agent_application import TEMPLATE
from test_agent_release import release
from test_local_fleet import binaries
from test_local_model_http import model_endpoint
from test_local_profile_agent import product_configuration


@pytest.mark.asyncio
async def test_agent_owner_crash_recovery_retains_history_and_runs_new_tools(tmp_path, binaries, release, model_endpoint, monkeypatch):
    product, setup, _, _ = await product_configuration(tmp_path, binaries, release, model_endpoint, monkeypatch)
    launch = tmp_path/'launch.json'
    LaunchJournal(tmp_path)._write(launch, dict(protocol=1, launcher=[sys.executable,'-m','pantheon'],
        bundle=str(product), setup=str(setup), profile=str(tmp_path/'profile'), workspace=str(tmp_path/'workspace')))
    template = tmp_path/'template.json'
    LaunchJournal(tmp_path)._write(template, {**TEMPLATE, 'agents': [
        {**TEMPLATE['agents'][0], 'toolsets': ['shell'], 'model': 'normal'}]})

    async def command(*args):
        child = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon', *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(180): out, err = await child.communicate()
            assert child.returncode == 0, err.decode()
            return [json.loads(line) for line in out.splitlines()]
        finally:
            if child.returncode is None: child.kill(); await child.wait()

    before = await command('cli', '--launch', str(launch), '--template-json', str(template),
                           '-i', 'turn before owner crash', '--stream')
    assert before[-1]['response'] == 'scoped reply' and 'PROFILE_TOOL_OK' in json.dumps(before)
    chat = before[-1]['chat_id']
    log = (tmp_path/'crashed-owner.log').open('wb')
    owner = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon', 'local',
        '--launch', str(launch), '--desktop-agent', 'agent', stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=log)
    children = []
    try:
        async with asyncio.timeout(180):
            while line := await owner.stdout.readline():
                if not line.startswith(PREFIX.encode()): continue
                event = json.loads(line[len(PREFIX):])
                if event['kind'] == 'ready': break
                assert not event.get('value', {}).get('needs_attention'), event['kind']
            else: pytest.fail('Desktop owner exited before readiness')
        root = tmp_path/'profile'
        receipt = json.loads((root/'processes.json').read_text())
        children = [psutil.Process(v['pid']) for v in receipt['children'].values()]
        old = json.loads((root/'app-profile/current.json').read_text())
        assert old['phase'] == 'ready' and old['cycle'] == 2
        owner.kill(); await owner.wait()
        assert all(child.is_running() for child in children)
        recovered = await command('local', '--launch', str(launch), '--recover')
        assert recovered[-1]['state'] == 'stopped'
        saved = json.loads((root/'app-profile/current.json').read_text())
        assert saved['phase'] == 'stopped' and saved['cycle'] == 2
        assert all(not child.is_running() or child.status() == psutil.STATUS_ZOMBIE for child in children)
        after = await command('cli', '--launch', str(launch), '--chat-id', chat,
                              '-i', 'turn after explicit crash recovery', '--stream')
        assert after[-1]['response'] == 'scoped reply' and after[-1]['chat_id'] == chat
        assert 'PROFILE_TOOL_OK' in json.dumps(after)
        calls = [body for path, _, body in model_endpoint.requests if path == '/v1/chat/completions']
        messages = json.dumps(calls[-1]['messages'])
        assert 'turn before owner crash' in messages and 'turn after explicit crash recovery' in messages
        final = json.loads((root/'app-profile/current.json').read_text())
        assert final['phase'] == 'stopped' and final['cycle'] == 3
    finally:
        if owner.returncode is None: owner.kill(); await owner.wait()
        for child in reversed(children):
            try:
                if child.is_running():
                    child.terminate()
                    try: await asyncio.to_thread(child.wait, 25)
                    except psutil.TimeoutExpired: child.kill(); await asyncio.to_thread(child.wait, 10)
            except psutil.NoSuchProcess: pass
        log.close()
