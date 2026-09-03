"""Placement (resolver._place) against recorded fleet registries — no runners.

The records below are what the fleet KV held for a topology user on
staging (2026-09-03): the k8s agent pod (caps proc, python probed) and the
Modal workspace sandbox (caps proc/fs:workspace/display/net — but the
runner's runtime probe listed no python). The desktop App, a process
runtime requiring a display, was unplaceable from the agent pod: every
call failed "no node offering [...] has joined the fleet yet" while the
sandbox sat there, capable. Declared caps outrank the advisory probe.
"""

from types import SimpleNamespace

import pytest

from pantheon.apps.resolver import AppInstanceResolver, NotJoinedError

AGENT = {
    "node_id": "n_agent", "name": "agent-u", "kind": "pod",
    "capability": {"runtimes": {"python": "3.12.14", "runner": "0.1.0"},
                   "caps": ["proc"]},
}
SANDBOX_NO_PY = {
    "node_id": "n_sandbox", "name": "workspace-u", "kind": "sandbox",
    "capability": {"runtimes": {"git": "2.39.5", "runner": "0.1.0"},
                   "caps": ["proc", "fs:workspace", "display", "net"]},
}
SANDBOX_PY = {
    "node_id": "n_sandbox_py", "name": "workspace-py", "kind": "sandbox",
    "capability": {"runtimes": {"python": "3.12.14", "runner": "0.1.0"},
                   "caps": ["proc", "fs:workspace", "display", "net"]},
}


def _app(requires, runtime="process", prefer=()):
    return SimpleNamespace(manifest=SimpleNamespace(
        id="desktop",
        placement=SimpleNamespace(requires=list(requires), prefer=list(prefer)),
        runtime=SimpleNamespace(value=runtime)))


def _resolver(local_node: str, nodes: list[dict]) -> AppInstanceResolver:
    r = AppInstanceResolver("f_test", local_node, "seed", "/workspace")

    async def _list_nodes(max_age: float = 10.0):
        return nodes
    r._list_nodes = _list_nodes  # type: ignore[method-assign]
    return r


@pytest.mark.asyncio
async def test_declared_caps_outrank_missing_python_probe():
    r = _resolver("n_agent", [AGENT, SANDBOX_NO_PY])
    placed = await r._place(_app(["proc", "fs:workspace", "display"]))
    assert placed == "n_sandbox"


@pytest.mark.asyncio
async def test_probed_python_node_still_preferred():
    r = _resolver("n_agent", [AGENT, SANDBOX_NO_PY, SANDBOX_PY])
    placed = await r._place(_app(["proc", "fs:workspace", "display"]))
    assert placed == "n_sandbox_py"


@pytest.mark.asyncio
async def test_no_capable_node_still_waits():
    capless = {**SANDBOX_NO_PY, "capability": {"runtimes": {}, "caps": ["proc"]}}
    r = _resolver("n_agent", [AGENT, capless])
    with pytest.raises(NotJoinedError):
        await r._place(_app(["proc", "fs:workspace", "display"]))


@pytest.mark.asyncio
async def test_local_node_wins_when_it_fits():
    r = _resolver("n_sandbox", [AGENT, SANDBOX_NO_PY])
    placed = await r._place(_app(["proc", "fs:workspace", "display"]))
    assert placed == "n_sandbox"
