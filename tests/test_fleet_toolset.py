"""Tests for the Fleet toolset (pantheonos.appkit.builtin.fleet.FleetToolSet).

Two layers:
  * structural / graceful-degradation tests — no infrastructure, always run.
  * a live integration test — gated on FLEET_E2E_NATS + FLEET_E2E_FLEET env,
    pointing at a running Fleet (nats-server + at least one `fleet up` node).
"""

import os

import pytest

from pantheonos.appkit.builtin import FleetToolSet

EXPECTED_TOOLS = {
    "fleet_list_nodes",
    "fleet_node_info",
    "fleet_status",
    "fleet_pick_node",
    "run_on_node",
    "run_on_label",
    "transfer",
    "transfer_status",
    "broadcast",
    "gather",
}

_FLEET_ENV = ("FLEET_NATS_URL", "FLEET_ID", "FLEET_CONTROLLER_URL", "FLEET_KEY", "PANTHEON_API_KEY")


def test_fleet_toolset_lazy_import():
    """FleetToolSet resolves via the builtin package's lazy _TOOLSET_MAPPING.

    (Formerly asserted against pantheon.toolsets, which no longer exists in
    this tree — a stale editable install of the OLD layout could shadow it
    and make this pass/fail for the wrong reasons.)
    """
    from pantheonos.appkit import builtin

    assert "FleetToolSet" in builtin._TOOLSET_MAPPING
    assert builtin.FleetToolSet is FleetToolSet


def test_fleet_toolset_exposes_expected_tools():
    ts = FleetToolSet("fleet")
    assert EXPECTED_TOOLS.issubset(set(ts.tool_functions.keys()))


async def test_fleet_toolset_list_tools_schema():
    """The @tool docstrings/signatures parse into LLM schemas."""
    ts = FleetToolSet("fleet")
    res = await ts.list_tools()
    assert res["success"]
    names = {t["name"] for t in res["tools"]}
    assert EXPECTED_TOOLS.issubset(names)
    # Every exposed tool carries a non-empty description (the `doc` field).
    for t in res["tools"]:
        if t["name"] in EXPECTED_TOOLS:
            assert (t.get("doc") or "").strip(), f"{t['name']} has no doc"


async def test_fleet_toolset_unconfigured_is_graceful(monkeypatch):
    """With no Fleet configured, tools return a helpful error, never raise."""
    for k in _FLEET_ENV:
        monkeypatch.delenv(k, raising=False)
    ts = FleetToolSet("fleet")
    # run_setup must not raise even with nothing configured.
    await ts.run_setup()

    r = await ts.fleet_list_nodes()
    assert r["success"] is False and "configured" in r["error"].lower()

    r = await ts.run_on_node("n_whatever", "echo hi")
    assert r["success"] is False and "configured" in r["error"].lower()

    r = await ts.transfer("n_a", "/x", "n_b", "/y")
    assert r["success"] is False and "configured" in r["error"].lower()

    r = await ts.broadcast("n_a", "/x", ["n_b", "n_c"], "/y")
    assert r["success"] is False and "configured" in r["error"].lower()

    r = await ts.run_on_label("gpu", "echo hi")
    assert r["success"] is False  # no fleet -> read fails / no nodes

    r = await ts.transfer_status("x_nope")
    assert r["success"] is False  # unknown transfer id

    await ts.cleanup()


async def test_transfer_resolves_aliases_and_names_at_both_ends(monkeypatch):
    """A name or "local" at either end becomes a node_id before the command goes
    out: sent as-is it names no cmd subject and fails with "no responders"."""
    ts = FleetToolSet("fleet")
    nodes = [
        {"node_id": "n_sandbox", "name": "sandbox-u1"},
        {"node_id": "n_box", "name": "lab-box"},
    ]
    started = []

    async def connected():
        return None

    async def read_nodes():
        return nodes

    async def local_node(snapshot=None):
        return "n_sandbox"

    def start(src, src_path, dst, dst_path, *rest):
        started.append((src, dst))
        return "x_test"

    monkeypatch.setattr(ts, "_ensure_connected", connected)
    monkeypatch.setattr(ts, "_read_nodes", read_nodes)
    monkeypatch.setattr(ts, "_resolve_local_node", local_node)
    monkeypatch.setattr(ts, "_start_transfer", start)

    for src, dst in (("local", "lab-box"), ("LAB-BOX", "here"), ("sandbox-u1", "n_box"), ("n_sandbox", "n_box")):
        r = await ts.transfer(src, "/a", dst, "/b")
        assert r["success"] is True
    assert started == [("n_sandbox", "n_box"), ("n_box", "n_sandbox"),
                       ("n_sandbox", "n_box"), ("n_sandbox", "n_box")]

    r = await ts.transfer("nowhere", "/a", "lab-box", "/b")
    assert r["success"] is False and r["error"].startswith("src_node: no Node 'nowhere'")
    assert "lab-box (n_box)" in r["error"]
    assert len(started) == 4  # nothing sent for an unknown Node

    async def no_registry():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(ts, "_read_nodes", no_registry)
    r = await ts.transfer("n_x", "/a", "n_y", "/b")  # ids pass through as before
    assert r["success"] is True and started[-1] == ("n_x", "n_y")


# --- live integration (opt-in) ------------------------------------------------

_E2E_NATS = os.environ.get("FLEET_E2E_NATS")
_E2E_FLEET = os.environ.get("FLEET_E2E_FLEET")


@pytest.mark.skipif(
    not (_E2E_NATS and _E2E_FLEET),
    reason="set FLEET_E2E_NATS + FLEET_E2E_FLEET (a running fleet) to run the integration test",
)
async def test_fleet_toolset_live_smoke():
    ts = FleetToolSet("fleet", nats_url=_E2E_NATS, fleet_id=_E2E_FLEET)
    await ts.run_setup()
    r = await ts.fleet_list_nodes()
    assert r["success"], r
    assert r["count"] >= 1, "expected at least one node in the fleet"
    node = r["nodes"][0]["node_id"]

    info = await ts.fleet_node_info(node)
    assert info["success"] and info["node"]["node_id"] == node

    status = await ts.fleet_status()
    assert status["success"] and status["nodes_online"] >= 1

    run = await ts.run_on_node(node, "echo fleet-e2e", kind="shell")
    assert run["success"] and "fleet-e2e" in run["stdout"]

    # run_on_label must always return a dict (matching 0 nodes is fine here).
    labelled = await ts.run_on_label("__nope__", "echo x")
    assert isinstance(labelled, dict) and "success" in labelled
    await ts.cleanup()
