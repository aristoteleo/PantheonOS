"""P1 of the App unification: schema, catalog triage, reflection, registry.

The load-bearing assertions:
  * every shipped ToolSet class is triaged in the catalog (nothing slips in
    untracked), and every catalog entry resolves to a real class;
  * class-level reflection equals what a live instance would register — the
    manifest cannot lie about the runtime;
  * committed manifests under docs/app-manifests/ are fresh, so a tool
    signature change shows up as a reviewable diff (the §06 idea in CI form).
"""

import json
from pathlib import Path

import pytest

from pantheon.apps import (
    CATALOG,
    all_apps,
    app_entries,
    emit_manifests,
    packaged_apps,
    parse_manifest,
    reflect_toolset_class,
    reflect_toolset_instance,
    signature_diff,
    toolset_apps,
)
from pantheon.apps.catalog import by_class_name


# ---- schema ----------------------------------------------------------------

def test_v1_atrium_manifest_parses_with_translation():
    m = parse_manifest({
        "id": "viv", "name": "Viv", "version": "1.0.0", "atriumApi": 1,
        "surface": "dom", "entry": {"frontend": "main.js"},
        "opens": [".ome.tif"], "launcher": True,
    })
    assert m.apiVersion == 1
    assert m.surface.value == "dom"
    assert m.entry.frontend == "main.js"


def test_newer_api_version_is_refused():
    with pytest.raises(ValueError, match="newer than supported"):
        parse_manifest({"id": "x", "name": "X", "apiVersion": 99})


def test_dependency_shorthand_coerces():
    m = parse_manifest({
        "id": "x", "name": "X",
        "dependencies": {"file-manager": ">=1.0",
                         "pty": {"range": "^2.0", "uses": ["pty@1"]}},
    })
    assert m.dependencies["file-manager"].range == ">=1.0"
    assert m.dependencies["pty"].uses == ["pty@1"]


def test_placement_requires_vocabulary_is_enforced():
    with pytest.raises(ValueError, match="unknown capabilities"):
        parse_manifest({"id": "x", "name": "X", "placement": {"requires": ["magic"]}})
    # prefer is free vocabulary (node kinds/labels)
    m = parse_manifest({"id": "x", "name": "X", "placement": {"prefer": ["sandbox", "hpc"]}})
    assert m.placement.prefer == ["sandbox", "hpc"]


# ---- catalog completeness ---------------------------------------------------

def _shipped_toolset_classes() -> set[str]:
    """Every ToolSet class shipped under pantheon/toolsets — from source, so a
    new class cannot dodge triage by staying out of __init__'s exports."""
    import re
    root = Path(__file__).resolve().parent.parent / "pantheon" / "toolsets"
    classes: set[str] = set()
    for py in root.rglob("*.py"):
        for match in re.finditer(r"^class ([A-Za-z0-9]+ToolSet)\b", py.read_text(encoding="utf-8"), re.M):
            classes.add(match.group(1))
    return classes


def test_every_shipped_toolset_class_is_triaged():
    triaged = set(by_class_name())
    shipped = _shipped_toolset_classes()
    untriaged = shipped - triaged
    assert not untriaged, (
        f"ToolSet classes with no catalog triage: {sorted(untriaged)} — "
        "add them to pantheon/apps/catalog.py (service/plugin/component/alias/absorb)"
    )


def test_every_catalog_entry_resolves_to_a_real_class():
    import importlib
    for entry in CATALOG:
        cls = getattr(importlib.import_module(entry.module), entry.class_name)
        assert isinstance(cls, type), entry.class_name


def test_components_and_aliases_name_a_real_parent_app():
    ids = {e.app_id for e in app_entries()}
    for entry in CATALOG:
        if entry.kind in ("component", "alias"):
            assert entry.parent in ids, f"{entry.app_id} parent {entry.parent!r} is not an app"


# ---- reflection -------------------------------------------------------------

def test_class_reflection_matches_live_instance():
    """The manifest's tools face must equal what a worker registers."""
    from pantheon.toolsets import FileManagerToolSet, ShellToolSet, WebToolSet

    for cls, kwargs in (
        (ShellToolSet, {"name": "shell"}),
        (FileManagerToolSet, {"name": "fm", "path": "."}),
        (WebToolSet, {"name": "web"}),
    ):
        diff = signature_diff(reflect_toolset_class(cls), reflect_toolset_instance(cls(**kwargs)))
        assert not diff, f"{cls.__name__}: {diff}"


def test_reflection_finds_known_tools():
    apps = {a.manifest.id: a.manifest for a in toolset_apps()}
    shell_tools = {t.name for t in apps["shell"].provides.tools}
    assert {"run_command", "new_shell", "close_shell"} <= shell_tools
    fm_tools = {t.name for t in apps["file-manager"].provides.tools}
    assert {"apply_patch", "create_directory", "delete_path"} <= fm_tools
    # params carry names and requiredness
    run_command = next(t for t in apps["shell"].provides.tools if t.name == "run_command")
    assert any(p.name == "command" for p in run_command.params)


def test_signature_diff_reports_breakage():
    a = reflect_toolset_class(__import__("pantheon.toolsets.shell", fromlist=["ShellToolSet"]).ShellToolSet)
    b = [s for s in a if s.name != "run_command"]
    problems = signature_diff(a, b)
    assert problems == ["tool removed: run_command"]


# ---- registry ---------------------------------------------------------------

def test_toolset_apps_cover_all_app_entries():
    reflected = {a.manifest.id for a in toolset_apps()}
    assert reflected == {e.app_id for e in app_entries()}


def test_packaged_apps_scan_precedence_and_resilience(tmp_path):
    ws = tmp_path / "ws" / ".pantheon" / "apps"
    user = tmp_path / "user" / ".pantheon" / "apps"
    # workspace app with a v2 manifest
    (ws / "alpha").mkdir(parents=True)
    (ws / "alpha" / "app.json").write_text(json.dumps(
        {"id": "alpha", "name": "Alpha", "apiVersion": 2, "surface": "dom",
         "entry": {"frontend": "main.js"}}))
    # same id in user scope must lose to workspace
    (user / "alpha").mkdir(parents=True)
    (user / "alpha" / "app.json").write_text(json.dumps(
        {"id": "alpha", "name": "AlphaOld", "apiVersion": 2}))
    # v1 manifest still accepted
    (user / "beta").mkdir(parents=True)
    (user / "beta" / "atrium.json").write_text(json.dumps(
        {"id": "beta", "name": "Beta", "atriumApi": 1, "surface": "dom",
         "entry": {"frontend": "m.js"}}))
    # garbage must be skipped, not raised
    (user / "broken").mkdir(parents=True)
    (user / "broken" / "app.json").write_text("{nope")

    apps = packaged_apps([(ws, "workspace"), (user, "user")])
    by_id = {a.manifest.id: a for a in apps}
    assert set(by_id) == {"alpha", "beta"}
    assert by_id["alpha"].manifest.name == "Alpha" and by_id["alpha"].scope == "workspace"
    assert by_id["beta"].manifest.apiVersion == 1


def test_packaged_app_cannot_shadow_builtin_toolset_app(tmp_path, monkeypatch):
    apps_dir = tmp_path / ".pantheon" / "apps" / "shell"
    apps_dir.mkdir(parents=True)
    (apps_dir / "app.json").write_text(json.dumps({"id": "shell", "name": "Evil"}))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    ids = [a.manifest.id for a in all_apps(workspace=tmp_path)]
    assert ids.count("shell") == 1


# ---- committed manifests stay fresh ----------------------------------------

MANIFEST_DIR = Path(__file__).resolve().parent.parent / "docs" / "app-manifests"


def test_committed_manifests_are_fresh(tmp_path):
    """Regenerate and compare with docs/app-manifests/. A mismatch means a
    tool signature changed without the manifest diff being committed — run
    `python -m pantheon.apps emit docs/app-manifests` and review the diff."""
    fresh = {p.name: p.read_text() for p in emit_manifests(tmp_path)}
    committed = {p.name: p.read_text() for p in MANIFEST_DIR.glob("*.app.json")}
    assert fresh == committed, (
        "docs/app-manifests is stale; regenerate with "
        "`python -m pantheon.apps emit docs/app-manifests` and commit the diff"
    )
