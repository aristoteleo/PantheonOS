import asyncio
from pathlib import Path

import pytest

from pantheon.apps.builtin.mcp.gateway import UnifiedMCPGateway
from pantheon.factory.template_io import FileBasedTemplateManager
from pantheon.factory.template_manager import TemplateManager
from pantheon.settings import load_jsonc
from pantheon.utils import log as pantheon_log
from pantheon.utils.log import log_startup_profile, startup_profile_enabled


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "pantheon" / "factory" / "templates"


def test_factory_defaults_only_autostart_file_manager():
    settings = load_jsonc(TEMPLATES_DIR / "settings.json")

    assert settings["services"]["builtin"] == ["file_manager"]


def test_factory_default_memory_selection_model_is_low():
    settings = load_jsonc(TEMPLATES_DIR / "settings.json")

    assert settings["memory_system"]["selection_model"] == "low"


def test_default_team_has_no_retired_toolsets():
    parser = FileBasedTemplateManager(Path("/tmp")).parser
    team = parser.parse_team((TEMPLATES_DIR / "teams" / "default.md").read_text())
    leader = next(agent for agent in team.agents if agent.id == "leader")

    # 'package' died with the toolset cleanup; every declared toolset must
    # be a catalog App (or the think pseudo-toolset)
    from pantheon.apps.catalog import by_service_type
    known = set(by_service_type()) | {"think", "task"}
    assert set(leader.toolsets) <= known, set(leader.toolsets) - known


def test_factory_runtime_fallback_assets_are_included_in_package_metadata():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    manifest = Path(__file__).resolve().parents[1] / "MANIFEST.in"

    pyproject_text = pyproject.read_text(encoding="utf-8")
    manifest_text = manifest.read_text(encoding="utf-8")

    for pattern in [
        '"templates/**/*.js"',
        '"templates/**/*.css"',
        '"templates/**/*.py"',
        '"templates/**/.gitkeep"',
    ]:
        assert pattern in pyproject_text

    assert "recursive-include pantheon/factory/templates *.md *.json *.example *.js *.css *.py .gitkeep" in manifest_text


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


def test_factory_templates_do_not_sync_to_global_in_runtime_mode(monkeypatch, tmp_path):
    # Sandbox startup should not copy factory templates into ephemeral HOME.
    # Runtime loading falls back to the packaged factory templates instead.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PANTHEON_FACTORY_TEMPLATE_MODE", raising=False)

    manager = TemplateManager(work_dir=tmp_path / "workspace")

    assert not (manager.settings.global_teams_dir / "default.md").exists()
    assert not (manager.teams_dir / "default.md").exists()


def test_force_sync_materializes_to_global_from_runtime_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PANTHEON_FACTORY_TEMPLATE_MODE", raising=False)

    manager = TemplateManager(work_dir=tmp_path / "workspace")
    assert not (manager.settings.global_teams_dir / "default.md").exists()

    total = manager.force_sync_factory_templates()

    assert total > 0
    assert (manager.settings.global_teams_dir / "default.md").exists()


def test_global_mode_materializes_to_global_on_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PANTHEON_FACTORY_TEMPLATE_MODE", "global")

    manager = TemplateManager(work_dir=tmp_path / "workspace")

    assert (manager.settings.global_teams_dir / "default.md").exists()
    assert not (manager.teams_dir / "default.md").exists()


def test_project_mode_is_not_supported_for_factory_materialization(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PANTHEON_FACTORY_TEMPLATE_MODE", "project")

    manager = TemplateManager(work_dir=tmp_path / "workspace")

    assert not (manager.settings.global_teams_dir / "default.md").exists()
    assert not (manager.teams_dir / "default.md").exists()


def test_bootstrap_reclaims_stale_factory_skill_from_project_keeps_user_skill(monkeypatch, tmp_path):
    # Reproduces the Modal-freeze state: a stale factory-origin skill + a
    # user-created skill, both pre-seeded in the PROJECT scope. bootstrap should
    # reclaim the factory-origin one (now served fresh from factory fallback)
    # and keep the user-created one.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PANTHEON_FACTORY_TEMPLATE_MODE", raising=False)
    pdir = tmp_path / "workspace" / ".pantheon"
    stale = pdir / "skills" / "desktop" / "SKILL.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("STALE copy\n", encoding="utf-8")
    user_skill = pdir / "skills" / "openclaw-medical_x" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text("user custom\n", encoding="utf-8")

    manager = TemplateManager(work_dir=tmp_path / "workspace")

    assert not stale.exists()  # factory-origin reclaimed from project
    assert (manager.system_templates_dir / "skills" / "desktop" / "SKILL.md").exists()
    assert not (manager.settings.global_skills_dir / "desktop" / "SKILL.md").exists()
    assert user_skill.exists()  # user-created skill preserved


def test_bootstrap_removes_a_retired_skill_tree(monkeypatch, tmp_path):
    """Withdrawing a template from the factory does not withdraw anyone's copy.

    Reclaim only removes project files the factory STILL ships — that is what
    makes it safe for user-created content — so a deleted tree stops looking
    factory-origin and is kept. On Modal that copy sits on a volume outliving
    every deploy, and the agent goes on loading skills for tools that no longer
    exist. Retirement has to be explicit.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PANTHEON_FACTORY_TEMPLATE_MODE", raising=False)
    pdir = tmp_path / "workspace" / ".pantheon"
    retired = pdir / "skills" / "live_view" / "gosling" / "gosling.md"
    retired.parent.mkdir(parents=True)
    retired.write_text("open_live_view(view_type='gosling')\n", encoding="utf-8")
    keeper = pdir / "skills" / "openclaw-medical_x" / "SKILL.md"
    keeper.parent.mkdir(parents=True)
    keeper.write_text("user custom\n", encoding="utf-8")

    TemplateManager(work_dir=tmp_path / "workspace")

    assert not retired.exists()
    assert not retired.parent.parent.exists()   # the whole tree, not one file
    assert keeper.exists()                      # nothing else was touched


def test_global_template_sync_preserves_user_modified_files(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PANTHEON_FACTORY_TEMPLATE_MODE", "global")

    manager = TemplateManager(work_dir=tmp_path / "workspace")
    factory_dir = tmp_path / "factory"
    (factory_dir / "teams").mkdir(parents=True)
    (factory_dir / "teams" / "default.md").write_text("factory v1\n", encoding="utf-8")

    manager.system_templates_dir = factory_dir
    manager.force_sync_factory_templates()

    global_team = manager.settings.global_teams_dir / "default.md"
    global_team.write_text("user edited\n", encoding="utf-8")
    (factory_dir / "teams" / "default.md").write_text("factory v2\n", encoding="utf-8")

    manager._ensure_default_templates()

    assert global_team.read_text(encoding="utf-8") == "user edited\n"


@pytest.mark.asyncio
async def test_mcp_gateway_initialization_does_not_block_event_loop(monkeypatch):
    events: list[str] = []

    class FakeMCP:
        async def run_http_async(self, **_kwargs):
            await asyncio.Event().wait()

    gateway = UnifiedMCPGateway()

    def slow_ensure_unified_mcp():
        import time

        time.sleep(0.05)
        events.append("mcp_initialized")
        gateway._unified_mcp = FakeMCP()
        return gateway._unified_mcp

    async def wait_until_ready():
        events.append("gateway_ready")

    async def other_task():
        events.append("other_task_started")

    monkeypatch.setattr(gateway, "_ensure_unified_mcp", slow_ensure_unified_mcp)
    monkeypatch.setattr(gateway, "_wait_until_ready", wait_until_ready)

    task = asyncio.create_task(other_task())
    await gateway.start_gateway()
    await task
    await gateway.stop_gateway()

    assert events.index("other_task_started") < events.index("mcp_initialized")
