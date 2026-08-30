"""FleetPlugin — give the leader the Fleet toolset when a fleet is configured.

When the sandbox is wired to a Pantheon-Fleet (``FLEET_CONTROLLER_URL`` present in
the environment — injected by the hub per user), the agent gains a ``fleet``
toolset to see and run code on the user's remote compute nodes (their laptop,
servers, GPU boxes). The plugin gates strictly on that env, so a sandbox with no
fleet configured is completely unaffected (no toolset, no prompt).

Mirrors think_plugin.py: a registry-driven TeamPlugin that injects a toolset into
the leader and appends an instructions block so the leader knows the capability
exists (and doesn't answer about its own sandbox when asked about the user's
machines).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from pantheon.team.plugin import TeamPlugin

if TYPE_CHECKING:
    from pantheon.team.pantheon import PantheonTeam


FLEET_PROMPT = """
## Remote Compute (Pantheon-Fleet)

You are connected to the user's compute cluster — one or more **remote nodes**
(their laptop, a server, a GPU box, cloud VMs) — via a `fleet` toolset. These are
REAL machines the user added, separate from this sandbox.

- List/inspect nodes to see what's available (OS, CPU, RAM, GPU, load, labels).
- Run shell commands ON a specific node (or on nodes matching a label).
- Move files between nodes (transfer / gather / broadcast).
- ONE node is the machine you are running on — `fleet_list_nodes` marks it
  `is_self: true`. Treat it as a DATA-TRANSFER endpoint ONLY: pull a file from
  another node into your workspace with `transfer` dst_node="local" — e.g.
  transfer(src_node=<their machine>, src_path="~/Downloads/x.pdf",
  dst_node="local", dst_path="/workspace/x.pdf"). Do NOT run_on_node against the
  is_self node to run local commands — use the `shell` toolset for that (it runs
  right here, no fleet round-trip).

When the user says "my machine", "the remote node", "my laptop", "my GPU box",
"run this on my server", etc., they mean a **fleet node** — list the nodes first
to pick the right one, then run there. Do NOT report this sandbox's own
environment when the user is asking about their own machines.
""".strip()


def _fleet_configured() -> bool:
    """True when the hub wired this sandbox to a fleet (controller URL or a
    direct dev NATS url)."""
    return bool(
        os.environ.get("FLEET_CONTROLLER_URL") or os.environ.get("FLEET_NATS_URL")
    )


class FleetPlugin(TeamPlugin):
    """Inject the Fleet toolset + prompt into the leader when fleet-configured."""

    async def get_toolsets(self, team: "PantheonTeam") -> list[tuple[Any, list[str] | None]]:
        if not _fleet_configured() or not team.team_agents:
            return []
        try:
            from pantheon.apps.builtin.fleet import FleetToolSet
        except Exception:  # toolset missing (e.g. image without the fleet code)
            return []
        primary = team.team_agents[0]
        # Config resolves from env (FLEET_CONTROLLER_URL / FLEET_KEY, or the dev
        # FLEET_NATS_URL / FLEET_ID) inside FleetToolSet.__init__.
        return [(FleetToolSet(), [primary.name])]

    async def on_team_created(self, team: "PantheonTeam") -> None:
        if not _fleet_configured() or not team.team_agents:
            return
        primary = team.team_agents[0]
        if not getattr(primary, "instructions", None):
            return
        if "## Remote Compute (Pantheon-Fleet)" in primary.instructions:
            return
        primary.instructions += "\n\n" + FLEET_PROMPT


def _create_fleet_plugin(config: dict, settings: Any) -> FleetPlugin:
    """Factory function for the plugin registry."""
    return FleetPlugin()


from pantheon.team.plugin_registry import PluginDef, register_plugin

register_plugin(
    PluginDef(
        name="fleet_system",
        config_key="fleet_system",
        enabled_key="enabled",
        factory=_create_fleet_plugin,
        priority=20,
    )
)
