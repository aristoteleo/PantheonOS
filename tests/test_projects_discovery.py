"""ProjectManager self-heal: re-discover on-disk projects missing from the registry.

Guards the Modal-sandbox bug where the project registry lived on ephemeral
~/.pantheon, so a restart dropped every sub-project's entry while the directories
survived on the persistent Volume — leaving the PROJECTS list empty even though
the work was still on disk.
"""
from pathlib import Path

from pantheon.chatroom.projects import ProjectManager, _friendly_default_name


def test_friendly_default_name_hides_volume_id():
    # The Modal Volume root (mount alias or real path) → "Workspace", not the id.
    assert _friendly_default_name("/workspace") == "Workspace"
    assert _friendly_default_name("/workspace/") == "Workspace"
    assert _friendly_default_name("/__modal/volumes/vo-cEm8TSQpvrlRvld6ZXQdpm") == "Workspace"
    # Sub-projects and local/desktop paths keep their real, meaningful basename.
    assert _friendly_default_name("/workspace/metabolomics_analysis") == "metabolomics_analysis"
    assert _friendly_default_name("/__modal/volumes/vo-abc/sub") == "sub"
    assert _friendly_default_name("/workspace/default_workspace") == "default_workspace"
    assert _friendly_default_name("/Users/me/my_project") == "my_project"


def _make_workspace(root: Path) -> Path:
    ws = root / "workspace"
    (ws / ".pantheon").mkdir(parents=True)                       # root = default project
    (ws / "sub_a" / ".pantheon" / "memory").mkdir(parents=True)  # sub-project A (had chats)
    (ws / "sub_b" / ".pantheon").mkdir(parents=True)             # sub-project B
    (ws / "not_a_project").mkdir()                               # no marker → not a project
    (ws / ".hidden" / ".pantheon").mkdir(parents=True)           # hidden → never a project
    return ws


def test_discovers_orphaned_subprojects(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # registry → temp, not real ~
    ws = _make_workspace(tmp_path)

    pm = ProjectManager(active_path=str(ws))
    names = {p["name"] for p in pm.list_projects()}

    assert {"sub_a", "sub_b"} <= names, f"orphaned sub-projects not recovered: {names}"
    assert "not_a_project" not in names, f"registered a dir with no .pantheon marker: {names}"
    assert ".hidden" not in names, f"registered a hidden dir: {names}"


def test_discovery_idempotent_across_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ws = _make_workspace(tmp_path)

    first = {p["name"] for p in ProjectManager(active_path=str(ws)).list_projects()}
    second = {p["name"] for p in ProjectManager(active_path=str(ws)).list_projects()}

    assert first == second, f"re-discovery not idempotent across restart: {first} vs {second}"
