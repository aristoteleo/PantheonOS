import pytest

from pantheon.toolsets.live_view.toolset import LiveViewToolSet


@pytest.mark.asyncio
async def test_resolve_viewer_falls_back_to_factory_skills(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    from pantheon.settings import get_settings, reset_settings

    reset_settings()
    settings = get_settings()
    factory_skills = tmp_path / "factory" / "skills"
    adapter = factory_skills / "live_view" / "factory_view" / "adapter.js"
    adapter.parent.mkdir(parents=True)
    adapter.write_text("export function setup() {}\n", encoding="utf-8")

    toolset = LiveViewToolSet()

    class FakeServer:
        def url_for(self, path):
            assert path == adapter.resolve()
            return "http://data.local/factory_view/adapter.js"

    async def fake_data_server():
        return FakeServer()

    monkeypatch.setattr(toolset, "_ensure_data_server", fake_data_server)
    settings.package_templates = factory_skills.parent

    try:
        url, demo, error = await toolset._resolve_viewer("factory_view")
    finally:
        reset_settings()

    assert url == "http://data.local/factory_view/adapter.js"
    assert demo is None
    assert error is None
