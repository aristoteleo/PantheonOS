"""NodeEntry contract (§03) and the placement predicate."""

import pytest

from pantheon.apps.registry import builtin_apps
from pantheon.apps.nodes import NodeCapability, NodeEntry, NodeKind, NodeSystem


def _node(kind: NodeKind, caps: list[str], os: str = "linux") -> NodeEntry:
    return NodeEntry(
        node_id=f"{kind.value}-test",
        kind=kind,
        capability=NodeCapability(caps=caps),
        system=NodeSystem(os=os, arch="amd64", runtimes={"runner": "1.0.0"}),
    )


SANDBOX = ["proc", "fs:workspace", "display", "net"]
BRAIN_POD = ["net"]
FRONTEND = ["dom"]


def test_fits_is_subset_on_capabilities():
    sandbox = _node(NodeKind.sandbox, SANDBOX)
    assert sandbox.fits(["proc", "fs:workspace"])
    assert not sandbox.fits(["dom"])
    assert _node(NodeKind.frontend, FRONTEND).fits(["dom"])


def test_catalog_placement_maps_onto_node_kinds():
    """The design's brain/body table, executed: every service app fits the
    node class the triage intended — and does NOT fit the brain when it
    needs the sandbox."""
    sandbox = _node(NodeKind.sandbox, SANDBOX)
    brain = _node(NodeKind.pod, BRAIN_POD)
    for app in builtin_apps():
        requires = list(app.manifest.placement.requires)
        assert sandbox.fits(requires) or requires == ["dom"], app.manifest.id
        expects_brain = set(requires) <= {"net"}
        assert brain.fits(requires) == expects_brain, app.manifest.id


def test_unknown_capability_is_refused():
    with pytest.raises(ValueError, match="unknown capabilities"):
        NodeCapability(caps=["telepathy"])


def test_system_field_is_mandatory_and_validated():
    with pytest.raises(ValueError):
        NodeEntry(node_id="x", kind=NodeKind.machine)  # no system
    with pytest.raises(ValueError):
        NodeSystem(os="templeos", arch="amd64")
