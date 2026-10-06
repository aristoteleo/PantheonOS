"""Opt-in production Atrium + native Fleet + Agent/Notebook/Browser Linux gate.

The image must provide Go, Node, nats-server, the runtime/test Python dependencies,
Xpra/X11 and Chromium. Only test packages are installed; no host user profile or
credentials are passed. GUI inputs are built separately from their real sources.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest


def test_independent_apps_through_native_desktop(tmp_path):
    image = os.environ.get('PANTHEON_TEST_NATIVE_IMAGE')
    if not image:
        pytest.skip('Provide a pre-provisioned Linux native Desktop acceptance image')
    required = ('PANTHEON_TEST_UI_ROOT', 'AGENT_APP_BUILD_DIR',
                'PLATFORM_DESKTOP_BUILD_DIR', 'PANTHEON_TEST_NOTEBOOK_FRONTEND',
                'PANTHEON_TEST_CHROMIUM')
    assert all(os.environ.get(key) for key in required), f'Required: {required}'
    repo = Path(__file__).resolve().parents[1]
    platform = os.environ.get('PANTHEON_TEST_NATIVE_PLATFORM', 'linux/amd64')
    assert platform in ('linux/amd64', 'linux/arm64'), platform
    desktop = Path(os.environ['PLATFORM_DESKTOP_BUILD_DIR']).resolve()
    chunks = json.loads((desktop/'agent-chunks.json').read_text())
    assert chunks and all((desktop/name).is_file() for name in chunks), 'Use scripts/build-platform-desktop.mjs'
    binaries = tmp_path/'binaries'; binaries.mkdir()
    env = dict(os.environ, GOOS='linux', GOARCH=platform.split('/')[1], CGO_ENABLED='0')
    for name, package in (('fleet', './cmd/fleet'), ('transport', './cmd/fleet-app-transport')):
        subprocess.run(['go', 'build', '-o', str(binaries/name), package], cwd=repo/'fleet', env=env, check=True, timeout=180)
    subprocess.run(['go', 'test', '-c', '-o', str(binaries/'gate'), './cmd/fleet-controller'],
                   cwd=repo/'fleet', env=env, check=True, timeout=180)
    results = tmp_path/'results'; results.mkdir()
    (results/'run.sh').write_text('''#!/bin/sh
set -u
# Thousands of venv files belong on node-local disk, not Docker Desktop's
# macOS bind mount. Keep this cold cache private to the disposable node.
mkdir -m 700 /test-cache || exit 1
/venv/bin/python3 /repo/tests/containers/kernel_smoke.py > /results/kernel-smoke.log 2>&1 || exit 1
cd /repo/fleet/cmd/fleet-controller
/binaries/gate -test.run '^TestDependencyRPCOverAuthenticatedNATSAndNativeApps$' -test.count=1 -test.timeout=12m -test.v > /results/gate.log 2>&1
result=$?
for source in /tmp/native-agent-*; do
    [ ! -f "$source" ] || cp "$source" /results/
done
cat /sys/fs/cgroup/memory.events > /results/memory.events
cat /sys/fs/cgroup/memory.peak > /results/memory.peak
exit "$result"
''')
    name = 'pantheon-native-desktop-' + uuid.uuid4().hex[:12]
    command = ['docker', 'run', '--pull=never', '--name', name, '--platform', platform,
               '--cpus', '4', '--memory', '8g', '--shm-size', '512m']
    mounts = [(repo, '/repo', True), (Path(os.environ['PANTHEON_TEST_UI_ROOT']).resolve(), '/ui', True),
              (Path(os.environ['AGENT_APP_BUILD_DIR']).resolve(), '/agent-gui', True), (desktop, '/desktop-gui', True),
              (Path(os.environ['PANTHEON_TEST_NOTEBOOK_FRONTEND']).resolve(), '/notebook-gui', True),
              (binaries, '/binaries', True), (results, '/results', False)]
    for source, target, readonly in mounts:
        command += ['--mount', f'type=bind,src={source},dst={target}' + (',readonly' if readonly else '')]
    variables = dict(FLEET_TEST_AGENT_DEPLOYMENT='1', FLEET_TEST_PYTHON='/venv/bin/python3',
        FLEET_TEST_CREDENTIAL_READER='/binaries/fleet', FLEET_TEST_AGENT_CACHE='/test-cache',
        AGENT_RELEASE_TRANSPORT='/binaries/transport', AGENT_APP_BUILD_DIR='/agent-gui',
        PANTHEON_TEST_NATIVE_DESKTOP='/ui/scripts/test-native-agent-desktop.mjs',
        PLATFORM_DESKTOP_BUILD_DIR='/desktop-gui', PANTHEON_TEST_NOTEBOOK_FRONTEND='/notebook-gui',
        PANTHEON_TEST_BROWSER_NATIVE='1', PANTHEON_TEST_CHROMIUM=os.environ['PANTHEON_TEST_CHROMIUM'])
    for key, value in variables.items():
        command += ['--env', f'{key}={value}']
    command += ['--entrypoint', '/bin/sh', image, '/results/run.sh']
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=780)
        log = (results/'gate.log').read_text(errors='replace') if (results/'gate.log').exists() else ''
        preflight = (results/'kernel-smoke.log').read_text() if (results/'kernel-smoke.log').exists() else ''
        assert completed.returncode == 0, completed.stdout + completed.stderr + preflight + log
        desktop_log = (results/'native-agent-desktop.log').read_text()
        assert 'Browser stream, pointer, keys, native tabs, close and reopen work after Agent uninstall' in desktop_log
        events = dict(line.split() for line in (results/'memory.events').read_text().splitlines())
        assert int(events['oom_kill']) == 0, events
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
        evidence = Path('/tmp')/name
        shutil.copytree(results, evidence)
        print('Native Desktop evidence:', evidence)
