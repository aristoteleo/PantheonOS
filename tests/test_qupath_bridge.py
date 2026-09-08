"""QuPath IPC publication, identity, and retry guarantees (no JVM required)."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest

_SOURCE = Path(__file__).parents[1] / "apps" / "qupath" / "bridge.py"
_SPEC = importlib.util.spec_from_file_location("qupath_bridge_tested", _SOURCE)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
QuPathBridge = _MODULE.QuPathBridge


@pytest.fixture
def bridge(tmp_path):
    return QuPathBridge(tmp_path / "ipc", "native-123")


def test_session_credentials_are_private_and_not_in_jvm_options(bridge):
    env = bridge.launch_environment()
    assert stat.S_IMODE(bridge.directory.stat().st_mode) == 0o700
    assert len(env["PANTHEON_QUPATH_BRIDGE_TOKEN"]) >= 32
    assert env["PANTHEON_QUPATH_BRIDGE_TOKEN"] not in bridge.startup_option()
    assert "qupath.startup.script=" in bridge.startup_option()
    assert (_SOURCE.with_name("bridge") / "startup.groovy").is_file()


@pytest.mark.asyncio
async def test_timeout_retains_request_and_retry_cannot_repeat_mutation(bridge):
    params = {"script": "return 42"}
    first = await bridge.call("script", params, request_id="stable", timeout=0)
    path = bridge.directory / "stable.request.json"
    before = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert first["state"] == "queued" and first["wait_timed_out"]
    again = await bridge.call("script", params, request_id="stable", timeout=0)
    assert again["request_id"] == "stable"
    assert path.read_bytes() == before
    assert len(list(bridge.directory.glob("*.request.json"))) == 1
    with pytest.raises(ValueError, match="different request"):
        await bridge.call("script", {"script": "return 43"}, request_id="stable", timeout=0)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_wait_observes_result_and_subsequent_retry_returns_same_result(bridge):
    async def pretend_jvm():
        path = bridge.directory / "test.request.json"
        while not path.exists():
            await asyncio.sleep(0.001)
        request = json.loads(path.read_text())
        assert request["token"] == bridge.launch_environment()["PANTHEON_QUPATH_BRIDGE_TOKEN"]
        response = {"session_id": bridge.session_id, "request_id": "test", "state": "succeeded", "result": 42}
        temporary = bridge.directory / ".reply.tmp"
        temporary.write_text(json.dumps(response))
        os.replace(temporary, bridge.directory / "test.response.json")

    task = asyncio.create_task(pretend_jvm())
    result = await bridge.call("script", {"script": "42"}, request_id="test", timeout=1)
    await task
    assert result["state"] == "succeeded" and result["result"] == 42
    assert await bridge.call("script", {"script": "42"}, request_id="test", timeout=0) == result
    assert "token" not in result


def test_stale_session_result_rejected(bridge):
    (bridge.directory / "ready.json").write_text(json.dumps({"session_id": "old-session"}))
    with pytest.raises(RuntimeError, match="identity mismatch"):
        bridge.ready()


@pytest.mark.parametrize("request_id", ["../secret", "/tmp/test", "a.b", "", "a" * 97])
def test_request_id_cannot_escape_private_directory(bridge, request_id):
    with pytest.raises(ValueError, match="request_id"):
        bridge.request_status(request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,params", [
    ("delete", {}), ("script", {"script": ""}),
    ("script", {"script": "42", "thread": "any"}),
    ("script", {"script": "42", "args": [23]}),
    ("state", {"annotation_limit": -1}),
    ("state", {"annotation_limit": 2001}),
    ("state", {"annotation_limit": True}),
    ("script", {"script": "x" * 600001}),
    ("script", {"script": "42", "expected_image": ""}),
    ("script", {"script": "42", "expected_image": 123}),
    ("script", {"script": "42", "expected_image": "a" * 97}),
    ("script", {"script": "42", "expected_image": {"token": "image"}}),
    *[("script", {"script": "42", "update_hierarchy": value})
      for value in [None, 0, 1, "false", [], {}]],
])
async def test_invalid_requests_are_never_published(bridge, method, params):
    with pytest.raises(ValueError):
        await bridge.call(method, params, timeout=0)
    assert list(bridge.directory.iterdir()) == []


def test_bridge_directory_cannot_be_reused(tmp_path):
    QuPathBridge(tmp_path / "ipc", "native-123")
    with pytest.raises(FileExistsError):
        QuPathBridge(tmp_path / "ipc", "native-123")


@pytest.mark.asyncio
async def test_concurrent_same_id_publishes_one_request(bridge):
    results = await asyncio.gather(*[
        bridge.call("script", {"script": "42"}, request_id="shared", timeout=0)
        for _ in range(8)
    ])
    assert {r["request_id"] for r in results} == {"shared"}
    assert len(list(bridge.directory.glob("*.request.json"))) == 1
    assert not list(bridge.directory.glob("*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", [None, "c0997054-1a8a-4a35-bfc6-3784b4d697ff"])
async def test_expected_image_is_part_of_the_idempotent_request(bridge, expected):
    params = {"script": "return 42", "expected_image": expected}
    await bridge.call("script", params, request_id="guarded", timeout=0)
    request = json.loads((bridge.directory / "guarded.request.json").read_text())
    assert "expected_image" in request["params"]
    assert request["params"]["expected_image"] == expected
    # A timeout retry cannot remove the image guard or retarget the mutation.
    with pytest.raises(ValueError, match="different request"):
        await bridge.call("script", {"script": "return 42"}, request_id="guarded", timeout=0)
    with pytest.raises(ValueError, match="different request"):
        await bridge.call("script", {"script": "return 42", "expected_image": "different-image"},
                          request_id="guarded", timeout=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [True, False])
async def test_hierarchy_notification_policy_is_retained_for_retries(bridge, update):
    params = {"script": "return 42", "update_hierarchy": update}
    await bridge.call("script", params, request_id="refresh-policy", timeout=0)
    request = json.loads((bridge.directory / "refresh-policy.request.json").read_text())
    assert request["params"]["update_hierarchy"] is update
    await bridge.call("script", params, request_id="refresh-policy", timeout=0)
    with pytest.raises(ValueError, match="different request"):
        await bridge.call("script", {**params, "update_hierarchy": not update},
                          request_id="refresh-policy", timeout=0)
