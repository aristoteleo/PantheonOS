"""Actual paired Agent upgrades through the public saved-launch CLI commands."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from pantheon.apps.local_agent import build_bundle, read_bundle, native_platform
from pantheon.apps.release_set import index_packages
from pantheon.chatroom.package import build_package
from pantheon.platform.local_launch import LaunchJournal, read_launch
from test_agent_application import TEMPLATE
from test_agent_release import release
from test_local_fleet import binaries
from test_local_model_http import model_endpoint
from test_local_profile_agent import product_configuration


@pytest.mark.asyncio
@pytest.mark.parametrize('driver', ['cli', 'native-owner', 'failed-desktop-start'])
async def test_saved_launch_cli_paired_agent_history_upgrade_and_rollback(tmp_path, binaries, release, model_endpoint, monkeypatch, driver):
    native = os.environ.get('PANTHEON_TEST_NATIVE_RELEASE')
    if driver == 'native-owner' and not native:
        pytest.skip('Supply the native Desktop Cargo manifest for owner command acceptance')
    gui = os.environ.get('AGENT_UPGRADE_GUI_DIR')
    if not gui: pytest.skip('Supply a paired 0.7.1 Agent GUI')
    source, setup, _, _ = await product_configuration(tmp_path, binaries, release, model_endpoint, monkeypatch)
    target_platform = native_platform()
    release_set = tmp_path/'new-release'; release_set.mkdir()
    _, entries = read_bundle(source)
    packages = {}
    for alias, (_, directory) in entries.items():
        output = release_set/alias
        if alias == 'agent':
            await asyncio.to_thread(build_package, output, target_platform, version='0.7.1',
                frontend=gui, transport=os.environ['AGENT_RELEASE_TRANSPORT'],
                dependencies={'shell': {'range': '^0.6.0', 'uses': ['shell@1'], 'binding': 'runtime'}})
            if driver == 'failed-desktop-start':
                definition = json.loads((output/'fleet.json').read_text())
                probe = definition['components'][0]['readiness']
                probe['argv'] = [probe['argv'][0], '-c',
                    'import subprocess,sys;from pathlib import Path;subprocess.run(sys.argv[2:],check=True);'
                    'Path(sys.argv[1]).write_text("ready");raise SystemExit(1)',
                    '${DATA}/candidate-readiness-passed',
                    *probe['argv']]
                probe['timeout_seconds'] = 3
                (output/'fleet.json').write_text(json.dumps(definition))
        else: shutil.copytree(directory, output)
        packages[alias] = {target_platform: output}
    index_packages(release_set, packages)
    target = build_bundle(tmp_path/'new-product', release=release_set, binaries=binaries, target=target_platform)
    launch = tmp_path/'launch.json'
    LaunchJournal(tmp_path)._write(launch, dict(protocol=1, launcher=[sys.executable, '-m', 'pantheon'],
        bundle=str(source), setup=str(setup), profile=str(tmp_path/'profile'), workspace=str(tmp_path/'workspace')))
    template = tmp_path/'template.json'
    LaunchJournal(tmp_path)._write(template, {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': ['shell'], 'model': 'normal'}]})

    async def command(*args):
        child = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon', *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(180): out, err = await child.communicate()
            assert child.returncode == 0, err.decode()
            return [json.loads(line) for line in out.splitlines()]
        finally:
            if child.returncode is None:
                child.terminate()
                try:
                    async with asyncio.timeout(30): await child.wait()
                except TimeoutError:
                    child.kill(); await child.wait()

    chat_id = None
    failed = driver == 'failed-desktop-start'
    prompts = ('source launch turn', 'failed candidate', 'restored launch turn') if failed else (
        'source launch turn', 'candidate launch turn', 'candidate reopen turn', 'restored launch turn')
    rollback_cycle = 2 if failed else 3
    for cycle, prompt in enumerate(prompts, 1):
        if failed and cycle == 2:
            from pantheon.platform.local_desktop import PREFIX
            log = (tmp_path/'failed-desktop-start.log').open('wb')
            child = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon', 'local',
                '--launch', str(launch), '--desktop-agent', 'agent', stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=log)
            statuses = []
            try:
                async with asyncio.timeout(180):
                    while line := await child.stdout.readline():
                        if not line.startswith(PREFIX.encode()): continue
                        event = json.loads(line[len(PREFIX):])
                        assert event['kind'] != 'ready', 'Rejected candidate must not open a view'
                        if event['kind'] != 'status': continue
                        statuses.append(event['value'])
                        if event['value'].get('needs_attention'):
                            child.stdin.write(b'{"protocol":1,"command":"stop"}\n')
                            await child.stdin.drain()
                    assert await child.wait() == 0
                assert sum(bool(s.get('needs_attention')) for s in statuses) == 1
                assert statuses[-1]['state'] == 'stopped'
                saved = json.loads((tmp_path/'profile/app-profile/current.json').read_text())
                assert saved['cycle'] == 2 and saved['phase'] == 'stopped' and saved['startup_abort'] is True
                markers = list((tmp_path/'profile/node/apps').glob('*/data/*/candidate-readiness-passed'))
                assert len(markers) == 1 and markers[0].read_text() == 'ready'
            finally:
                if child.returncode is None: child.kill(); await child.wait()
                log.close()
            review = (await command('local-release', '--launch', str(launch), '--rollback-of', upgrade))[0]
            result = (await command('local-release', '--launch', str(launch), '--rollback-of', upgrade,
                                    '--approve', review['review_id']))[0]
            assert result['state'] == 'approved' and read_launch(launch)['bundle'] == str(source)
            continue
        options = ['--chat-id', chat_id] if chat_id else ['--template-json', str(template)]
        result = await command('cli', '--launch', str(launch), *options, '-i', prompt, '--stream')
        assert result[-1]['kind'] == 'result' and result[-1]['response'] == 'scoped reply'
        assert 'PROFILE_TOOL_OK' in json.dumps(result)
        chat_id = result[-1]['chat_id']
        profile = json.loads((tmp_path/'profile/app-profile/current.json').read_text())
        assert profile['cycle'] == cycle and profile['phase'] == 'stopped'
        calls = [body for path, _, body in model_endpoint.requests if path == '/v1/chat/completions']
        messages = json.dumps(calls[-1]['messages'])
        assert 'source launch turn' in messages
        if cycle in (2, 3) and not failed: assert 'candidate launch turn' in messages
        if cycle == len(prompts):
            assert 'candidate launch turn' not in messages and 'candidate reopen turn' not in messages
            assert read_launch(launch)['bundle'] == str(source)
        if cycle in (1, rollback_cycle):
            if driver == 'native-owner':
                env = {**os.environ, 'PANTHEON_TEST_RELEASE_LAUNCH': str(launch)}
                # Cargo runs in the UI checkout, outside this owner worktree.
                # Select the runtime under test explicitly, never an unrelated
                # editable Pantheon installation in the shared Python env.
                env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
                env.pop('PANTHEON_TEST_RELEASE_TARGET', None)
                if cycle == 1: env['PANTHEON_TEST_RELEASE_TARGET'] = str(target)
                child = await asyncio.create_subprocess_exec('cargo', 'test', '--manifest-path', native, '--lib',
                    'agent_product::release::tests::real_owner_release_command', '--', '--exact', '--ignored', '--nocapture',
                    env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    async with asyncio.timeout(180): out, err = await child.communicate()
                    assert child.returncode == 0, out.decode()+err.decode()
                    assert '1 passed' in out.decode()
                finally:
                    if child.returncode is None: child.kill(); await child.wait()
                assert read_launch(launch)['bundle'] == str(target if cycle == 1 else source)
            else:
                selection = ['--target-bundle', str(target)] if cycle == 1 else ['--rollback-of', upgrade]
                review = (await command('local-release', '--launch', str(launch), *selection))[0]
                approval = (await command('local-release', '--launch', str(launch), *selection, '--approve', review['review_id']))[0]
                assert approval['state'] == 'approved' and approval['launch'] == read_launch(launch)
                if cycle == 1: upgrade = review['review_id']
