"""Release delivery pins ordinary artifacts; startup remains AppDeployment's job."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.lifecycle import build_artifact, FleetLifecycle
from pantheon.apps.release_set import index_packages, stage_release_set
from pantheon.platform.model_dependency_package import build_package


@pytest.fixture
def distribution(tmp_path):
    root = tmp_path/'releases'
    root.mkdir()
    packages = {name: {p: build_package(root/p/name, p) for p in ('linux-amd64', 'darwin-arm64')}
                for name in ('first', 'second')}
    index = index_packages(root, packages)
    placements = {
        'first': dict(node_id='linux', platform='linux-amd64', scope='access-first', generation=0),
        'second': dict(node_id='mac', platform='darwin-arm64', scope='access-second', generation=4)}
    return root, index, placements


class Nodes:
    def __init__(self):
        self.uploads = []
        self.loss = False
        self.status = AsyncMock(side_effect=lambda node: dict(owner='owner', node_id=node, dependency_config_protocol=1))
        self.target_platform = AsyncMock(side_effect=lambda node: {'linux':'linux-amd64', 'mac':'darwin-arm64'}[node])

    async def stage_exact(self, node, payload, digest):
        self.uploads.append((node, payload, digest))
        if self.loss and node == 'mac':
            self.loss = False
            raise TimeoutError('lost reply')
        return digest


@pytest.mark.asyncio
async def test_deliver_mixed_platforms_preserves_artifacts_and_retry_identity(distribution):
    root, index, placements = distribution
    nodes = Nodes()
    nodes.loss = True
    with pytest.raises(TimeoutError):
        await stage_release_set(nodes, root, owner='owner', placements=placements)
    original = list(nodes.uploads)
    targets = await stage_release_set(nodes, root, owner='owner', placements=placements)
    assert nodes.uploads[2:] == original
    assert nodes.status.await_count == nodes.target_platform.await_count == 4
    assert targets['second']['generation'] == 4
    for name, target in targets.items():
        entry = index['apps'][name][placements[name]['platform']]
        assert target['revision'] == entry['revision']
        assert set(target) == {'node_id', 'revision', 'scope', 'generation'}
        expected, digest = build_artifact(root/entry['path'])
        assert (target['node_id'], expected, digest) in nodes.uploads
    assert index == json.loads((root/'release-set.json').read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['changed', 'missing-platform', 'escape', 'link', 'owner', 'node', 'protocol', 'platform', 'bad-generation', 'duplicate'])
async def test_all_inputs_checked_before_first_upload(distribution, fault):
    root, index, placements = distribution
    nodes = Nodes()
    entry = index['apps']['second']['darwin-arm64']
    if fault == 'changed': (root/entry['path']/'requirements.txt').write_text('changed')
    if fault == 'missing-platform': del index['apps']['second']['darwin-arm64']
    if fault == 'escape': entry['path'] = '../outside'
    if fault == 'link':
        path = root/entry['path']
        path.rename(path.with_name('saved'))
        path.symlink_to(path.with_name('saved'), target_is_directory=True)
    if fault in ('owner', 'node', 'protocol'):
        nodes.status.side_effect = lambda node: dict(owner='other' if fault=='owner' else 'owner',
            node_id='elsewhere' if fault=='node' else node, dependency_config_protocol=0 if fault=='protocol' else 1)
    if fault == 'platform': nodes.target_platform.side_effect = lambda _: 'linux-amd64'
    if fault == 'bad-generation': placements['second']['generation'] = True
    if fault == 'duplicate': placements['second'] = deepcopy(placements['first'])
    (root/'release-set.json').write_text(json.dumps(index))
    with pytest.raises((AssemblyError, ValueError)):
        await stage_release_set(nodes, root, owner='owner', placements=placements)
    assert not nodes.uploads
    if fault not in ('owner', 'node', 'protocol', 'platform'):
        nodes.status.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_source_bytes_survive_change_during_network_await(distribution):
    root, index, placements = distribution
    nodes = Nodes()
    original = build_artifact(root/index['apps']['second']['darwin-arm64']['path'])[0]
    async def status(node):
        (root/index['apps']['second']['darwin-arm64']['path']/'requirements.txt').write_text('changed later')
        return dict(owner='owner', node_id=node, dependency_config_protocol=1)
    nodes.status.side_effect = status
    await stage_release_set(nodes, root, owner='owner', placements=placements)
    assert nodes.uploads[-1][1] == original


@pytest.mark.asyncio
async def test_upload_cancellation_never_becomes_success(distribution):
    nodes = Nodes()
    entered = asyncio.Event()
    async def upload(*args):
        entered.set()
        await asyncio.Event().wait()
    nodes.stage_exact = upload
    root, _, placements = distribution
    task = asyncio.create_task(stage_release_set(nodes, root, owner='owner', placements=placements))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task


def test_index_refuses_overwrite_and_cross_platform_identity(distribution):
    root, index, _ = distribution
    before = (root/'release-set.json').read_bytes()
    with pytest.raises(FileExistsError):
        index_packages(root, {'first': {'linux-amd64':root/'linux-amd64/first'}})
    assert (root/'release-set.json').read_bytes() == before
    (root/'release-set.json').unlink()
    manifest = root/'darwin-arm64/first/app.json'
    value = json.loads(manifest.read_text()); value['version'] = '9.0.0'
    manifest.write_text(json.dumps(value))
    definition = manifest.with_name('fleet.json')
    value = json.loads(definition.read_text()); value['version'] = '9.0.0'
    definition.write_text(json.dumps(value))
    with pytest.raises(AssemblyError, match='same App identity'):
        index_packages(root, {'first': {p:root/p/'first' for p in ('linux-amd64', 'darwin-arm64')}})
    assert not (root/'release-set.json').exists()


@pytest.mark.asyncio
async def test_existing_multiplatform_app_uses_canonical_artifact_selection(tmp_path):
    import shutil
    from pantheon.apps.registry import BUILTIN_ROOT
    root = tmp_path/'set'; root.mkdir()
    package = root/'model-service'
    shutil.copytree(Path(BUILTIN_ROOT)/'model-service', package,
                    ignore=shutil.ignore_patterns('__pycache__', '.git', '*.pyc'))
    index_packages(root, {'models': {p: package for p in ('linux-amd64', 'darwin-arm64')}})
    nodes = Nodes()
    placements = {'models':dict(node_id='mac', platform='darwin-arm64', scope='models', generation=0)}
    result = await stage_release_set(nodes, root, owner='owner', placements=placements)
    payload, digest = build_artifact(package, 'darwin-arm64')
    assert nodes.uploads == [('mac', payload, digest)]
    assert result['models']['revision'] == digest


@pytest.mark.asyncio
async def test_target_platform_requires_authenticated_inventory():
    lifecycle = FleetLifecycle(None)
    lifecycle._client = AsyncMock()
    lifecycle._platforms['mac'] = 'darwin-arm64'
    assert await lifecycle.target_platform('mac') == 'darwin-arm64'
    lifecycle._client.assert_awaited_once_with('mac')
    with pytest.raises(ValueError): await lifecycle.target_platform('unknown')


def test_delivery_imports_no_agent_code():
    source = '''import importlib.abc, sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name.startswith(('pantheon.chatroom','pantheon.agent','pantheon.factory','pantheon.team')):
   raise AssertionError(name)
sys.meta_path.insert(0, Boundary())
import pantheon.apps.release_set
'''
    result = subprocess.run([sys.executable, '-c', source], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_build_real_paired_release_set_and_preserve_additional_dependencies(tmp_path):
    from pantheon.chatroom.release import build_release_set
    frontend, transport = os.environ.get('AGENT_APP_BUILD_DIR'), os.environ.get('AGENT_RELEASE_TRANSPORT')
    if not frontend or not transport:
        pytest.skip('Supply paired GUI and Fleet transport for release-set build acceptance')
    from pantheon.chatroom.package import _transport_platform
    platform = _transport_platform(Path(transport))
    extra = {'shell': {'range':'^0.6.0', 'uses':['shell@1'], 'binding':'runtime'}}
    root = build_release_set(tmp_path/'distribution', version='0.7.0', frontend=frontend,
                             transports={platform:transport}, dependencies=extra)
    index = json.loads((root/'release-set.json').read_text())
    assert set(index['apps']) == {'agent', 'allocator', 'model-access'}
    for alias, variants in index['apps'].items():
        entry = variants[platform]
        assert build_artifact(root/entry['path'])[1] == entry['revision']
    manifest = json.loads((root/platform/'agent/app.json').read_text())
    assert manifest['dependencies']['shell'] == extra['shell']
    assert manifest['entry'] == {'backend':'backend/__init__.py', 'frontend':'frontend/index.js'}
    assert not (root/platform/'agent/backend/_vendor/pantheon/chatroom/release.py').exists()
    assert not (root/platform/'agent/backend/_vendor/pantheon/models/credentials.py').exists()
    with pytest.raises(FileExistsError):
        build_release_set(root, version='0.7.0', frontend=frontend, transports={platform:transport})
    # A mismatched native binary fails the complete distribution atomically.
    wrong = 'linux-amd64' if platform != 'linux-amd64' else 'darwin-arm64'
    with pytest.raises(ValueError, match='target platform'):
        build_release_set(tmp_path/'broken', version='0.7.0', frontend=frontend, transports={wrong:transport})
    assert not (tmp_path/'broken').exists()
