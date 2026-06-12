import pytest

from pantheon.endpoint.toolsets import ToolSetManager


def test_knowledge_toolset_is_not_exported_by_default():
    import pantheon.toolsets as toolsets

    assert "KnowledgeToolSet" not in toolsets.__all__
    assert "KnowledgeToolSet" not in dir(toolsets)
    with pytest.raises(AttributeError):
        getattr(toolsets, "KnowledgeToolSet")


def test_knowledge_toolset_can_still_be_loaded_explicitly(tmp_path):
    manager = ToolSetManager(
        config={},
        id_hash="test",
        endpoint_path=tmp_path,
        log_dir=tmp_path,
    )

    cls = manager._get_toolset_class("knowledge")

    assert cls.__name__ == "KnowledgeToolSet"


def test_knowledge_toolset_remains_visible_to_cli_discovery(monkeypatch):
    module = _load_toolsets_cli_module(monkeypatch)

    assert "knowledge" in module.get_toolset_modules()


def test_cli_can_still_resolve_knowledge_when_requested(monkeypatch):
    module = _load_toolsets_cli_module(monkeypatch)

    cls = module.import_toolset_class("knowledge")

    assert cls.__name__ == "KnowledgeToolSet"


def _load_toolsets_cli_module(monkeypatch):
    import fire
    import importlib.util
    from pathlib import Path

    monkeypatch.setattr(fire, "Fire", lambda *args, **kwargs: None)
    module_path = Path(__file__).parents[1] / "pantheon" / "toolsets" / "__main__.py"
    spec = importlib.util.spec_from_file_location("toolsets_cli_for_test", module_path)
    module = importlib.util.module_from_spec(spec)

    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module
