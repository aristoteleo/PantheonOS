"""NodeEntry — what a Node writes into the fleet registry (§03).

P1 form: the contract only. Python validates and (de)serializes it; the Go
runner and the frontend shell will PRODUCE these entries in P2. Defined here
rather than in the fleet repo so the placement side (brain, hub, tests) and
the producing side agree through one schema — the cross-language boundary is
this JSON shape, not shared code.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from pantheon.apps.schema import CAPABILITIES


class NodeKind(str, Enum):
    sandbox = "sandbox"    # Modal sandbox (the body)
    pod = "pod"            # k8s pod (the brain, and future k8s bodies)
    machine = "machine"    # user laptop / HPC / VM joined via the runner
    frontend = "frontend"  # a browser session; dom-only, command-restricted


class NodeSystem(BaseModel):
    """The node's self-description — placement refuses incompatible targets
    (a linux/amd64 binary must not land on a darwin laptop)."""

    os: str = Field(pattern="^(linux|darwin|windows)$")
    arch: str = Field(pattern="^(amd64|arm64)$")
    kernel: Optional[str] = None
    runtimes: dict[str, str] = Field(default_factory=dict)  # python/pantheon/runner/browser → version


class NodeCapability(BaseModel):
    cpu_cores: Optional[float] = None
    ram_gb: Optional[float] = None
    gpu: Optional[str] = None
    caps: list[str] = Field(default_factory=list)

    @field_validator("caps")
    @classmethod
    def _known_caps(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in CAPABILITIES]
        if unknown:
            raise ValueError(f"unknown capabilities {unknown}; vocabulary is {CAPABILITIES}")
        return v


class Reachability(BaseModel):
    """How bytes reach this node's data plane (§04b): a public HTTPS base
    when one exists (sandbox tunnel, pod ingress), else via the bridge."""

    direct: Optional[str] = None
    bridged: bool = False


class InstanceState(BaseModel):
    """One running AppInstance, as the node reports it."""

    app_id: str
    version: str
    scope: str = "app"          # app | window | node
    service_id: Optional[str] = None
    health: str = Field(default="starting",
                        pattern="^(starting|healthy|degraded|stopped|crashed)$")


class NodeEntry(BaseModel):
    node_id: str
    kind: NodeKind
    labels: list[str] = Field(default_factory=list)
    capability: NodeCapability = Field(default_factory=NodeCapability)
    system: NodeSystem
    reachability: Reachability = Field(default_factory=Reachability)
    instances: list[InstanceState] = Field(default_factory=list)

    def fits(self, requires: list[str]) -> bool:
        """Can this node host an App with these placement requirements?
        The placer's core predicate: requires ⊆ declared caps."""
        return set(requires) <= set(self.capability.caps)
