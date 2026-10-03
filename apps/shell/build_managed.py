"""Build a native Shell App directory; target nodes need no Go or Python.

This opt-in package preserves the legacy builtin catalog until owner dependency
assembly is ready to select the managed deployment. Run with a new output path.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def build(output: Path, target_os: str, arch: str, go: str = "go") -> None:
    if target_os not in {"darwin", "linux"} or arch not in {"arm64", "amd64"}:
        raise ValueError("Managed Shell currently supports macOS/Linux arm64/amd64")
    source = Path(__file__).resolve().parent
    output = output.absolute()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".shell-build-", dir=output.parent) as temp:
        package = Path(temp) / "package"
        package.mkdir()
        env = dict(os.environ, GOOS=target_os, GOARCH=arch, CGO_ENABLED="0")
        subprocess.run([go, "build", "-trimpath", "-buildvcs=false", "-ldflags=-s -w",
                        "-o", str(package / "shell"), "./shell/cmd/shell"],
                       cwd=source.parent, env=env, check=True)
        manifest = json.loads((source / "app.json").read_text())
        manifest["runtime"] = "process"
        manifest["execution"] = {"protocol": 1, "manifest": "fleet.json"}
        manifest["notes"] = "Native managed Shell App; generic Fleet lifecycle and resource-session@1."
        command = lambda action: ["${PACKAGE}/shell", action, "--data", "${DATA}"]
        definition = {
            "protocol": 1, "app_id": manifest["id"], "version": manifest["version"],
            "requires": {"os": [target_os], "arch": [arch], "caps": ["proc"]},
            "components": [{"name": "backend", "runtime": "process", "argv": command("start"),
                            "ports": {"http": 0}, "stop_seconds": 30,
                            "readiness": {"argv": command("ready"), "timeout_seconds": 15}}],
            "hooks": {"before_stop": {"component": "backend", "argv": command("drain"),
                                      "timeout_seconds": 10}},
        }
        for name, content in [("app.json", manifest), ("fleet.json", definition)]:
            (package / name).write_text(json.dumps(content, indent=2) + "\n")
        shutil.copy2(source / "README.md", package / "README.md")
        package.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--os", required=True, choices=["darwin", "linux"])
    parser.add_argument("--arch", required=True, choices=["arm64", "amd64"])
    parser.add_argument("--go", default="go")
    args = parser.parse_args()
    build(args.output, args.os, args.arch, args.go)
