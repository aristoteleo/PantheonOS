"""Read a Fleet-owned configuration snapshot without consulting Agent settings.

This file intentionally depends only on the standard library so ordinary App
backends can bundle it. A configured but invalid snapshot never falls back to
ambient credentials. Load once at startup; changes require a new prepared start.
Native process Apps retain their OS user's trust boundary.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


class RuntimeConfigurationError(ValueError):
    """An unavailable or stale configuration; messages contain no input data."""


@dataclass(frozen=True)
class RuntimeCredential:
    endpoint: str
    key: str = field(repr=False)


@dataclass(frozen=True)
class RuntimeConfiguration:
    values: Mapping[str, Any] = field(repr=False)
    credentials: Mapping[str, RuntimeCredential] = field(repr=False)
    instance_id: str
    revision: str
    generation: int
    component: str


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def load_runtime_configuration(*, required: bool = False) -> RuntimeConfiguration | None:
    """Load only this component's generation-bound ``PANTHEON_APP_CONFIG``.

    Missing optional configuration returns None; malformed or mismatched data
    raises even when optional. Configuration values are not environment variables.
    Credential endpoints must be used with their paired key by the consumer.
    """
    location = os.environ.get("PANTHEON_APP_CONFIG")
    if not location:
        if required:
            raise RuntimeConfigurationError("App runtime configuration is required")
        return None
    try:
        with Path(location).open("rb") as stream:
            raw = stream.read((256 << 10) + 1)
        if len(raw) > 256 << 10:
            raise ValueError
        value = json.loads(raw)
        expected = {
            "owner": "PANTHEON_FLEET_ID",
            "node_id": "PANTHEON_NODE_ID",
            "instance_id": "PANTHEON_INSTANCE_ID",
            "revision": "PANTHEON_APP_REVISION",
            "component": "PANTHEON_COMPONENT_NAME",
        }
        if not isinstance(value, dict) or set(value) != {
            "protocol", "generation", "values", "credentials", *expected,
        }:
            raise ValueError
        if type(value["protocol"]) is not int or value["protocol"] != 1:
            raise ValueError
        for key, env in expected.items():
            identity = os.environ.get(env)
            if not identity or value[key] != identity:
                raise ValueError
        generation = value["generation"]
        if type(generation) is not int or generation <= 0 or str(generation) != os.environ.get("PANTHEON_INSTANCE_GENERATION"):
            raise ValueError
        if not isinstance(value["values"], dict) or not isinstance(value["credentials"], dict):
            raise ValueError
        credentials = {}
        for name, credential in value["credentials"].items():
            if not isinstance(credential, dict) or set(credential) != {"endpoint", "key"}:
                raise ValueError
            if not all(isinstance(item, str) and item for item in credential.values()):
                raise ValueError
            credentials[name] = RuntimeCredential(**credential)
        return RuntimeConfiguration(
            _freeze(value["values"]), MappingProxyType(credentials),
            value["instance_id"], value["revision"], generation, value["component"],
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise RuntimeConfigurationError("App runtime configuration is unavailable, invalid or stale") from None
