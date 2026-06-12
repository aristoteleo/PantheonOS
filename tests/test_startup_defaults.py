import asyncio
from pathlib import Path

import pytest

from pantheon.endpoint.core import Endpoint
from pantheon.factory.template_io import FileBasedTemplateManager
from pantheon.settings import load_jsonc
from pantheon.utils import log as pantheon_log
from pantheon.utils.log import log_startup_profile, startup_profile_enabled


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "pantheon" / "factory" / "templates"


def test_factory_defaults_only_autostart_file_manager():
    settings = load_jsonc(TEMPLATES_DIR / "settings.json")

    assert settings["services"]["builtin"] == ["file_manager"]


def test_default_team_keeps_package_for_lazy_dynamic_start():
    parser = FileBasedTemplateManager(Path("/tmp")).parser
    team = parser.parse_team((TEMPLATES_DIR / "teams" / "default.md").read_text())
    leader = next(agent for agent in team.agents if agent.id == "leader")

    assert "package" in leader.toolsets


def test_startup_profile_logs_default_enabled(monkeypatch):
    monkeypatch.delenv("PANTHEON_STARTUP_PROFILE", raising=False)

    assert startup_profile_enabled()


@pytest.mark.parametrize("value", ["0", "false", "False", "off", "no", "disabled"])
def test_startup_profile_logs_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv("PANTHEON_STARTUP_PROFILE", value)

    assert not startup_profile_enabled()


def test_startup_profile_log_helper_respects_disabled_env(monkeypatch):
    emitted = []

    def fake_info(message):
        emitted.append(message)

    monkeypatch.setattr(pantheon_log.logger, "info", fake_info)
    monkeypatch.setenv("PANTHEON_STARTUP_PROFILE", "0")

    log_startup_profile("hidden")

    assert emitted == []


@pytest.mark.asyncio
async def test_endpoint_background_startup_waits_until_worker_ready(monkeypatch):
    events: list[str] = []

    class FakeSettings:
        def get_mcp_config(self):
            return {"servers": {}, "auto_start": []}

    class FakeGateway:
        async def start_gateway(self):
            events.append("gateway_started")

    class FakeMCPManager:
        def __init__(self):
            self._gateway = FakeGateway()
            self.port = 3100

        async def load_config(self, _config):
            return {"errors": []}

        def get_unified_uri(self):
            return "http://localhost:3100/mcp"

        async def start_services(self, _services):
            events.append("mcp_services_started")
            return {"success": True, "started": []}

    class FakeToolSetManager:
        async def start_services(self, services, local_retries=10, remote_retries=10):
            events.append(f"builtin_started:{services}")
            return {"success": True, "started": services, "errors": []}

    endpoint = object.__new__(Endpoint)
    endpoint.config = {"builtin_services": []}
    endpoint.worker = object()
    endpoint._worker_ready = asyncio.Event()
    endpoint.mcp_manager = FakeMCPManager()
    endpoint.toolset_manager = FakeToolSetManager()

    async def services_ready():
        return True

    async def warmup():
        events.append("warmup_started")

    async def mount_endpoint_mcp():
        events.append("endpoint_mcp_mounted")

    endpoint.services_ready = services_ready
    endpoint._warmup_llm_connection = warmup
    endpoint._start_endpoint_mcp_server = mount_endpoint_mcp

    monkeypatch.setattr("pantheon.endpoint.core.get_settings", lambda: FakeSettings())

    await endpoint.run_setup()
    await asyncio.sleep(0)

    assert events == ["builtin_started:[]"]

    endpoint._worker_ready.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert "warmup_started" in events
    assert "gateway_started" in events
    assert "endpoint_mcp_mounted" in events
