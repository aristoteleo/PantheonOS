import json

import pytest

from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.apps.runtime_config import (
    RuntimeConfigurationError,
    load_runtime_configuration,
)


@pytest.fixture
def configuration(tmp_path, monkeypatch):
    identity = {
        "owner": ("PANTHEON_FLEET_ID", "owner"),
        "node_id": ("PANTHEON_NODE_ID", "node"),
        "instance_id": ("PANTHEON_INSTANCE_ID", "instance"),
        "revision": ("PANTHEON_APP_REVISION", "a" * 64),
        "component": ("PANTHEON_COMPONENT_NAME", "backend"),
    }
    for env, value in identity.values():
        monkeypatch.setenv(env, value)
    monkeypatch.setenv("PANTHEON_INSTANCE_GENERATION", "2")
    path = tmp_path / "config.json"
    monkeypatch.setenv("PANTHEON_APP_CONFIG", str(path))
    value = {
        "protocol": 1,
        **{key: value for key, (_, value) in identity.items()},
        "generation": 2,
        "values": {"route": {"models": ["test-model"]}},
        "credentials": {"provider": {"endpoint": "https://provider.example/v1", "key": "fixture-secret"}},
    }
    path.write_text(json.dumps(value))
    return path, value


def test_component_snapshot_is_immutable_and_repr_omits_secrets(configuration):
    path, _ = configuration
    cfg = load_runtime_configuration(required=True)
    assert cfg.values["route"]["models"] == ("test-model",)
    assert cfg.credentials["provider"].key == "fixture-secret"
    with pytest.raises(TypeError):
        cfg.values["route"]["models"] = ()
    with pytest.raises(TypeError):
        cfg.credentials["other"] = cfg.credentials["provider"]
    assert "fixture-secret" not in repr(cfg)
    assert "fixture-secret" not in repr(cfg.credentials["provider"])
    path.unlink()
    assert cfg.credentials["provider"].key == "fixture-secret"


@pytest.mark.parametrize("key", ["owner", "node_id", "instance_id", "revision", "component", "generation", "protocol"])
def test_rejects_wrong_identity_without_secret_in_exception(configuration, key):
    path, value = configuration
    value[key] = "fixture-secret"
    path.write_text(json.dumps(value))
    with pytest.raises(RuntimeConfigurationError) as error:
        load_runtime_configuration()
    assert "fixture-secret" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("body", ["{fixture-secret", "[]", "null", "x" * ((256 << 10) + 1)])
def test_invalid_optional_snapshot_never_falls_back(configuration, monkeypatch, body):
    path, _ = configuration
    path.write_text(body)
    monkeypatch.setenv("OPENAI_API_KEY", "ambient")
    with pytest.raises(RuntimeConfigurationError):
        load_runtime_configuration(required=False)


def test_absent_and_removed_configuration(configuration, monkeypatch):
    path, _ = configuration
    path.unlink()
    with pytest.raises(RuntimeConfigurationError):
        load_runtime_configuration()
    monkeypatch.delenv("PANTHEON_APP_CONFIG")
    assert load_runtime_configuration() is None
    with pytest.raises(RuntimeConfigurationError):
        load_runtime_configuration(required=True)


@pytest.mark.asyncio
async def test_configuration_control_request_is_exact_and_does_not_start(monkeypatch):
    client = FleetLifecycle(None)
    calls = []
    components = {"backend": {"values": {"route": "model"}}}

    async def request(node, method, **data):
        components["backend"]["values"]["route"] = "changed-after-await"
        calls.append((node, method, data))
        return {"ok": True}

    monkeypatch.setattr(client, "_request", request)
    assert await client.configure("node", instance_id="instance", revision="a" * 64,
                                  generation=1, preparation_id="prep", components=components) == {"ok": True}
    assert calls == [("node", "configure", {
        "instance_id": "instance", "revision": "a" * 64, "generation": 1,
        "configuration": {"preparation_id": "prep", "components": {
            "backend": {"values": {"route": "model"}},
        }},
    })]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("instance_id", "../instance"), ("revision", "latest"),
    ("generation", True), ("generation", 0), ("preparation_id", ""),
    ("components", {}), ("components", {"backend": {"values": {"x": float("nan")}}}),
    ("components", {"backend": {"values": {"x": "x" * (128 << 10)}}}),
])
async def test_invalid_configuration_is_not_sent(field, value):
    client = FleetLifecycle(None)
    kwargs = dict(instance_id="instance", revision="a" * 64, generation=1,
                  preparation_id="prep", components={"backend": {"values": {"route": "model"}}})
    kwargs[field] = value
    with pytest.raises(ValueError):
        await client.configure("node", **kwargs)
