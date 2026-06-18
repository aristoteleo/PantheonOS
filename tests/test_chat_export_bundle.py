import json
import shutil
import tarfile
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest

import pantheon.chatroom.export as export_module
from pantheon.chatroom.export import export_chat_bundle, import_chat_bundle


@contextmanager
def _workspace_under_tmp():
    """Create a real /tmp workspace so absolute-path tests isolate portability."""
    workspace = Path(tempfile.mkdtemp(prefix="pantheon-export-test-", dir="/tmp"))
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _write_chat(memory_dir: Path, chat_id: str, messages: list[dict], meta: dict) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / f"{chat_id}.jsonl").write_text(
        "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages),
        encoding="utf-8",
    )
    (memory_dir / f"{chat_id}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False),
        encoding="utf-8",
    )


def test_export_includes_workspace_relative_file_references(tmp_path):
    workspace = tmp_path / "workspace"
    memory_dir = workspace / ".pantheon" / "memory"
    output_dir = workspace / ".pantheon" / "exports" / "chat-1"
    artifact = workspace / "results" / "out.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("gene,count\nA,1\n", encoding="utf-8")

    _write_chat(
        memory_dir,
        "chat-1",
        [
            {
                "role": "assistant",
                "content": "I saved the result at results/out.csv",
            }
        ],
        {
            "id": "chat-1",
            "name": "Relative artifact chat",
            "extra_data": {
                "project": {
                    "workspace_path": str(workspace),
                }
            },
        },
    )

    result = export_chat_bundle(memory_dir, "chat-1", output_dir, compress=False)

    assert result["success"] is True
    exported_files = json.loads((output_dir / "manifest.json").read_text())["files"]
    assert any(file["original"] == str(artifact) for file in exported_files)
    assert (output_dir / "chat.jsonl").read_text(encoding="utf-8").count("./files/") == 1


def test_export_stores_workspace_absolute_paths_as_workspace_relative_bundle_files(tmp_path):
    with _workspace_under_tmp() as workspace:
        memory_dir = workspace / ".pantheon" / "memory"
        output_dir = tmp_path / "chat-absolute-workspace-path"
        artifact = workspace / "results" / "out.csv"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("gene,count\nA,1\n", encoding="utf-8")

        _write_chat(
            memory_dir,
            "chat-absolute-workspace-path",
            [
                {
                    "role": "assistant",
                    "content": f"I saved the result at {artifact}",
                }
            ],
            {
                "id": "chat-absolute-workspace-path",
                "name": "Absolute workspace artifact chat",
                "extra_data": {
                    "project": {
                        "workspace_path": str(workspace),
                    }
                },
            },
        )

        result = export_chat_bundle(
            memory_dir, "chat-absolute-workspace-path", output_dir, compress=False
        )

    assert result["success"] is True
    exported_files = json.loads((output_dir / "manifest.json").read_text())["files"]
    assert exported_files == [
        {
            "original": str(artifact),
            "relative": "results/out.csv",
            "local": "files/results/out.csv",
            "size": len("gene,count\nA,1\n".encode("utf-8")),
            "source_root_kind": "workspace",
        }
    ]
    assert (output_dir / "files" / "results" / "out.csv").read_text(
        encoding="utf-8"
    ) == "gene,count\nA,1\n"
    rewritten = (output_dir / "chat.jsonl").read_text(encoding="utf-8")
    assert "./files/results/out.csv" in rewritten
    assert str(artifact) not in rewritten


def test_export_manifest_records_canonical_relative_path_and_source_root_kind(tmp_path):
    with _workspace_under_tmp() as workspace:
        memory_dir = workspace / ".pantheon" / "memory"
        output_dir = tmp_path / "chat-canonical-manifest"
        artifact = workspace / "results" / "out.csv"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("gene,count\nA,1\n", encoding="utf-8")

        _write_chat(
            memory_dir,
            "chat-canonical-manifest",
            [
                {
                    "role": "assistant",
                    "content": f"Absolute artifact path: {artifact}",
                }
            ],
            {
                "id": "chat-canonical-manifest",
                "name": "Canonical manifest chat",
                "extra_data": {
                    "project": {
                        "workspace_path": str(workspace),
                    }
                },
            },
        )

        result = export_chat_bundle(
            memory_dir, "chat-canonical-manifest", output_dir, compress=False
        )

    assert result["success"] is True
    exported_files = json.loads((output_dir / "manifest.json").read_text())["files"]
    assert exported_files == [
        {
            "original": str(artifact),
            "relative": "results/out.csv",
            "local": "files/results/out.csv",
            "size": len("gene,count\nA,1\n".encode("utf-8")),
            "source_root_kind": "workspace",
        }
    ]


def test_export_canonicalizes_workdir_references_to_workspace_relative_files(tmp_path):
    workspace = tmp_path / "workspace"
    memory_dir = workspace / ".pantheon" / "memory"
    output_dir = workspace / ".pantheon" / "exports" / "chat-workdir"
    artifact = workspace / "results" / "out.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("gene,count\nA,1\n", encoding="utf-8")

    _write_chat(
        memory_dir,
        "chat-workdir",
        [
            {
                "role": "tool",
                "name": "executor",
                "content": {
                    "result_file": "workdir/results/out.csv",
                    "log": "Saved workdir/results/out.csv",
                },
            }
        ],
        {
            "id": "chat-workdir",
            "name": "Workdir artifact chat",
            "extra_data": {
                "project": {
                    "workspace_path": str(workspace),
                }
            },
        },
    )

    result = export_chat_bundle(memory_dir, "chat-workdir", output_dir, compress=False)

    assert result["success"] is True
    exported_files = json.loads((output_dir / "manifest.json").read_text())["files"]
    assert exported_files == [
        {
            "original": str(artifact),
            "relative": "results/out.csv",
            "local": "files/results/out.csv",
            "size": len("gene,count\nA,1\n".encode("utf-8")),
            "source_root_kind": "workspace",
        }
    ]
    assert (output_dir / "files" / "results" / "out.csv").is_file()
    rewritten = (output_dir / "chat.jsonl").read_text(encoding="utf-8")
    assert "./files/results/out.csv" in rewritten
    assert "./files/workdir/results/out.csv" not in rewritten
    assert "workdir/results/out.csv" not in rewritten


def test_export_deduplicates_absolute_relative_and_file_uri_references(tmp_path):
    workspace = tmp_path / "workspace"
    memory_dir = workspace / ".pantheon" / "memory"
    output_dir = workspace / ".pantheon" / "exports" / "chat-dedup"
    artifact = workspace / "results" / "out.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("gene,count\nA,1\n", encoding="utf-8")

    _write_chat(
        memory_dir,
        "chat-dedup",
        [
            {
                "role": "assistant",
                "content": (
                    f"Absolute {artifact}; relative results/out.csv; uri {artifact.as_uri()}"
                ),
            }
        ],
        {
            "id": "chat-dedup",
            "name": "Deduplicated artifact chat",
            "extra_data": {
                "project": {
                    "workspace_path": str(workspace),
                }
            },
        },
    )

    result = export_chat_bundle(memory_dir, "chat-dedup", output_dir, compress=False)

    assert result["success"] is True
    exported_files = json.loads((output_dir / "manifest.json").read_text())["files"]
    assert len(exported_files) == 1
    assert exported_files[0]["relative"] == "results/out.csv"
    assert exported_files[0]["local"] == "files/results/out.csv"
    assert (output_dir / "chat.jsonl").read_text(encoding="utf-8").count(
        "./files/results/out.csv"
    ) == 3


def test_export_includes_file_uri_image_references(tmp_path):
    workspace = tmp_path / "workspace"
    memory_dir = workspace / ".pantheon" / "memory"
    output_dir = workspace / ".pantheon" / "exports" / "chat-2"
    image = workspace / ".pantheon" / "images" / "chat-2" / "plot.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake-png")

    _write_chat(
        memory_dir,
        "chat-2",
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please inspect this image."},
                    {"type": "image_url", "image_url": {"url": f"file://{image}"}},
                ],
            }
        ],
        {
            "id": "chat-2",
            "name": "Image chat",
            "extra_data": {
                "project": {
                    "workspace_path": str(workspace),
                }
            },
        },
    )

    result = export_chat_bundle(memory_dir, "chat-2", output_dir, compress=False)

    assert result["success"] is True
    exported_files = json.loads((output_dir / "manifest.json").read_text())["files"]
    assert any(file["original"] == str(image) for file in exported_files)
    assert "file://" not in (output_dir / "chat.jsonl").read_text(encoding="utf-8")


def test_export_includes_structured_result_file_fields(tmp_path):
    workspace = tmp_path / "workspace"
    memory_dir = workspace / ".pantheon" / "memory"
    output_dir = workspace / ".pantheon" / "exports" / "chat-structured-file"
    artifact = workspace / "reports" / "summary.pdf"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"%PDF-1.4\nfake")

    _write_chat(
        memory_dir,
        "chat-structured-file",
        [
            {
                "role": "tool",
                "name": "reporter",
                "content": {
                    "result_file": "reports/summary.pdf",
                    "caption": "Generated report",
                },
            }
        ],
        {
            "id": "chat-structured-file",
            "name": "Structured artifact chat",
            "extra_data": {
                "project": {
                    "workspace_path": str(workspace),
                }
            },
        },
    )

    result = export_chat_bundle(memory_dir, "chat-structured-file", output_dir, compress=False)

    assert result["success"] is True
    exported_files = json.loads((output_dir / "manifest.json").read_text())["files"]
    assert exported_files == [
        {
            "original": str(artifact),
            "relative": "reports/summary.pdf",
            "local": "files/reports/summary.pdf",
            "size": len(b"%PDF-1.4\nfake"),
            "source_root_kind": "workspace",
        }
    ]
    assert (output_dir / "files" / "reports" / "summary.pdf").read_bytes() == b"%PDF-1.4\nfake"
    assert "./files/reports/summary.pdf" in (output_dir / "chat.jsonl").read_text(
        encoding="utf-8"
    )


def test_export_includes_markdown_file_uri_links_with_fragments(tmp_path):
    workspace = tmp_path / "workspace"
    memory_dir = workspace / ".pantheon" / "memory"
    output_dir = workspace / ".pantheon" / "exports" / "chat-markdown-file-uri"
    artifact = workspace / "src" / "main.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("print('hello')\n", encoding="utf-8")

    _write_chat(
        memory_dir,
        "chat-markdown-file-uri",
        [
            {
                "role": "assistant",
                "content": f"Open [main.py]({artifact.as_uri()}#L1-L1)",
            }
        ],
        {
            "id": "chat-markdown-file-uri",
            "name": "Markdown file URI chat",
            "extra_data": {
                "project": {
                    "workspace_path": str(workspace),
                }
            },
        },
    )

    result = export_chat_bundle(
        memory_dir, "chat-markdown-file-uri", output_dir, compress=False
    )

    assert result["success"] is True
    exported_files = json.loads((output_dir / "manifest.json").read_text())["files"]
    assert any(file["original"] == str(artifact) for file in exported_files)
    rewritten = (output_dir / "chat.jsonl").read_text(encoding="utf-8")
    assert "./files/src/main.py#L1-L1" in rewritten
    assert "file://" not in rewritten


def test_export_includes_file_uri_paths_with_url_encoded_spaces(tmp_path):
    workspace = tmp_path / "workspace with spaces"
    memory_dir = workspace / ".pantheon" / "memory"
    output_dir = workspace / ".pantheon" / "exports" / "chat-uri-space"
    artifact = workspace / "results" / "final report.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("gene,count\nA,1\n", encoding="utf-8")

    _write_chat(
        memory_dir,
        "chat-uri-space",
        [
            {
                "role": "assistant",
                "content": f"Open [final report]({artifact.as_uri()})",
            }
        ],
        {
            "id": "chat-uri-space",
            "name": "Encoded file URI chat",
            "extra_data": {
                "project": {
                    "workspace_path": str(workspace),
                }
            },
        },
    )

    result = export_chat_bundle(memory_dir, "chat-uri-space", output_dir, compress=False)

    assert result["success"] is True
    exported_files = json.loads((output_dir / "manifest.json").read_text())["files"]
    assert exported_files == [
        {
            "original": str(artifact),
            "relative": "results/final report.csv",
            "local": "files/results/final report.csv",
            "size": len("gene,count\nA,1\n".encode("utf-8")),
            "source_root_kind": "workspace",
        }
    ]
    assert "./files/results/final report.csv" in (output_dir / "chat.jsonl").read_text(
        encoding="utf-8"
    )


def test_compressed_export_archive_contains_rewritten_chat_and_files(tmp_path):
    workspace = tmp_path / "workspace"
    memory_dir = workspace / ".pantheon" / "memory"
    output_dir = workspace / ".pantheon" / "exports" / "chat-zipped"
    artifact = workspace / "results" / "out.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("gene,count\nA,1\n", encoding="utf-8")

    _write_chat(
        memory_dir,
        "chat-zipped",
        [
            {
                "role": "assistant",
                "content": "I saved the result at results/out.csv",
            }
        ],
        {
            "id": "chat-zipped",
            "name": "Compressed artifact chat",
            "extra_data": {
                "project": {
                    "workspace_path": str(workspace),
                }
            },
        },
    )

    result = export_chat_bundle(memory_dir, "chat-zipped", output_dir, compress=True)

    assert result["success"] is True
    assert result["bundle_path"] == str(output_dir) + ".zip"
    with zipfile.ZipFile(result["bundle_path"]) as archive:
        names = set(archive.namelist())
        assert "chat-zipped/files/results/out.csv" in names
        chat_jsonl = archive.read("chat-zipped/chat.jsonl").decode("utf-8")
        manifest = json.loads(archive.read("chat-zipped/manifest.json").decode("utf-8"))

    assert "./files/results/out.csv" in chat_jsonl
    assert manifest["stats"]["files_copied"] == 1


def test_compressed_export_can_import_files_under_new_target_root(tmp_path):
    source_workspace = tmp_path / "source"
    memory_dir = source_workspace / ".pantheon" / "memory"
    output_dir = source_workspace / ".pantheon" / "exports" / "chat-roundtrip"
    artifact = source_workspace / "results" / "out.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("gene,count\nA,1\n", encoding="utf-8")

    _write_chat(
        memory_dir,
        "chat-roundtrip",
        [
            {
                "role": "assistant",
                "content": "I saved the result at results/out.csv",
            }
        ],
        {
            "id": "chat-roundtrip",
            "name": "Compressed roundtrip chat",
            "extra_data": {
                "project": {
                    "workspace_path": str(source_workspace),
                }
            },
        },
    )

    export_result = export_chat_bundle(
        memory_dir, "chat-roundtrip", output_dir, compress=True
    )
    target_root = tmp_path / "target"
    target_memory = target_root / ".pantheon" / "memory"

    import_result = import_chat_bundle(
        target_memory, export_result["bundle_path"], target_root
    )

    restored = target_root / "results" / "out.csv"
    imported_jsonl = (target_memory / "chat-roundtrip.jsonl").read_text(encoding="utf-8")
    imported_meta = json.loads(
        (target_memory / "chat-roundtrip.meta.json").read_text(encoding="utf-8")
    )
    assert import_result["success"] is True
    assert restored.read_text(encoding="utf-8") == "gene,count\nA,1\n"
    assert str(restored) in imported_jsonl
    assert imported_meta["extra_data"]["project"]["workspace_path"] == str(target_root)


def test_scan_file_paths_recognizes_volumes_absolute_paths(monkeypatch):
    artifact = "/Volumes/workspace/results/out.csv"

    monkeypatch.setattr(export_module.os.path, "isfile", lambda path: path == artifact)
    monkeypatch.setattr(export_module, "_is_exportable", lambda path: path == artifact)

    assert export_module._scan_file_paths(f"Saved at {artifact}") == {artifact}


def test_import_restores_files_under_target_root(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle_file = bundle / "files" / "workspace" / "results" / "out.csv"
    bundle_file.parent.mkdir(parents=True)
    bundle_file.write_text("gene,count\nA,1\n", encoding="utf-8")

    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "chat_id": "chat-3",
                "chat_name": "Portable chat",
                "files": [
                    {
                        "original": "/old-machine/project/results/out.csv",
                        "local": "files/workspace/results/out.csv",
                        "size": bundle_file.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (bundle / "chat.meta.json").write_text(
        json.dumps({"id": "chat-3", "name": "Portable chat"}),
        encoding="utf-8",
    )
    (bundle / "chat.jsonl").write_text(
        json.dumps(
            {
                "role": "assistant",
                "content": "See ./files/workspace/results/out.csv",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    memory_dir = tmp_path / "target" / ".pantheon" / "memory"
    target_root = tmp_path / "target"
    original_copy2 = export_module.shutil.copy2

    def guarded_copy2(src, dest, *args, **kwargs):
        dest_path = Path(dest)
        try:
            dest_path.resolve().relative_to(target_root.resolve())
        except ValueError:
            pytest.fail(f"import should not restore files outside target_root: {dest_path}")
        return original_copy2(src, dest, *args, **kwargs)

    monkeypatch.setattr(export_module.shutil, "copy2", guarded_copy2)

    try:
        result = import_chat_bundle(memory_dir, bundle, target_root)
    except OSError as exc:
        pytest.fail(
            "import_chat_bundle should restore files under target_root instead of "
            f"writing bundle paths back to absolute filesystem locations: {exc}"
        )

    restored = target_root / "results" / "out.csv"
    assert result["success"] is True
    assert restored.read_text(encoding="utf-8") == "gene,count\nA,1\n"
    imported_jsonl = (memory_dir / "chat-3.jsonl").read_text(encoding="utf-8")
    assert str(restored) in imported_jsonl


def test_import_uses_original_workspace_metadata_for_legacy_absolute_bundle_layout(tmp_path):
    bundle = tmp_path / "bundle"
    bundle_file = (
        bundle
        / "files"
        / "old-machine"
        / "project"
        / "results"
        / "out.csv"
    )
    bundle_file.parent.mkdir(parents=True)
    bundle_file.write_text("gene,count\nA,1\n", encoding="utf-8")

    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "chat_id": "chat-legacy",
                "chat_name": "Legacy Portable Chat",
                "files": [
                    {
                        "original": "/old-machine/project/results/out.csv",
                        "local": "files/old-machine/project/results/out.csv",
                        "size": bundle_file.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (bundle / "chat.meta.json").write_text(
        json.dumps(
            {
                "id": "chat-legacy",
                "name": "Legacy Portable Chat",
                "extra_data": {
                    "project": {
                        "workspace_path": "/old-machine/project",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (bundle / "chat.jsonl").write_text(
        json.dumps(
            {
                "role": "assistant",
                "content": "See ./files/old-machine/project/results/out.csv",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    memory_dir = tmp_path / "target" / ".pantheon" / "memory"
    target_root = tmp_path / "target"

    result = import_chat_bundle(memory_dir, bundle, target_root)

    restored = target_root / "results" / "out.csv"
    assert result["success"] is True
    assert restored.read_text(encoding="utf-8") == "gene,count\nA,1\n"
    imported_jsonl = (memory_dir / "chat-legacy.jsonl").read_text(encoding="utf-8")
    imported_meta = json.loads(
        (memory_dir / "chat-legacy.meta.json").read_text(encoding="utf-8")
    )
    assert str(restored) in imported_jsonl
    assert imported_meta["extra_data"]["project"]["workspace_path"] == str(target_root)


def test_import_prefers_manifest_relative_path_over_legacy_local_layout(tmp_path):
    bundle = tmp_path / "bundle"
    bundle_file = (
        bundle
        / "files"
        / "old-machine"
        / "project"
        / "results"
        / "out.csv"
    )
    bundle_file.parent.mkdir(parents=True)
    bundle_file.write_text("gene,count\nA,1\n", encoding="utf-8")

    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "chat_id": "chat-new-manifest",
                "chat_name": "Canonical Import Chat",
                "files": [
                    {
                        "original": "/old-machine/project/results/out.csv",
                        "relative": "results/out.csv",
                        "local": "files/old-machine/project/results/out.csv",
                        "size": bundle_file.stat().st_size,
                        "source_root_kind": "workspace",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (bundle / "chat.meta.json").write_text(
        json.dumps({"id": "chat-new-manifest", "name": "Canonical Import Chat"}),
        encoding="utf-8",
    )
    (bundle / "chat.jsonl").write_text(
        json.dumps(
            {
                "role": "assistant",
                "content": "See ./files/old-machine/project/results/out.csv",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    memory_dir = tmp_path / "target" / ".pantheon" / "memory"
    target_root = tmp_path / "target"

    result = import_chat_bundle(memory_dir, bundle, target_root)

    restored = target_root / "results" / "out.csv"
    legacy_restored = target_root / "old-machine" / "project" / "results" / "out.csv"
    imported_jsonl = (memory_dir / "chat-new-manifest.jsonl").read_text(encoding="utf-8")
    assert result["success"] is True
    assert restored.read_text(encoding="utf-8") == "gene,count\nA,1\n"
    assert not legacy_restored.exists()
    assert str(restored) in imported_jsonl


def test_import_rejects_zip_entries_outside_bundle_root(tmp_path):
    archive_path = tmp_path / "bad-bundle.zip"
    outside_target = tmp_path / "outside.txt"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("bundle/chat.jsonl", "")
        archive.writestr("bundle/chat.meta.json", "{}")
        archive.writestr("bundle/manifest.json", "{}")
        archive.writestr("../outside.txt", "escaped")

    result = import_chat_bundle(
        tmp_path / "target" / ".pantheon" / "memory",
        archive_path,
        tmp_path / "target",
    )

    assert result["success"] is False
    assert "unsafe" in result["message"].lower()
    assert not outside_target.exists()


def test_import_rejects_tar_entries_outside_bundle_root(tmp_path):
    archive_path = tmp_path / "bad-bundle.tar.gz"
    outside_target = tmp_path / "outside.txt"
    safe_file = tmp_path / "chat.jsonl"
    unsafe_file = tmp_path / "payload.txt"
    safe_file.write_text("", encoding="utf-8")
    unsafe_file.write_text("escaped", encoding="utf-8")

    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(safe_file, arcname="bundle/chat.jsonl")
        archive.add(unsafe_file, arcname="../outside.txt")

    result = import_chat_bundle(
        tmp_path / "target" / ".pantheon" / "memory",
        archive_path,
        tmp_path / "target",
    )

    assert result["success"] is False
    assert "unsafe" in result["message"].lower()
    assert not outside_target.exists()


def test_import_restores_image_urls_as_target_file_uris(tmp_path):
    bundle = tmp_path / "bundle"
    bundle_file = bundle / "files" / ".pantheon" / "images" / "chat-4" / "plot.png"
    bundle_file.parent.mkdir(parents=True)
    bundle_file.write_bytes(b"\x89PNG\r\n\x1a\nfake-png")

    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "chat_id": "chat-4",
                "chat_name": "Image import chat",
                "files": [
                    {
                        "original": "/old-machine/project/.pantheon/images/chat-4/plot.png",
                        "local": "files/.pantheon/images/chat-4/plot.png",
                        "size": bundle_file.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (bundle / "chat.meta.json").write_text(
        json.dumps({"id": "chat-4", "name": "Image import chat"}),
        encoding="utf-8",
    )
    (bundle / "chat.jsonl").write_text(
        json.dumps(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please inspect this image."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "./files/.pantheon/images/chat-4/plot.png"},
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    memory_dir = tmp_path / "target" / ".pantheon" / "memory"
    target_root = tmp_path / "target"

    result = import_chat_bundle(memory_dir, bundle, target_root)

    restored = target_root / ".pantheon" / "images" / "chat-4" / "plot.png"
    imported_message = json.loads((memory_dir / "chat-4.jsonl").read_text(encoding="utf-8"))
    imported_url = imported_message["content"][1]["image_url"]["url"]
    assert result["success"] is True
    assert restored.read_bytes() == b"\x89PNG\r\n\x1a\nfake-png"
    assert imported_url == restored.as_uri()
