"""Opt-in real Linux capture/lifecycle evidence for independent Browser App."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from pantheon.apps.portable import execution_package


def test_packaged_browser_with_real_linux_display(tmp_path):
    image = os.environ.get('PANTHEON_TEST_BROWSER_IMAGE')
    if not image:
        pytest.skip('Provide a pre-provisioned Linux amd64 Chromium/Xpra test image')
    xpra = os.environ.get('PANTHEON_TEST_XPRA_DIR')
    if not xpra:
        pytest.skip('Provide the production UI vendored Xpra directory')
    repo = Path(__file__).resolve().parents[1]
    with execution_package(repo/'apps/browser', 'linux-amd64') as source:
        shutil.copytree(source, tmp_path/'package')
    checks, results = tmp_path/'checks', tmp_path/'results'
    checks.mkdir(); results.mkdir()
    shutil.copytree(xpra, checks/'xpra')
    for name in ('browser_stream_container.py', 'platform_no_agent.py'):
        shutil.copyfile(repo/'tests'/name, checks/name)
    name = 'pantheon-browser-acceptance-' + uuid.uuid4().hex[:12]
    command = ['docker', 'run', '--pull=never', '--rm', '--name', name, '--platform', 'linux/amd64',
               # Two real Chromium processes plus Xpra under amd64 emulation.
               '--network', 'none', '--cpus', '2', '--memory', '4g', '--shm-size', '256m',
               '--mount', f'type=bind,src={tmp_path / "package"},dst=/package,readonly',
               '--mount', f'type=bind,src={checks},dst=/checks,readonly',
               '--mount', f'type=bind,src={results},dst=/results',
               '--entrypoint', '/venv/bin/python3', image, '-I', '/checks/browser_stream_container.py']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=360)
        print(result.stdout)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads((results/'result.json').read_text())['success']
    finally:
        # Only this invocation's uniquely named disposable container is removed.
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
        artifacts = Path('/tmp')/name
        artifacts.mkdir()
        for filename in ('backend.log', 'browser-native.png', 'browser-viewer.png', 'browser-viewer-final.png', 'browser-input.png', 'input-trace.json', 'imports.log', 'result.json'):
            if (results/filename).exists():
                shutil.copyfile(results/filename, artifacts/filename)
        print('Browser evidence: ' + str(artifacts))
