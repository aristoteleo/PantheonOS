"""Migrated OAuth is admitted and usable in the independently packaged runtime."""
import json
import subprocess

from pantheon.chatroom.migration_profile import LocalAgentMigration
from test_agent_release import release
from test_agent_migration import legacy
from test_agent_migration_import import prepared
from test_agent_migration_oauth import oauth_source, inputs


def test_packaged_runtime_receives_migrated_oauth_without_owner_imports(release, prepared, tmp_path):
    request, args = inputs(prepared)
    workflow = LocalAgentMigration(request)
    result = workflow._import(**args)
    package, python = release
    script = r'''
import importlib.abc, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if name.startswith('pantheon.chatroom.migration') or name in ('pantheon.chatroom.room', 'pantheon.platform.service'):
            raise AssertionError('Runtime imported owner/legacy code: ' + name)
sys.meta_path.insert(0, Boundary())
from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.app_models import AppModels
from pantheon.utils.oauth import codex, gemini
root, projects = Path(sys.argv[2]), json.loads(sys.argv[3])
models = {'oauth': ['codex', 'gemini-cli']}
data = AgentAppData(root, namespace='migrated-agent', projects=AppProjects(projects),
                   model_configuration={'owner': 'owner', 'node_id': 'node', 'models': models})
try:
    scoped = AppModels(root, defaults={}, config=models, credentials={})
    for provider in ('codex', 'gemini-cli'):
        manager = scoped.scope.oauth(provider)
        def refresh(token):
            expected = 'synthetic-codex-refresh' if provider == 'codex' else 'synthetic-gemini-refresh'
            assert token == expected
            return {'access_token': 'release-access', 'refresh_token': 'release-rotated',
                    **({'id_token': 'test.id.token'} if provider == 'codex' else {'expires_at': 9999999999})}
        if provider == 'codex': codex._refresh_tokens = refresh
        else: gemini.refresh_access_token = refresh
        manager.refresh()
        assert manager.get_tokens()['refresh_token'] == 'release-rotated'
finally:
    data.close()
'''
    process = subprocess.run([str(python), '-I', '-B', '-c', script,
        str(package / 'backend/_vendor'), str(args['target']), json.dumps(request['legacy']['projects'])],
        cwd=tmp_path, capture_output=True, text=True, timeout=45)
    assert process.returncode == 0, process.stdout + process.stderr
    assert workflow._import(**args) == result
    for provider in ('codex', 'gemini-cli'):
        saved = json.loads((args['target'] / 'oauth' / (provider + '.json')).read_text())
        assert saved['tokens']['refresh_token'] == 'release-rotated'
