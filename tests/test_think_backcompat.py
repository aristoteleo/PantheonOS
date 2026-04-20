"""Backward-compat tests for legacy think_tool parsing."""

from pantheon.factory.models import AgentConfig



def test_agent_config_from_dict_absorbs_legacy_think_tool_flag():
    cfg = AgentConfig.from_dict(
        {
            "id": "leader",
            "name": "Leader",
            "model": "normal",
            "think_tool": True,
            "toolsets": ["file_manager"],
        }
    )

    assert "think" in cfg.toolsets
    assert "file_manager" in cfg.toolsets


def test_agent_config_from_dict_does_not_duplicate_think():
    cfg = AgentConfig.from_dict(
        {
            "id": "leader",
            "name": "Leader",
            "model": "normal",
            "think_tool": True,
            "toolsets": ["think", "file_manager"],
        }
    )

    assert cfg.toolsets.count("think") == 1
