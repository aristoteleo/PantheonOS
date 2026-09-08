import pytest

from pantheon.apps.builtin.file import FileManagerToolSet


@pytest.mark.parametrize("recursive", [False, True])
async def test_listing_a_file_is_not_an_empty_directory(tmp_path, recursive):
    source = tmp_path / "document.txt"
    source.write_text("keep these bytes")
    files = FileManagerToolSet("file_manager", str(tmp_path))

    result = await files.list_files(str(source), recursive=recursive)

    assert result == {"success": False, "error": "Path is not a directory"}
    assert source.read_text() == "keep these bytes"
