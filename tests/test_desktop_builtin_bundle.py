"""The Desktop App ships the desktop's built-in Apps and serves them as its builtin scope."""
import json

import pytest

from pantheonos.apps.builtin.desktop.build_managed import bundle_builtin_apps


def app(root, name, manifest, frontend=None):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / 'app.json').write_text(json.dumps(manifest))
    if frontend:
        (directory / frontend).parent.mkdir(parents=True, exist_ok=True)
        (directory / frontend).write_text('export {}')
    (directory / '__pycache__').mkdir()
    (directory / '__pycache__' / 'x.pyc').write_bytes(b'')


def test_only_desktop_apps_with_frontends_are_bundled(tmp_path):
    apps = tmp_path / 'apps'
    app(apps, 'viewer', {'id': 'image-viewer', 'entry': {'frontend': 'frontend/main.js'}}, 'frontend/main.js')
    app(apps, 'files', {'id': 'files', 'entry': {'frontend': 'ui:files'}})
    app(apps, 'shell', {'id': 'shell', 'entry': {'backend': 'backend.py'}})
    bundle_builtin_apps(apps, tmp_path / 'out')
    assert sorted(p.name for p in (tmp_path / 'out').iterdir()) == ['files', 'viewer']
    assert not (tmp_path / 'out' / 'viewer' / '__pycache__').exists()


def test_an_unbuilt_frontend_stops_the_build(tmp_path):
    apps = tmp_path / 'apps'
    app(apps, 'viewer', {'id': 'image-viewer', 'entry': {'frontend': 'frontend/main.js'}})
    with pytest.raises(ValueError, match='build its frontend'):
        bundle_builtin_apps(apps, tmp_path / 'out')
