"""The packaged Desktop carries what it needs to offer installed Apps to the
Agent (docs/agent/agent-app-directory.md), with only its own vendored runtime."""
import json
import subprocess
import sys

import pytest


def test_packaged_desktop_lists_an_apps_tools_and_actions(tmp_path):
    from pantheonos.appkit.builtin.desktop.build_managed import build
    from pantheonos.appkit.local_agent import native_platform
    package = build(tmp_path / 'desktop', native_platform())
    vendor = package / 'backend' / '_vendor'
    manifest = {'id': 'graph', 'name': 'Graph', 'version': '1.0.0', 'apiVersion': 2,
                'entry': {'backend': 'backend/__init__.py'},
                'provides': {'tools': [{'name': 'rank', 'params': [{'name': 'top', 'type': 'int'}]}]},
                'actions': [{'name': 'fit'}]}
    code = ('import json, sys; sys.path.insert(0, sys.argv[1]); '
            'from pantheonos.appkit.builtin.desktop.agent_apps import offer; '
            'import pantheonos; assert pantheonos.__file__.startswith(sys.argv[1]), pantheonos.__file__; '
            'print(json.dumps(offer(json.loads(sys.argv[2]))))')
    result = subprocess.run([sys.executable, '-I', '-c', code, str(vendor), json.dumps(manifest)],
                            capture_output=True, text=True, cwd=tmp_path, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    entry = json.loads(result.stdout)
    assert [f['name'] for f in entry['tools']] == ['rank'] and [f['name'] for f in entry['actions']] == ['fit']
