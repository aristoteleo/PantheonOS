"""app.json v2 — the unified App manifest (design: "一切皆 App" §02).

One manifest describes every kind of App: a headed desktop app (dom surface),
a headless service (the toolset face), or both. v1 manifests (atrium.json,
`atriumApi`) are still readable during migration — see `parse_manifest`.

This module is the schema's single home: the pydantic models validate, and
`json_schema()` exports the same thing for editors and the store.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

#: The manifest revision this codebase implements.
API_VERSION = 2

#: Manifest file names, in resolution order (first found wins).
MANIFEST_NAMES = ("app.json", "atrium.json")

#: The capability vocabulary, shared verbatim between what a Node declares
#: and what an App requires. Adding a word here is a design decision, not a
#: convenience — the placer matches these by set inclusion.
CAPABILITIES = ("proc", "fs:workspace", "display", "gpu", "net", "dom")


class Surface(str, Enum):
    dom = "dom"
    stream = "stream"
    headless = "headless"
    dom_headless = "dom+headless"


class Runtime(str, Enum):
    """Execution form (§04c). Same tools contract in all three; callers are
    oblivious. `embedded` is first-party-only — the loader enforces origin."""

    embedded = "embedded"
    process = "process"
    builtin = "builtin"


class Entry(BaseModel):
    frontend: Optional[str] = None
    backend: Optional[str] = None
    backendInstance: str = Field(default="app", pattern="^(app|window|node)$")


class ToolParam(BaseModel):
    name: str
    type: Optional[str] = None
    description: Optional[str] = None
    required: bool = True
    default: Optional[Any] = None


class ToolSig(BaseModel):
    """One tool's machine-readable signature — the unit interface contracts
    (§06) diff. `hidden` mirrors @tool(exclude=True): callable over the bus
    but not offered to the LLM."""

    name: str
    description: Optional[str] = None
    params: list[ToolParam] = Field(default_factory=list)
    hidden: bool = False


class Interface(BaseModel):
    """A named, versioned group of tool signatures the App promises to keep.
    Breaking a member signature requires bumping `version` (and the app's
    major) — `app check-compat` enforces this."""

    name: str
    version: int = 1
    tools: list[str] = Field(default_factory=list)


class Provides(BaseModel):
    tools: list[ToolSig] = Field(default_factory=list)
    interfaces: list[Interface] = Field(default_factory=list)


class Placement(BaseModel):
    #: Capabilities the node MUST declare (validated against CAPABILITIES).
    requires: list[str] = Field(default_factory=list)
    #: Node kinds/labels to prefer (free vocabulary: "sandbox", "gpu", "hpc"…) —
    #: hints, not constraints, so they are not validated against a fixed list.
    prefer: list[str] = Field(default_factory=list)

    @field_validator("requires")
    @classmethod
    def _known_caps(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in CAPABILITIES]
        if unknown:
            raise ValueError(f"unknown capabilities {unknown}; vocabulary is {CAPABILITIES}")
        return v


class DependencySpec(BaseModel):
    """Resolution by semver range; safety by interface contract (§06)."""

    range: str = "*"
    uses: list[str] = Field(default_factory=list)  # e.g. ["fs@1"]


class ExposedPort(BaseModel):
    """A dynamic HTTP service the backend runs; the node maps it out (§04b).
    App code never touches network topology."""

    name: str
    port: int
    protocol: str = Field(default="http", pattern="^(http|ws)$")


class AppKind(str, Enum):
    """What sort of App this manifest defines. Components and legacy aliases
    are not Apps — they have no manifest of their own (see
    pantheon/apps/catalog.py's residual table)."""

    service = "service"
    plugin = "plugin"    # agent-pipeline hook loaded in the brain process
    absorb = "absorb"    # scheduled for absorption into other machinery


class AppManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str
    version: str = "0.0.0"
    apiVersion: int = API_VERSION
    description: Optional[str] = None
    kind: AppKind = AppKind.service
    surface: Surface = Surface.headless
    runtime: Runtime = Runtime.process
    #: absorb-kind apps: where the capability is headed.
    absorbInto: Optional[str] = None
    #: Go-rewrite batch marker: a runner-builtin implementation exists (or is
    #: planned); runtime stays as-is until check-compat parity certifies it.
    builtinTarget: bool = False
    notes: Optional[str] = None
    entry: Entry = Field(default_factory=Entry)
    provides: Provides = Field(default_factory=Provides)
    placement: Placement = Field(default_factory=Placement)
    dependencies: dict[str, DependencySpec] = Field(default_factory=dict)
    expose: Optional[dict[str, list[ExposedPort]]] = None

    # Headed-surface fields carried over from v1, semantics unchanged.
    opens: list[str] = Field(default_factory=list)
    defaultFor: list[str] = Field(default_factory=list)
    launcher: bool = False
    icon: Optional[dict[str, Any]] = None
    #: Preferred window size, `{"width": …, "height": …}`; the shell has a
    #: default for apps that don't care.
    defaultSize: Optional[dict[str, int]] = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    caps: Optional[dict[str, Any]] = None
    persistState: list[str] = Field(default_factory=list)
    sharedState: list[str] = Field(default_factory=list)
    menus: list[dict[str, Any]] = Field(default_factory=list)
    skill: Optional[str] = None
    prewarm: bool = False

    @field_validator("dependencies", mode="before")
    @classmethod
    def _coerce_dep_shorthand(cls, v):
        """Accept the v1 shorthand `{"file-manager": ">=1.0"}` as
        `{"file-manager": {"range": ">=1.0"}}`."""
        if isinstance(v, dict):
            return {
                k: ({"range": spec} if isinstance(spec, str) else spec)
                for k, spec in v.items()
            }
        return v


def parse_manifest(data: dict) -> AppManifest:
    """Parse a manifest dict, accepting v1 (atriumApi) with translation.

    v1 fields map 1:1; `atriumApi` becomes `apiVersion`. Anything newer than
    what this build implements is refused — same policy the shell has for
    ATRIUM_API today.
    """
    data = dict(data)
    if "atriumApi" in data and "apiVersion" not in data:
        data["apiVersion"] = data.pop("atriumApi")
    api = int(data.get("apiVersion", 1) or 1)
    if api > API_VERSION:
        raise ValueError(f"manifest apiVersion {api} is newer than supported {API_VERSION}")
    return AppManifest.model_validate(data)


def json_schema() -> dict:
    """The manifest's JSON Schema, for editors, the store, and CI."""
    return AppManifest.model_json_schema()
