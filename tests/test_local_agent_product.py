"""Product packaging/compiler checks; actual execution lives in the native gate."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.local_agent import build_bundle, compose_profile, native_platform, read_bundle, CORE
from pantheon.apps.release_set import index_packages
from pantheon.platform.local_fleet import LocalFleetBinaries
from pantheon.platform.model_dependency_package import build_package


@pytest.fixture
def product(tmp_path):
    release = tmp_path/'release'; release.mkdir()
    target = native_platform()
    sources = {}
    for alias, identity in {**CORE, 'connector': 'model-service', 'shell': 'shell'}.items():
        root = build_package(release/alias, target)
        for filename in ('app.json', 'fleet.json'):
            path = root/filename; value = json.loads(path.read_text())
            value['id' if filename == 'app.json' else 'app_id'] = identity
            path.write_text(json.dumps(value))
        sources[alias] = {target: root}
    index_packages(release, sources)
    (release/'agent'/'.env').write_text('PRIVATE_USER_SECRET=must-not-ship\n')
    (release/'agent'/'__pycache__').mkdir()
    (release/'agent'/'__pycache__'/'ignored').write_bytes(b'cached')
    paths = []
    for name in ('controller', 'broker', 'runner'):
        path = tmp_path/name; path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o755); paths.append(path)
    bundle = build_bundle(tmp_path/'product', release=release, binaries=LocalFleetBinaries(*paths), target=target)
    return bundle


def setup():
    return {'protocol': 1, 'agent': {'protocol': 1, 'namespace': 'local-agent',
        'projects': [{'id': 'shared', 'name': 'Shared', 'path': {'$local': 'workspace'}}],
        'settings': {'memory': {'enabled': True}, 'custom': {'preserve': 'unchanged'}},
        'models': {'fleet_tiers': {'normal': 'fleet-model://local/example%3A8b'}},
        'dependencies': {'allocator': 'allocator', 'profiles': {'toolsets': {
            'shell': {'alias': 'shell', 'functions': []}}, 'mcp_servers': {}},
            'defaults': {'toolsets': ['shell'], 'mcp_servers': []}}},
        'tools': {'shell': {'app_id': 'shell', 'provider': {'$app': 'shell', 'component': 'backend', 'port': 'http'},
            'methods': {'run_command': {'arguments': ['command'], 'bound': {}}},
            'resource': {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}}},
        'models': {'deployments': {'local': {'$model': 'connector'}}, 'routes': {}, 'allow_wake': False},
        'providers': {'shell': {'scope': 'shared-shell', 'components': {}, 'bindings': {}}},
        'model_apps': {'connector': {'deployment_id': 'local', 'name': 'User model',
            'models': [{'id': 'example:8b', 'context_limit': 4096}],
            'app': {'scope': 'model-local', 'components': {'backend': {'values': {'connector': {
                'engine': 'ollama', 'endpoint': 'http://127.0.0.1:11434'}}}}, 'bindings': {}}}}}


def test_bundle_compiles_without_disabling_configuration_or_issuing_credentials(product, monkeypatch):
    binaries, entries = read_bundle(product)
    assert not (product/'apps/agent/.env').exists()
    assert not (product/'apps/agent/__pycache__').exists()
    selected = setup(); before = deepcopy(selected)
    def no_rebuild(*args): raise AssertionError('Launch must not rebuild artifacts')
    monkeypatch.setattr('pantheon.apps.local_agent.build_artifact', no_rebuild)
    assert read_bundle(product)[0] == binaries
    profile = compose_profile(entries, selected)
    assert selected == before
    agent = profile['apps']['agent']['components']['backend']['values']['agent']
    assert agent['settings'] == selected['agent']['settings']
    assert agent['dependencies'] == selected['agent']['dependencies']
    assert agent['projects'][0]['path'] == {'$local': 'workspace'}
    assert agent['models']['fleet_tiers'] == selected['agent']['models']['fleet_tiers']
    for alias in ('allocator', 'model-access'):
        assert all(value == {'$local': 'owner_credential'} for value in
                   profile['apps'][alias]['components']['backend']['credentials'].values())
    assert 'local-template' not in json.dumps(profile)
    assert profile['model_apps']['connector']['app']['components'] == selected['model_apps']['connector']['app']['components']
    assert all(Path(row['path']).is_relative_to(product) for row in profile['packages'].values())


@pytest.mark.parametrize('damage', ['binary', 'platform', 'escape', 'symlink', 'missing-core', 'wrong-app'])
def test_invalid_product_cannot_launch(product, damage):
    path = product/'local-bundle.json'; value = json.loads(path.read_text())
    if damage == 'binary': (product/'bin/runner').write_text('changed')
    elif damage == 'platform': value['platform'] = 'windows-amd64'
    elif damage == 'escape': value['binaries']['runner']['path'] = '../runner'
    elif damage == 'symlink':
        runner = product/'bin/runner'; runner.rename(product/'bin/saved'); runner.symlink_to(product/'bin/saved')
    else:
        index_path = product/'apps/release-set.json'; index = json.loads(index_path.read_text())
        if damage == 'missing-core': del index['apps']['agent']
        else: index['apps']['agent'][native_platform()]['app_id'] = 'other-app'
        index_path.write_text(json.dumps(index))
    path.write_text(json.dumps(value))
    with pytest.raises(AssemblyError): read_bundle(product)


@pytest.mark.parametrize('damage', ['provider', 'missing-app', 'tool-policy', 'foreign-trust'])
def test_invalid_setup_is_rejected_instead_of_reducing_capabilities(product, damage):
    _, entries = read_bundle(product); selected = setup()
    if damage == 'provider': selected['providers']['agent'] = selected['providers']['shell']
    elif damage == 'missing-app': del entries['shell']
    elif damage == 'tool-policy': selected['tools'] = {}
    else: selected['agent']['rpc_origin'] = 'https://foreign.example'
    with pytest.raises(AssemblyError): compose_profile(entries, selected)


def test_product_loader_has_no_agent_runtime_import():
    result = subprocess.run([sys.executable, '-c', '''
import importlib.abc
import sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name == 'pantheon.agent' or name.startswith(('pantheon.chatroom', 'pantheon.repl', 'pantheon.team', 'pantheon.factory')):
   raise AssertionError(name)
sys.meta_path.insert(0, Boundary())
from pantheon.apps.local_agent import compose_profile, read_bundle
'''], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_existing_multiplatform_provider_retains_its_native_artifact(product, tmp_path):
    from pantheon.apps.lifecycle import build_artifact
    from pantheon.apps.registry import BUILTIN_ROOT
    import shutil
    root = tmp_path/'variants'; root.mkdir()
    base = json.loads((product/'apps/release-set.json').read_text())
    selected = {}
    target = native_platform()
    for alias, variants in base['apps'].items():
        output = root/alias
        if alias == 'connector':
            shutil.copytree(Path(BUILTIN_ROOT)/'model-service', output,
                            ignore=shutil.ignore_patterns('.git', '__pycache__', '.env*'))
        else:
            shutil.copytree(product/'apps'/variants[target]['path'], output)
        selected[alias] = {target: output}
    index_packages(root, selected)
    binaries, _ = read_bundle(product)
    bundle = build_bundle(tmp_path/'multi-product', release=root, binaries=binaries, target=target)
    _, entries = read_bundle(bundle)
    profile = compose_profile(entries, setup())
    provider = profile['packages']['connector']
    assert provider['platform'] == target
    assert build_artifact(Path(provider['path']), provider['platform'])[1] == provider['revision']


@pytest.mark.parametrize('selection', [['--bundle', '/product'], ['--bundle=/product']])
def test_cli_product_dispatch_does_not_start_legacy_setup(selection):
    script = '''
import importlib.abc, json, sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name == 'pantheon.agent' or name.startswith(('pantheon.chatroom', 'pantheon.repl', 'pantheon.factory')):
   raise AssertionError(name)
sys.meta_path.insert(0, Boundary())
import pantheon.platform.local_profile as local
seen = []
local.main = lambda args: seen.append(args)
from pantheon.__main__ import main
arguments = json.loads(sys.argv[1])
sys.argv = ['pantheon', 'cli', *arguments]
main()
assert seen == [arguments + ['--agent', 'agent']], seen
'''
    result = subprocess.run([sys.executable, '-c', script, json.dumps(selection)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
