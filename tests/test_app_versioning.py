"""P4 acceptance: fork → break → check-compat refuses → fix → publish →
install with a dependency closure. One git repo per App; a directory of
bare repos is the forge.
"""

import json
import subprocess
from pathlib import Path

import pytest

from pantheon.apps.compat import check_compat
from pantheon.apps.schema import parse_manifest
from pantheon.apps.versioning import (
    VersioningError,
    fork,
    install,
    last_published_tag,
    publish,
    publish_check,
)


def _manifest(app_id: str, version: str = "1.0.0", tools=None, ifaces=None, deps=None) -> dict:
    return {
        "id": app_id, "name": app_id, "version": version, "apiVersion": 2,
        "kind": "service", "surface": "headless",
        "entry": {"backend": f"pantheon.apps.builtin.{app_id}:X"},
        "provides": {
            "tools": tools if tools is not None else [
                {"name": "greet", "params": [
                    {"name": "who", "type": "str", "required": True},
                    {"name": "loud", "type": "bool", "required": False},
                ]},
                {"name": "leave", "params": []},
            ],
            "interfaces": ifaces if ifaces is not None else [
                {"name": "greeting", "version": 1, "tools": ["greet"]},
            ],
        },
        "dependencies": deps or {},
    }


def _mkapp(root: Path, app_id: str, **kw) -> Path:
    d = root / app_id
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps(_manifest(app_id, **kw), indent=1))
    (d / "backend.py").write_text("# body\n")
    return d


def _edit_manifest(app_dir: Path, fn) -> None:
    data = json.loads((app_dir / "app.json").read_text())
    fn(data)
    (app_dir / "app.json").write_text(json.dumps(data, indent=1))


def _bare_forge(work: Path, app_dir: Path) -> str:
    """Publish an App repo into a bare forge dir; returns the clone source."""
    bare = work / "forge" / f"{app_dir.name}.git"
    bare.parent.mkdir(exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(app_dir), str(bare)], check=True)
    return str(bare)


# ── check-compat semantics ─────────────────────────────────────────────────

def test_breaking_vs_additive_classification():
    old = parse_manifest(_manifest("a"))
    # additive: new tool + new optional param
    new = parse_manifest(_manifest("a", version="1.1.0", tools=[
        {"name": "greet", "params": [
            {"name": "who", "type": "str", "required": True},
            {"name": "loud", "type": "bool", "required": False},
            {"name": "lang", "type": "str", "required": False},
        ]},
        {"name": "leave", "params": []},
        {"name": "wave", "params": []},
    ]))
    r = check_compat(old, new)
    assert r.ok and not r.breaking and r.required_bump == "minor"

    # breaking: type change, without the bumps
    new2 = parse_manifest(_manifest("a", version="1.0.1", tools=[
        {"name": "greet", "params": [
            {"name": "who", "type": "int", "required": True},
            {"name": "loud", "type": "bool", "required": False},
        ]},
        {"name": "leave", "params": []},
    ]))
    r2 = check_compat(old, new2)
    assert not r2.ok and r2.required_bump == "major"
    assert any("type" in b for b in r2.breaking)
    # both gates fire: the interface carrying greet, and the semver
    assert any("interface greeting@1" in v for v in r2.violations)
    assert any("MAJOR" in v for v in r2.violations)

    # the same break, honestly declared: iface bump + major bump = OK
    new3 = parse_manifest(_manifest("a", version="2.0.0", tools=[
        {"name": "greet", "params": [{"name": "who", "type": "int", "required": True}]},
        {"name": "leave", "params": []},
    ], ifaces=[{"name": "greeting", "version": 2, "tools": ["greet"]}]))
    assert check_compat(old, new3).ok


def test_minor_required_for_additive():
    old = parse_manifest(_manifest("a"))
    new = parse_manifest(_manifest("a", version="1.0.1", tools=[
        {"name": "greet", "params": [
            {"name": "who", "type": "str", "required": True},
            {"name": "loud", "type": "bool", "required": False},
        ]},
        {"name": "leave", "params": []},
        {"name": "wave", "params": []},
    ]))
    r = check_compat(old, new)
    assert not r.ok and any("MINOR" in v for v in r.violations)


# ── the full agent flow ────────────────────────────────────────────────────

def test_fork_break_refuse_fix_publish(tmp_path):
    src = _mkapp(tmp_path / "builtin", "hello")
    workspace = tmp_path / "ws"

    forked = fork(src, workspace)
    assert (forked / ".git").is_dir()
    assert last_published_tag(forked) == "v1.0.0"

    # the agent breaks greet and forgets every bump
    _edit_manifest(forked, lambda d: (
        d["provides"]["tools"][0]["params"].pop(1),
        d.update(version="1.0.1"),
    ))
    report, tag = publish_check(forked)
    assert tag == "v1.0.0" and not report.ok
    with pytest.raises(VersioningError, match="publish refused"):
        publish(forked)

    # the exact fix the report demands: iface bump + major bump
    _edit_manifest(forked, lambda d: (
        d.update(version="2.0.0"),
        d["provides"]["interfaces"][0].update(version=2),
    ))
    assert publish(forked) == "v2.0.0"
    assert last_published_tag(forked) == "v2.0.0"

    # publishing the same version twice is refused
    with pytest.raises(VersioningError, match="already published"):
        publish(forked)


def test_install_closure_from_forge(tmp_path):
    lib = _mkapp(tmp_path / "src", "libgreet")
    app = _mkapp(tmp_path / "src", "hello",
                 deps={"libgreet": {"range": "^1.0.0"}})
    ws = tmp_path / "gitws"
    lib_repo = fork(lib, ws)
    app_repo = fork(app, ws)
    forge = {
        "libgreet": _bare_forge(tmp_path, lib_repo),
        "hello": _bare_forge(tmp_path, app_repo),
    }

    scope = tmp_path / "installed"
    got = install(forge["hello"], scope, resolve=lambda dep: forge[dep])
    assert {p.name for p in got} == {"hello", "libgreet"}
    assert (scope / "hello" / "app.json").is_file()
    assert (scope / "libgreet" / "backend.py").is_file()

    # flat resolution: an unsatisfiable range is refused, loudly
    _edit_manifest(scope / "hello",
                   lambda d: d["dependencies"].update(libgreet={"range": "^9.0.0"}))
    scope2 = tmp_path / "installed2"
    install(forge["libgreet"], scope2)  # pre-install 1.0.0
    with pytest.raises(VersioningError, match="needs libgreet"):
        install(str(scope / "hello"), scope2, resolve=lambda dep: forge[dep])


def test_install_without_index_requires_preinstalled(tmp_path):
    app = _mkapp(tmp_path / "src", "hello", deps={"libgreet": {"range": "*"}})
    with pytest.raises(VersioningError, match="no source index"):
        install(str(app), tmp_path / "scope")


def test_upgrade_gate_refuses_silent_breakage(tmp_path):
    v1 = _mkapp(tmp_path / "v1", "hello")
    scope = tmp_path / "scope"
    install(str(v1), scope)

    # v2 removes a tool but only bumps patch — the upgrade gate refuses
    v2 = _mkapp(tmp_path / "v2", "hello", version="1.0.1", tools=[
        {"name": "greet", "params": [
            {"name": "who", "type": "str", "required": True},
            {"name": "loud", "type": "bool", "required": False},
        ]},
    ])
    with pytest.raises(VersioningError, match="upgrade hello .* refused"):
        install(str(v2), scope)
    # the installed copy is untouched
    assert json.loads((scope / "hello" / "app.json").read_text())["version"] == "1.0.0"

    # force is the explicit escape hatch
    install(str(v2), scope, force=True)
    assert json.loads((scope / "hello" / "app.json").read_text())["version"] == "1.0.1"

    # an honest major upgrade passes on its own
    v3 = _mkapp(tmp_path / "v3", "hello", version="2.0.0", tools=[
        {"name": "greet", "params": [{"name": "who", "type": "str", "required": True}]},
    ], ifaces=[{"name": "greeting", "version": 2, "tools": ["greet"]}])
    install(str(v3), scope)
    assert json.loads((scope / "hello" / "app.json").read_text())["version"] == "2.0.0"
