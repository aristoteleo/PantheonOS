"""The Agent's source repository builds the package the release ships
(docs/agent-app-repository.md §2)."""
import asyncio
import json
import sys
import subprocess
import os
import struct
from pathlib import Path

import pytest

from pantheonos.apps import app_build
from pantheonos.apps.lifecycle import build_artifact
from pantheonos.apps.schema import parse_manifest
from pantheon.chatroom import agent_build
from pantheon.chatroom.package import build_package

VERSION = '1.2.3-staging.1'


def inputs(tmp_path):
    frontend = tmp_path / 'dist'
    frontend.mkdir()
    (frontend / 'index.js').write_text('export const x = 1\n')
    (frontend / 'agent.css').write_text('body{}\n')
    (frontend / 'build-report.json').write_text(json.dumps({'version': VERSION, 'revision': 'abc',
        'startupChunks': ['index.js'], 'desktopImplementation': False}))
    transport = tmp_path / 'fleet-app-transport'
    header = bytearray(64)
    header[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', header, 18, 62)
    transport.write_bytes(bytes(header))
    window_source = tmp_path / 'window'
    (window_source / 'src').mkdir(parents=True)
    (window_source / 'package.json').write_text('{"scripts": {"build:agent-app": "node build.js"}}')
    (window_source / 'pnpm-lock.yaml').write_text('lockfileVersion: 9.0\n')
    (window_source / 'src' / 'main.ts').write_text('console.log(1)\n')
    (window_source / '.env.production').write_text('VITE_APP_MODE=hub\n')
    return frontend, transport, window_source


@pytest.fixture(scope='module')
def release(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp('agent-source')
    frontend, transport, window = inputs(tmp_path)
    package = build_package(tmp_path / 'agent', 'linux-amd64', version=VERSION, frontend=frontend,
                            transport=transport, frontend_source=window, source_output=tmp_path / 'source',
                            urls={'frontend': 'https://example.test/agent-frontend.tar.gz'})
    return tmp_path, package, tmp_path / 'source', frontend, transport


def test_the_source_tree_holds_source_and_pins_prebuilt_inputs(release):
    _, _, source, frontend, _ = release
    manifest = json.loads((source / 'app.json').read_text())
    assert manifest['build'] == {'argv': ['python3', 'build.py'], 'timeout_seconds': 1800}
    assert 'execution' not in manifest
    assert not (source / 'fleet.json').exists() and not (source / '.fleet-runtime').exists()
    assert not (source / 'backend' / 'pantheon' / 'models' / 'fleet-app-transport').exists()
    assert (source / 'backend' / 'pantheon' / 'factory' / 'templates').is_dir()
    assert (source / 'frontend' / 'src' / 'main.ts').is_file()
    assert (source / 'frontend' / '.env.production').read_text() == 'VITE_APP_MODE=hub\n'
    config = json.loads((source / 'build.json').read_text())
    assert config['inputs']['frontend']['url'] == 'https://example.test/agent-frontend.tar.gz'
    assert config['inputs']['frontend']['source'] == agent_build.tree_digest(source / 'frontend')
    assert config['inputs']['frontend']['sha256'] == __import__('hashlib').sha256(
        agent_build.frontend_archive(frontend)).hexdigest()
    parse_manifest(manifest)


def test_the_package_is_complete(release):
    _, package, _, _, _ = release
    manifest = json.loads((package / 'app.json').read_text())
    assert manifest['execution']['rpc_transport'] == 'fleet' and 'build' not in manifest
    assert list(manifest)[list(manifest).index('entry') + 1] == 'execution'
    definition = json.loads((package / 'fleet.json').read_text())
    assert definition['version'] == VERSION and definition['requires']['os'] == ['linux']
    assert definition['components'][0]['configuration']['credentials']['allocator'] == {'required': True}
    vendor = package / 'backend' / '_vendor' / 'pantheon'
    assert VERSION in (vendor / '__init__.py').read_text()
    # The platform package it runs on carries the model transport.
    assert os.access(vendor.parent / 'pantheonos' / 'models' / 'fleet-app-transport', os.X_OK)
    assert (package / 'frontend' / 'index.js').read_text() == 'export const x = 1\n'
    assert (package / '.fleet-runtime' / 'host.py').is_file()
    inventory = json.loads((package / 'release.json').read_text())['files']
    assert set(inventory) == {p.relative_to(package).as_posix() for p in package.rglob('*')
                              if p.is_file()} - {'release.json'}
    build_artifact(package)


PACKAGED_ATTACH = """
import asyncio, sys
sys.path.insert(0, sys.argv[1])
import pantheon
assert pantheon.__file__.startswith(sys.argv[1]), pantheon.__file__
from pantheon.agent import Agent
from pantheonos.apps.dependency_client import DependencyClient
from pantheonos.apps.runtime_config import RuntimeCredential
from pantheon.dependency_provider import DependencyToolProvider
from pantheon.factory.bindings import AgentToolBindings

async def main():
    agent = Agent(name='a', instructions='', model='gpt-4o-mini')
    client = DependencyClient(RuntimeCredential('https://localhost/rpc', 'a' * 64))
    desktop = DependencyToolProvider('desktop', client, [{'name': 'desktop_apps', 'description': '',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}])
    await AgentToolBindings({'desktop': desktop}).attach(agent, ['desktop'], [])
    assert agent.app_directory is not None
asyncio.run(main())
print('attached')
"""


def test_the_packaged_agent_reaches_installed_apps(release, tmp_path):
    # Only the vendored runtime: a module the package leaves out fails here.
    _, package, _, _, _ = release
    result = subprocess.run([sys.executable, '-I', '-c', PACKAGED_ATTACH, str(package / 'backend' / '_vendor')],
                            capture_output=True, text=True, cwd=tmp_path, timeout=300)
    assert result.returncode == 0 and 'attached' in result.stdout, result.stderr[-3000:]


def seeded_cache(tmp_path, frontend, transport, source):
    config = json.loads((source / 'build.json').read_text())
    cache = tmp_path / 'cache'
    (cache / 'inputs').mkdir(parents=True)
    (cache / 'inputs' / config['inputs']['frontend']['sha256']).write_bytes(agent_build.frontend_archive(frontend))
    (cache / 'inputs' / config['inputs']['transport']['linux-amd64']['sha256']).write_bytes(transport.read_bytes())
    return cache


@pytest.mark.asyncio
async def test_an_owner_build_of_the_repository_is_the_release_package(release, tmp_path):
    _, package, source, frontend, transport = release
    cache = seeded_cache(tmp_path, frontend, transport, source)
    manifest = json.loads((source / 'app.json').read_text())
    await app_build.run(source, tmp_path / 'built', manifest=manifest, platform='linux-amd64',
                        revision='c' * 40, cache=cache, log=tmp_path / 'build.log')
    assert build_artifact(tmp_path / 'built')[1] == build_artifact(package)[1]


@pytest.mark.asyncio
async def test_a_changed_window_is_built_from_source(release, tmp_path, monkeypatch):
    _, _, source, frontend, transport = release
    fork = tmp_path / 'fork'
    import shutil
    shutil.copytree(source, fork)
    (fork / 'frontend' / 'src' / 'main.ts').write_text('console.log(2)\n')
    calls = []

    def run(argv, cwd, env):
        calls.append(argv[1:])
        if argv[1:] == ['run', 'build:agent-app']:
            out = Path(env['AGENT_APP_BUILD_DIR'])
            out.mkdir(parents=True, exist_ok=True)
            for name in ('index.js', 'agent.css', 'build-report.json'):
                (out / name).write_text('rebuilt ' + env['AGENT_APP_VERSION'])
    monkeypatch.setattr(agent_build, 'run', run)
    monkeypatch.setattr(agent_build.shutil, 'which', lambda name: '/usr/bin/' + name)
    cache = seeded_cache(tmp_path, frontend, transport, fork)
    agent_build.build(fork, tmp_path / 'out', platform='linux-amd64', revision='d' * 40, cache=cache)
    assert calls == [['install', '--frozen-lockfile', '--store-dir', str(cache / 'pnpm-store')], ['run', 'build:agent-app']]
    assert (tmp_path / 'out' / 'frontend' / 'index.js').read_text() == 'rebuilt ' + VERSION
    # node_modules is reused while pnpm-lock.yaml is unchanged.
    calls.clear()
    agent_build.build(fork, tmp_path / 'again', platform='linux-amd64', revision='d' * 40, cache=cache)
    assert calls == [['run', 'build:agent-app']]


def test_inputs_are_verified(tmp_path):
    with pytest.raises(SystemExit, match='not available'):
        agent_build.fetch({'url': '', 'sha256': 'a' * 64}, tmp_path)
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs' / ('b' * 64)).write_text('tampered')
    with pytest.raises(SystemExit, match='not available'):
        agent_build.fetch({'url': '', 'sha256': 'b' * 64}, tmp_path)
    assert asyncio.iscoroutinefunction(app_build.run)
