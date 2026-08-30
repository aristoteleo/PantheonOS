"""The unified App model (design: "一切皆 App").

An App is a directory whose root carries its definition (app.json). This
package is the framework that serves them: schema, manifest-scanning
registry, signature reflection (the manifest's honesty check), the
instance resolver/client, the proxy, and the apphost process shim.
First-party App implementations live under pantheon/apps/builtin/<dir>/.
"""

from pantheon.apps.schema import (
    API_VERSION,
    CAPABILITIES,
    MANIFEST_NAMES,
    AppKind,
    AppManifest,
    Interface,
    Runtime,
    Surface,
    ToolParam,
    ToolSig,
    json_schema,
    parse_manifest,
)
from pantheon.apps.catalog import NON_APP_CLASSES, NonAppClass, non_app_class_names
from pantheon.apps.reflect import (
    reflect_toolset_class,
    reflect_toolset_instance,
    signature_diff,
)
from pantheon.apps.registry import (
    BUILTIN_ROOT,
    RegisteredApp,
    all_apps,
    backend_class,
    builtin_apps,
    by_app_id,
    by_service_type,
    packaged_apps,
    refresh_manifest,
    service_type_of,
    verify_interfaces,
)

__all__ = [
    "API_VERSION",
    "CAPABILITIES",
    "MANIFEST_NAMES",
    "AppKind",
    "AppManifest",
    "Interface",
    "Runtime",
    "Surface",
    "ToolParam",
    "ToolSig",
    "json_schema",
    "parse_manifest",
    "NON_APP_CLASSES",
    "NonAppClass",
    "non_app_class_names",
    "reflect_toolset_class",
    "reflect_toolset_instance",
    "signature_diff",
    "BUILTIN_ROOT",
    "RegisteredApp",
    "all_apps",
    "backend_class",
    "builtin_apps",
    "by_app_id",
    "by_service_type",
    "packaged_apps",
    "refresh_manifest",
    "service_type_of",
    "verify_interfaces",
]
