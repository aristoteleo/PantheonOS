from PIL import Image
import pytest
from pantheon.apps.builtin.file import FileManagerToolSet


@pytest.mark.asyncio
async def test_image_preview_uses_active_root_and_keeps_containment(tmp_path, monkeypatch):
    original = tmp_path / "original"
    active = tmp_path / "active"
    original.mkdir()
    active.mkdir()
    Image.new("RGB", (4, 4), "red").save(active / "figure.png")
    Image.new("RGB", (4, 4), "blue").save(original / "outside.png")
    (active / "linked.png").symlink_to(active / "figure.png")
    (active / "escape.png").symlink_to(original / "outside.png")
    toolset = FileManagerToolSet("file_manager", str(original))
    monkeypatch.setattr(toolset, "_get_effective_workdir", lambda: str(active))
    for path in ["figure.png", "linked.png", str(active / "figure.png")]:
        result = await toolset.fetch_image_base64(path, 400)
        assert result["success"] is True, result
        assert result["data_uri"].startswith("data:image/")
    outside = await toolset.fetch_image_base64("escape.png", 400)
    assert outside["success"] is False
    assert outside["error"] == "Path outside allowed workspace"
    missing = await toolset.fetch_image_base64("missing.png", 400)
    assert missing["success"] is False
    assert "Image does not exist" in missing["error"]
