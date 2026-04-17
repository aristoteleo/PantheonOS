"""
Unit tests for pantheon.utils.image_detection.
"""

import base64
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pantheon.utils.image_detection import (
    IMAGE_OUTPUT_DIR,
    _IMAGE_EXTENSIONS,
    _get_image_limits,
    diff_snapshots,
    encode_images_to_uris,
    snapshot_images,
)


# ============ snapshot_images ============


class TestSnapshotImages:
    def test_empty_directory(self, tmp_path):
        assert snapshot_images(tmp_path) == {}

    def test_detects_images(self, tmp_path):
        (tmp_path / "plot.png").write_bytes(b"\x89PNG")
        (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8")
        snap = snapshot_images(tmp_path)
        assert len(snap) == 2
        assert str(tmp_path / "plot.png") in snap
        assert str(tmp_path / "photo.jpg") in snap

    def test_ignores_non_image_files(self, tmp_path):
        (tmp_path / "data.csv").write_text("a,b,c")
        (tmp_path / "script.py").write_text("print(1)")
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        snap = snapshot_images(tmp_path)
        assert len(snap) == 1

    def test_only_scans_top_level(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.png").write_bytes(b"\x89PNG")
        (tmp_path / "top.png").write_bytes(b"\x89PNG")
        snap = snapshot_images(tmp_path)
        assert len(snap) == 1
        assert str(tmp_path / "top.png") in snap

    def test_nonexistent_directory(self, tmp_path):
        snap = snapshot_images(tmp_path / "does_not_exist")
        assert snap == {}

    def test_case_insensitive_extensions(self, tmp_path):
        (tmp_path / "upper.PNG").write_bytes(b"\x89PNG")
        (tmp_path / "mixed.JpEg").write_bytes(b"\xff\xd8")
        snap = snapshot_images(tmp_path)
        assert len(snap) == 2


# ============ diff_snapshots ============


class TestDiffSnapshots:
    def test_no_changes(self):
        snap = {"a.png": 100.0, "b.jpg": 200.0}
        assert diff_snapshots(snap, snap) == []

    def test_new_file(self):
        pre = {"a.png": 100.0}
        post = {"a.png": 100.0, "b.png": 200.0}
        diff = diff_snapshots(pre, post)
        assert diff == ["b.png"]

    def test_modified_file(self):
        pre = {"a.png": 100.0}
        post = {"a.png": 200.0}
        diff = diff_snapshots(pre, post)
        assert diff == ["a.png"]

    def test_deleted_file_not_in_diff(self):
        pre = {"a.png": 100.0, "b.png": 200.0}
        post = {"a.png": 100.0}
        diff = diff_snapshots(pre, post)
        assert diff == []

    def test_empty_pre(self):
        post = {"a.png": 100.0, "b.png": 200.0}
        diff = diff_snapshots({}, post)
        assert set(diff) == {"a.png", "b.png"}

    def test_both_empty(self):
        assert diff_snapshots({}, {}) == []


# ============ encode_images_to_uris ============


class TestEncodeImagesToUris:
    def test_encodes_png(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG_content")
        uris = encode_images_to_uris([str(img)])
        assert len(uris) == 1
        assert uris[0].startswith("data:image/png;base64,")
        decoded = base64.b64decode(uris[0].split(",", 1)[1])
        assert decoded == b"\x89PNG_content"

    def test_encodes_jpg(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8content")
        uris = encode_images_to_uris([str(img)])
        assert len(uris) == 1
        assert uris[0].startswith("data:image/jpeg;base64,")

    def test_encodes_jpeg(self, tmp_path):
        img = tmp_path / "photo.jpeg"
        img.write_bytes(b"\xff\xd8content")
        uris = encode_images_to_uris([str(img)])
        assert uris[0].startswith("data:image/jpeg;base64,")

    def test_skips_missing_file(self, tmp_path):
        uris = encode_images_to_uris([str(tmp_path / "nonexistent.png")])
        assert uris == []

    def test_multiple_files(self, tmp_path):
        (tmp_path / "a.png").write_bytes(b"aa")
        (tmp_path / "b.jpg").write_bytes(b"bb")
        uris = encode_images_to_uris([str(tmp_path / "a.png"), str(tmp_path / "b.jpg")])
        assert len(uris) == 2

    def test_skips_oversized_file(self, tmp_path):
        img = tmp_path / "huge.png"
        img.write_bytes(b"x" * 100)
        with patch(
            "pantheon.utils.image_detection._get_image_limits",
            return_value=(50, 1568),
        ):
            uris = encode_images_to_uris([str(img)])
        assert uris == []

    def test_allows_file_within_limit(self, tmp_path):
        img = tmp_path / "small.png"
        img.write_bytes(b"x" * 50)
        with patch(
            "pantheon.utils.image_detection._get_image_limits",
            return_value=(100, 1568),
        ):
            uris = encode_images_to_uris([str(img)])
        assert len(uris) == 1


# ============ _get_image_limits ============


class TestGetImageLimits:
    def test_returns_defaults_without_config(self):
        max_size, max_dim = _get_image_limits()
        assert max_size == 10 * 1024 * 1024
        assert max_dim == 1568

    def test_reads_from_config(self):
        with patch(
            "pantheon.claw.config.ClawConfigStore.load",
            return_value={"images": {"max_size_bytes": 5000, "max_dimension": 800}},
        ):
            max_size, max_dim = _get_image_limits()
        assert max_size == 5000
        assert max_dim == 800


# ============ Integration: snapshot + diff + encode ============


class TestSnapshotDiffEncodeRoundTrip:
    def test_full_round_trip(self, tmp_path):
        # Pre-snapshot (empty)
        pre = snapshot_images(tmp_path)
        assert pre == {}

        # Create an image
        img = tmp_path / "result.png"
        img.write_bytes(b"\x89PNG_test_data")

        # Post-snapshot
        post = snapshot_images(tmp_path)
        assert len(post) == 1

        # Diff
        new_paths = diff_snapshots(pre, post)
        assert new_paths == [str(img)]

        # Encode
        uris = encode_images_to_uris(new_paths)
        assert len(uris) == 1
        assert "base64" in uris[0]
