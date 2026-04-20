from pantheon.factory.models import AgentConfig, TeamConfig
from pantheon.factory.template_manager import TemplateManager


def _make_manager(tmp_path):
    return TemplateManager(work_dir=tmp_path)


def test_prepare_team_excludes_think_from_required_toolsets(tmp_path):
    manager = _make_manager(tmp_path)

    team = TeamConfig(
        id="t1",
        name="T1",
        description="demo",
        agents=[
            AgentConfig(
                id="leader",
                name="Leader",
                model="low",
                toolsets=["file_manager", "think"],
            )
        ],
    )

    _, required_toolsets, _ = manager.prepare_team(team)

    assert "think" not in required_toolsets
    assert "file_manager" in required_toolsets
