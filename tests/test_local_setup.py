"""First-run choices compile the full product without running an App."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import subprocess

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.platform.local_setup import prepare, create
from pantheon.platform.local_credentials import read_credentials
from test_general_agent_preset import complete_entries
from test_local_agent_product import product


def choices():
    return {'protocol': 1, 'project_name': 'Research', 'engine': 'api',
        'endpoint': 'https://models.example.test', 'key': 'private-first-run-key',
        'tiers': {'low': 'vendor/small', 'normal': 'vendor/medium', 'high': 'vendor/large'},
        'store_origin': 'https://store.example.test'}


def test_complete_preset_preserves_defaults_and_scoped_model_access(complete_entries, tmp_path):
    selected = choices()
    before = deepcopy(selected)
    setup, secrets, spec = prepare(complete_entries, selected, tmp_path/'private')
    assert selected == before
    assert 'settings' not in setup['agent']
    assert setup['evolution'] == {'execution': 'node', 'options': {}}
    assert set(setup['agent']['models']['fleet_tiers']) == {'low', 'normal', 'high'}
    assert setup['agent']['models']['fleet_tiers']['normal'] == 'fleet-model://local/vendor%2Fmedium'
    assert set(spec['apps']) == {'agent', 'allocator', 'model-access', 'files-models', 'files',
        'shell', 'notebook', 'web', 'evolution', 'desktop', 'fleet', 'model-management'}
    assert setup['files'] == {name: {'state': 'unconfigured'} for name in ('sampling', 'image_generation')}
    notebook = spec['apps']['notebook']['components']['backend']['values']['notebook']
    assert notebook == {'workspace': {'$local': 'workspace'}, 'execution_timeout': 3600, 'execution_logging': True}
    assert secrets['credentials']['node-secret://selected-model']['key'] == selected['key']
    assert selected['key'] not in json.dumps(setup) + json.dumps(spec)


def test_optional_image_choices_use_same_model_service(complete_entries, tmp_path):
    selected = {**choices(), 'image_model': 'vendor/image', 'image_inspection': True, 'context_limit': 65536}
    setup, _, spec = prepare(complete_entries, selected, tmp_path/'private')
    assert setup['files']['image_generation']['model'] == 'fleet-model://local/vendor%2Fimage'
    assert setup['files']['sampling']['model'] == setup['agent']['models']['fleet_tiers']['normal']
    assert len(setup['model_apps']['connector']['models']) == 4
    assert spec['apps']['files-models']['components']['backend']['values']['model_services']['policies']['files']['deployments']


@pytest.mark.parametrize('bad', [
    {'tiers': {'normal': 'model'}}, {'engine': 'speaches'}, {'context_limit': 0},
    {'endpoint': 'https://user:secret@models.example.test'}, {'key': 'key\n'},
    {'store_origin': 'http://remote.example.test'}, {'image_inspection': 'yes'},
    {'engine': 'ollama', 'endpoint': 'https://remote.example.test'},
])
def test_bad_first_run_choices_do_not_compile(complete_entries, tmp_path, bad):
    with pytest.raises((AssemblyError, ValueError)):
        prepare(complete_entries, {**choices(), **bad}, tmp_path/'private')


def test_creation_is_private_and_cannot_replace_existing_profile(complete_entries, tmp_path, monkeypatch):
    monkeypatch.setattr('pantheon.platform.local_setup.read_bundle', lambda path: (None, complete_entries))
    root, workspace, bundle = tmp_path/'private', tmp_path/'workspace', tmp_path/'bundle'
    workspace.mkdir(); bundle.mkdir()
    result = create(bundle, root, workspace, [sys.executable, '-m', 'pantheon'], choices())
    assert not (root/'profile').exists()  # No Fleet or App was started.
    assert json.loads((root/'launch.json').read_text()) == result
    assert Path(result['credentials']).stat().st_mode & 0o077 == 0
    assert root.stat().st_mode & 0o077 == 0
    setup = json.loads((root/'setup.json').read_text())
    from pantheon.apps.local_agent import compose_profile
    assert read_credentials(result['credentials'], compose_profile(complete_entries, setup), workspace).entries
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    with pytest.raises(AssemblyError): create(bundle, root, workspace, [sys.executable], choices())
    assert before == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_creation_rejects_public_destination_before_writing(complete_entries, tmp_path, monkeypatch):
    monkeypatch.setattr('pantheon.platform.local_setup.read_bundle', lambda path: (None, complete_entries))
    workspace, bundle = tmp_path/'workspace', tmp_path/'bundle'
    workspace.mkdir(); bundle.mkdir()
    for base in (workspace, bundle):
        with pytest.raises(AssemblyError): create(bundle, base/'secret', workspace, [sys.executable], choices())
        assert not (base/'secret').exists()


def test_interrupted_creation_has_no_launch_receipt(complete_entries, tmp_path, monkeypatch):
    monkeypatch.setattr('pantheon.platform.local_setup.read_bundle', lambda path: (None, complete_entries))
    from pantheon.apps.owner_journal import OwnerJournal
    write = OwnerJournal._write
    def fail_receipt(self, path, value):
        if path.name == 'launch.json': raise OSError('interrupted write')
        return write(self, path, value)
    monkeypatch.setattr(OwnerJournal, '_write', fail_receipt)
    workspace = tmp_path/'workspace'; workspace.mkdir()
    root = tmp_path/'private'
    with pytest.raises(OSError): create(tmp_path/'bundle', root, workspace, [sys.executable], choices())
    assert (root/'setup.json').is_file() and (root/'credentials.json').is_file()
    assert not (root/'launch.json').exists() and not (root/'profile').exists()
    with pytest.raises(AssemblyError): create(tmp_path/'bundle', root, workspace, [sys.executable], choices())


def test_public_setup_dispatch_does_not_import_legacy_agent_or_factory():
    script = '''
import importlib.abc, sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name == 'pantheon.agent' or name.startswith(('pantheon.chatroom', 'pantheon.repl', 'pantheon.factory')):
   raise AssertionError(name)
sys.meta_path.insert(0, Boundary())
import pantheon.platform.local_setup as setup
seen = []
setup.main = lambda args: seen.append(args)
from pantheon.__main__ import main
sys.argv = ['pantheon', 'local-setup', '--bundle', '/explicit/product']
main()
assert seen == [['--bundle', '/explicit/product']]
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
