"""The tool wire format is a contract — this test is its tripwire.

pantheon.funcdesc's Description.to_json shape is consumed by NATS tool
registration, the manifests' provides.tools, and mirrored byte-for-byte by
the Go side (fleet/appsvc: Param{type,range,default,name,doc}, the
"not_defined" sentinel, desc() = {name,doc,inputs,outputs,side_effects}).
If this test fails, the change breaks Go builtins and every published
manifest, not just Python.
"""

import json

from pantheon.funcdesc import Description, parse_func


def sample(command: str, timeout: int = 5, shell_id: str | None = None) -> dict:
    """Run the thing."""
    return {}


def test_wire_shape_round_trip():
    desc = parse_func(sample)
    wire = json.loads(desc.to_json())

    assert set(wire) >= {"name", "doc", "inputs", "outputs", "side_effects"}
    assert wire["name"] == "sample"
    assert wire["doc"] == "Run the thing."

    by_name = {p["name"]: p for p in wire["inputs"]}
    assert set(by_name) == {"command", "timeout", "shell_id"}
    for p in wire["inputs"]:
        # The exact five keys the Go Param struct declares.
        assert set(p) == {"type", "range", "default", "name", "doc"}, p

    # funcdesc marks a required parameter with the "not_defined" sentinel —
    # the same constant Go's appsvc.NotDefined carries.
    assert by_name["command"]["default"] == "not_defined"
    assert by_name["timeout"]["default"] == 5
    assert by_name["shell_id"]["default"] is None

    # from_json is the receiving side of the same wire.
    back = Description.from_json(desc.to_json())
    assert [v.name for v in back.inputs] == [v.name for v in desc.inputs]


def test_manifest_tools_match_this_shape():
    """The committed Go-builtin manifests speak the same param keys."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "apps"
    for app in ("shell", "pty"):
        manifest = json.loads((root / app / "app.json").read_text())
        tools = manifest["provides"]["tools"]
        assert tools, f"{app} manifest lists no tools"
        for tool in tools:
            for p in tool.get("params", []):
                assert {"name", "type"} <= set(p), (app, tool["name"], p)
