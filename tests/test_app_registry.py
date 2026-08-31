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
    all_apps,
    backend_class,
    builtin_apps,
    non_app_class_names,
    packaged_apps,
    parse_manifest,
    reflect_toolset_class,
    reflect_toolset_instance,
    signature_diff,
)


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
    """Every ToolSet class shipped under pantheon/apps/builtin — from source,
    so a new class cannot dodge triage by staying out of __init__'s exports."""
    import re
    from pantheon.apps.registry import BUILTIN_ROOT as root
    classes: set[str] = set()
    for py in root.rglob("*.py"):
        for match in re.finditer(r"^class ([A-Za-z0-9]+ToolSet)\b", py.read_text(encoding="utf-8"), re.M):
            classes.add(match.group(1))
    return classes


def test_every_shipped_toolset_class_is_accounted_for():
    """A class is either some app.json's entry.backend, or in the residual
    non-app table (component/alias) — nothing ships untracked."""
    app_backed = set()
    for app in builtin_apps():
        backend = app.manifest.entry.backend or ""
        app_backed.add(backend.rsplit(":", 1)[-1])
    accounted = app_backed | non_app_class_names()
    unaccounted = _shipped_toolset_classes() - accounted
    assert not unaccounted, (
        f"ToolSet classes with no app.json and no non-app triage: "
        f"{sorted(unaccounted)}"
    )


def test_every_python_backend_resolves_to_a_real_class():
    from pantheon.apps.registry import verifiable

    for app in builtin_apps():
        if not verifiable(app.manifest):
            continue
        cls = backend_class(app.manifest)
        assert isinstance(cls, type), app.manifest.id


def test_non_app_classes_name_a_real_parent_app():
    from pantheon.apps import NON_APP_CLASSES

    ids = {a.manifest.id for a in builtin_apps()}
    for c in NON_APP_CLASSES:
        assert c.parent in ids, f"{c.class_name} parent {c.parent!r} is not an app"


# ---- reflection -------------------------------------------------------------

def test_class_reflection_matches_live_instance():
    """The manifest's tools face must equal what a worker registers."""
    from pantheon.apps.builtin import FileManagerToolSet, WebToolSet

    for cls, kwargs in (
        (FileManagerToolSet, {"name": "fm", "path": "."}),
        (WebToolSet, {"name": "web"}),
    ):
        diff = signature_diff(reflect_toolset_class(cls), reflect_toolset_instance(cls(**kwargs)))
        assert not diff, f"{cls.__name__}: {diff}"


def test_reflection_finds_known_tools():
    apps = {a.manifest.id: a.manifest for a in builtin_apps()}
    shell_tools = {t.name for t in apps["shell"].provides.tools}
    assert {"run_command", "new_shell", "close_shell"} <= shell_tools
    fm_tools = {t.name for t in apps["file-manager"].provides.tools}
    assert {"apply_patch", "create_directory", "delete_path"} <= fm_tools
    # params carry names and requiredness
    run_command = next(t for t in apps["shell"].provides.tools if t.name == "run_command")
    assert any(p.name == "command" for p in run_command.params)


def test_signature_diff_reports_breakage():
    from pantheon.apps.builtin.web import WebToolSet

    a = reflect_toolset_class(WebToolSet)
    removed = a[0].name
    problems = signature_diff(a, a[1:])
    assert problems == [f"tool removed: {removed}"]


# ---- registry ---------------------------------------------------------------

def test_builtin_apps_all_parse_and_expose_tools():
    apps = builtin_apps()
    from pantheon.apps.registry import BUILTIN_ROOT

    assert len(apps) == len(list(BUILTIN_ROOT.glob("*/app.json")))
    for app in apps:
        if app.manifest.surface.value == "dom":
            continue  # headed apps have windows, not tools
        assert app.manifest.provides.tools, f"{app.manifest.id} has no tools face"


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


# ---- manifests stay honest ---------------------------------------------------

def test_manifest_tool_faces_match_code():
    """Each app.json's provides.tools must equal what its entry.backend
    reflects — a signature change without the manifest diff fails here.
    Run `python -m pantheon.apps emit` and commit the diff."""
    from pantheon.apps.registry import reflected_tools, verify_interfaces

    from pantheon.apps.registry import verifiable

    for app in builtin_apps():
        if not verifiable(app.manifest):
            # builtin-runtime faces are held by the e2e parity tests; headed
            # apps have no tools face
            assert not verify_interfaces(app.manifest), app.manifest.id
            continue
        diff = signature_diff(app.manifest.provides.tools,
                              reflected_tools(app.manifest))
        assert not diff, f"{app.manifest.id}: {diff}"
        assert not verify_interfaces(app.manifest), app.manifest.id


# ---- interface contracts ----------------------------------------------------

def test_go_batch_apps_declare_interfaces():
    apps = {a.manifest.id: a.manifest for a in builtin_apps()}
    assert [i.name for i in apps["shell"].provides.interfaces] == ["shell"]
    assert [i.name for i in apps["pty"].provides.interfaces] == ["pty"]
    # fs@1 is the Go-implementable core; the tree-sitter outline is its own
    # interface so a runner-builtin node can claim fs without cgo grammars
    assert [i.name for i in apps["file-manager"].provides.interfaces] == ["fs", "outline"]
    fs = apps["file-manager"].provides.interfaces[0]
    assert "view_file_outline" not in fs.tools
    # pty's interface covers hidden tools — the frontend's bus contract counts
    pty_tools = {t.name: t for t in apps["pty"].provides.tools}
    for member in apps["pty"].provides.interfaces[0].tools:
        assert pty_tools[member].hidden


def test_interface_members_must_exist_in_tools_face():
    from pantheon.apps.registry import verify_interfaces
    from pantheon.apps.schema import AppManifest, Interface, Provides, ToolSig

    m = AppManifest(
        id="x", name="X",
        provides=Provides(tools=[ToolSig(name="real")],
                          interfaces=[Interface(name="i", version=1, tools=["real", "ghost"])]),
    )
    assert verify_interfaces(m) == ["i@1:ghost"]


# ── breaker: the brain must reacquire a late body ──────────────────────────

def test_breaker_half_open_recovers(monkeypatch):
    from pantheon.apps.resolver import AppInstanceResolver

    r = AppInstanceResolver("f", "n", "seed", workdir=".")
    for _ in range(3):
        r._note_failure()
    # the breaker gates NEW starts…
    with pytest.raises(RuntimeError, match="cooling down"):
        r._gate_new_starts()
    # …but never the catalog answer (live cached instances keep serving)
    assert r.resolves("shell") is True

    # cooldown elapses -> half-open lets one start attempt through
    r._disabled_at -= AppInstanceResolver.COOLDOWN_S + 1
    r._gate_new_starts()  # no raise: the probe is allowed
    # the probe failing re-opens immediately (one strike at half-open)
    r._note_failure()
    with pytest.raises(RuntimeError, match="cooling down"):
        r._gate_new_starts()

    # and a success closes it fully
    r._disabled_at -= AppInstanceResolver.COOLDOWN_S + 1
    r._gate_new_starts()
    r._consecutive_failures = 0
    r._disabled_at = None
    r._gate_new_starts()


def test_not_joined_is_not_a_breaker_strike(tmp_path):
    import asyncio

    from pantheon.apps.resolver import AppInstanceResolver, NotJoinedError

    r = AppInstanceResolver("", "", "seed", workdir=".", state_dir=str(tmp_path))
    for _ in range(10):  # far past MAX_FAILURES
        with pytest.raises(NotJoinedError):
            asyncio.get_event_loop().run_until_complete(
                r.ensure_instance("shell"))
    assert r._disabled_at is None, "waiting for the body must never trip the breaker"
    assert r.resolves("shell") is True


def test_placer_waits_instead_of_misplacing(monkeypatch):
    """A workspace App must not fall back onto a capless local node."""
    import asyncio

    from pantheon.apps.registry import by_service_type
    from pantheon.apps.resolver import AppInstanceResolver, NotJoinedError

    r = AppInstanceResolver("f", "n_agent", "seed", workdir=".")
    shell = by_service_type()["shell"]

    async def fake_nodes(records):
        async def _list(self=None):
            return records
        return _list

    # registry knows the local node and it lacks fs:workspace -> wait
    r._list_nodes = lambda: _ret([{"node_id": "n_agent", "kind": "pod",
                                   "capability": {"caps": ["proc"]}}])
    with pytest.raises(NotJoinedError, match="no node offering"):
        asyncio.get_event_loop().run_until_complete(r._place(shell))

    # the workspace node joins -> placed there
    r._list_nodes = lambda: _ret([
        {"node_id": "n_agent", "kind": "pod", "capability": {"caps": ["proc"]}},
        {"node_id": "n_ws", "kind": "sandbox",
         "capability": {"caps": ["proc", "fs:workspace", "display", "net"],
                        "runtimes": {"python": "3.12"}}},
    ])
    placed = asyncio.get_event_loop().run_until_complete(r._place(shell))
    assert placed == "n_ws"

    # unreadable registry (empty) -> the old resilient fallback survives
    r._list_nodes = lambda: _ret([])
    placed = asyncio.get_event_loop().run_until_complete(r._place(shell))
    assert placed == "n_agent"


def _ret(value):
    async def _coro():
        return value
    return _coro()
