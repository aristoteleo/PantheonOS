"""Complete distribution acceptance using real builders and explicit GUI inputs."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.general_agent_preset import PROVIDERS
from pantheon.apps.general_agent_release import build_release
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.local_agent import native_platform


def options():
    return dict(version='0.7.0', frontend='missing-agent', notebook_frontend='missing-notebook',
                transport='missing-transport', model_aliases=['connector'])


@pytest.mark.parametrize('aliases', [['agent'], ['files-models'], ['shell'], ['x', 'x'],
                                    ['../escape'], ['a', 'b', 'c', 'd', 'e'], 'connector', [None]])
def test_invalid_model_aliases_do_not_build(tmp_path, aliases):
    with pytest.raises(AssemblyError, match='aliases'):
        build_release(tmp_path/'release', native_platform(), **{**options(), 'model_aliases': aliases})
    assert not list(tmp_path.iterdir())


def test_unsupported_target_does_not_build(tmp_path):
    with pytest.raises(AssemblyError, match='POSIX'):
        build_release(tmp_path/'release', 'windows-amd64', **options())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('symlink', [False, True])
def test_existing_destination_is_not_replaced(tmp_path, symlink):
    output = tmp_path/'release'
    if symlink:
        output.symlink_to(tmp_path/'missing')
    else:
        output.mkdir()
        (output/'owner-data').write_text('keep')
    with pytest.raises(FileExistsError):
        build_release(output, native_platform(), **options())
    assert output.is_symlink() if symlink else (output/'owner-data').read_text() == 'keep'


def test_late_build_failure_removes_partial_release(tmp_path, monkeypatch):
    from pantheon.apps.builtin.evolution import build_managed
    def fail(output, platform):
        # Desktop has already been packaged: this is not an early input failure.
        assert (output.parent/'desktop/app.json').is_file()
        raise RuntimeError('provider-build-failed')
    monkeypatch.setattr(build_managed, 'build', fail)
    with pytest.raises(RuntimeError, match='provider-build-failed'):
        build_release(tmp_path/'release', native_platform(), **options())
    assert not list(tmp_path.iterdir())


def test_complete_release_command_exact_contracts_and_artifacts(tmp_path):
    required = ('AGENT_APP_BUILD_DIR', 'AGENT_RELEASE_TRANSPORT')
    if any(not os.environ.get(name) for name in required):
        pytest.skip('Supply built Agent GUI and native Fleet transport')
    root = Path(__file__).resolve().parents[1]
    output = tmp_path/'release'
    built = subprocess.run([sys.executable, '-m', 'pantheon.apps.general_agent_release',
        '--output', str(output), '--platform', native_platform(), '--version', '0.7.0',
        '--frontend', os.environ['AGENT_APP_BUILD_DIR'],
        '--notebook-frontend', str(root/'apps/notebook/frontend'),
        '--transport', os.environ['AGENT_RELEASE_TRANSPORT'],
        '--model-app', 'connector', '--model-app', 'image-connector'],
        cwd=root, capture_output=True, text=True, timeout=180)
    assert built.returncode == 0, built.stderr
    index = json.loads((output/'release-set.json').read_text())
    assert set(index['apps']) == {*PROVIDERS, 'agent', 'allocator', 'model-access',
                                 'files-models', 'connector', 'image-connector'}
    agent = json.loads((output/'agent/app.json').read_text())
    for alias, variants in index['apps'].items():
        entry = variants[native_platform()]
        package = output/entry['path']
        payload, digest = build_artifact(package, native_platform())
        assert (len(payload), digest) == (entry['bytes'], entry['revision'])
        if alias not in PROVIDERS:
            continue
        manifest = json.loads((package/'app.json').read_text())
        dependency = agent['dependencies'][manifest['id']]
        assert dependency['range'] == manifest['version']
        assert set(dependency['uses']) == {
            f"{item['name']}@{item.get('version', 1)}" for item in manifest['provides']['interfaces']}
        assert dependency['binding'] == ('startup' if alias == 'files' else 'runtime')
    assert (output/'shell/shell').stat().st_mode & 0o111
    assert (output/'agent/frontend/index.js').read_bytes() == (Path(os.environ['AGENT_APP_BUILD_DIR'])/'index.js').read_bytes()
    assert (output/'notebook/frontend/index.js').read_bytes() == (root/'apps/notebook/frontend/index.js').read_bytes()
    assert (output/'connector/app.json').read_bytes() == (output/'image-connector/app.json').read_bytes()
    assert set(path.name for path in tmp_path.iterdir()) == {'release'}
