import os
import pytest
from tempfile import TemporaryDirectory

from pantheon.toolsets.file import FileManagerToolSet


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink not supported")
async def test_list_files_handles_broken_symlink():
    with TemporaryDirectory() as temp_dir:
        toolset = FileManagerToolSet("file_manager", temp_dir)
        os.symlink("missing-target", os.path.join(temp_dir, "broken-link"))
        with open(os.path.join(temp_dir, "ok.txt"), "w", encoding="utf-8") as f:
            f.write("ok")

        result = await toolset.list_files()

        assert result["success"] is True
        names = {entry["name"]: entry for entry in result["files"]}
        assert "ok.txt" in names
        assert "broken-link" in names
        assert names["broken-link"]["type"] == "symlink"
