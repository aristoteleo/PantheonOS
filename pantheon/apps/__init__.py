"""The unified App model (design: "一切皆 App").

P1 scope: describe, don't drive — the schema (app.json v2), the toolset
triage catalog, signature reflection, and the read-only unified registry.
Runtime behaviour is untouched until P3.
"""

from pantheon.apps.schema import (
    API_VERSION,
    CAPABILITIES,
    MANIFEST_NAMES,
    AppManifest,
    Interface,
    Runtime,
    Surface,
    ToolParam,
    ToolSig,
    json_schema,
    parse_manifest,
)
from pantheon.apps.catalog import CATALOG, CatalogEntry, app_entries, entries
from pantheon.apps.reflect import (
    reflect_toolset_class,
    reflect_toolset_instance,
    signature_diff,
)
from pantheon.apps.registry import (
    RegisteredApp,
    all_apps,
    emit_manifests,
    packaged_apps,
    toolset_apps,
)

__all__ = [
    "API_VERSION",
    "CAPABILITIES",
    "MANIFEST_NAMES",
    "AppManifest",
    "Interface",
    "Runtime",
    "Surface",
    "ToolParam",
    "ToolSig",
    "json_schema",
    "parse_manifest",
    "CATALOG",
    "CatalogEntry",
    "app_entries",
    "entries",
    "reflect_toolset_class",
    "reflect_toolset_instance",
    "signature_diff",
    "RegisteredApp",
    "all_apps",
    "emit_manifests",
    "packaged_apps",
    "toolset_apps",
]
