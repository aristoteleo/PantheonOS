"""End-to-end check: the Python FleetClient drives a Go Runner over NATS.

Run a JetStream NATS + a `fleet up` Node first, then:

    NATS=nats://localhost:4223 FLEET=test python test_e2e.py
"""

import asyncio
import os

from fleet_client import FleetClient


async def main() -> None:
    nats_url = os.environ.get("NATS", "nats://localhost:4223")
    fleet = os.environ.get("FLEET", "test")

    fc = await FleetClient.connect(nats_url, fleet)

    nodes = await fc.list_nodes()
    print(f"list_nodes: {len(nodes)} node(s)")
    assert nodes, "no nodes registered"
    n = nodes[0]
    cap = n["capability"]
    print(f"  {n['node_id']} {n['name']} {cap['os']}/{cap['arch']} {cap['cpu_cores']}c")
    nid = n["node_id"]

    r = await fc.run_on_node(nid, "echo from-python via fleet; uname -sm")
    print("run shell ->", repr(r["stdout"]))
    assert "from-python" in r["stdout"], r

    r = await fc.run_on_node(nid, "print('py-interop', 7 * 6)", kind="python")
    print("run python ->", repr(r["stdout"]))
    assert "py-interop 42" in r["stdout"], r

    p = await fc.ping(nid)
    print("ping ->", p)
    assert p.get("pong") == nid

    await fc.close()
    print("✅ Python<->Go interop OK")


if __name__ == "__main__":
    asyncio.run(main())
