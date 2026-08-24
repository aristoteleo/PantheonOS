"""App packages install into the workspace app root the desktop discovers."""

import pytest

from pantheon.store.installer import PackageInstaller


APP_FILES = {
    "atrium.json": '{"id": "clock", "name": "Clock", "atriumApi": 1}',
    "frontend/index.js": "export default function () {}",
    "assets/icon.svg": "<svg/>",
}


@pytest.fixture
def installer(tmp_path):
    return PackageInstaller(work_dir=tmp_path)


def test_app_installs_under_workspace_apps_root(installer, tmp_path):
    written = installer.install("app", "clock", "README", dict(APP_FILES))
    root = tmp_path / ".pantheon" / "apps" / "clock"
    assert (root / "atrium.json").read_text() == APP_FILES["atrium.json"]
    assert (root / "frontend" / "index.js").exists()
    assert (root / "assets" / "icon.svg").exists()
    assert len(written) == 3


def test_app_id_prefixed_paths_are_tolerated(installer, tmp_path):
    files = {f"clock/{k}": v for k, v in APP_FILES.items()}
    installer.install("app", "clock", "README", files)
    assert (tmp_path / ".pantheon" / "apps" / "clock" / "atrium.json").exists()


def test_app_files_cannot_escape_the_package_dir(installer, tmp_path):
    files = {"../../evil.txt": "pwned"}
    with pytest.raises(ValueError, match="escapes"):
        installer.install("app", "clock", "README", files)
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / ".pantheon" / "evil.txt").exists()


def test_app_without_files_is_refused(installer):
    with pytest.raises(ValueError, match="no files"):
        installer.install("app", "clock", "README", None)


def test_app_uninstall_removes_the_whole_dir(installer, tmp_path):
    installer.install("app", "clock", "README", dict(APP_FILES))
    removed = installer.uninstall("app", "clock")
    assert not (tmp_path / ".pantheon" / "apps" / "clock").exists()
    assert removed
