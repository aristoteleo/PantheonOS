"""apphost — the App-as-a-process shim (§04c `process`, P3 groundwork).

Layered on purpose: resolution and construction are unit-tested in-process;
the full CLI boots real apps in a subprocess with --no-remote (construct +
run_setup, no bus). Bus registration itself is ToolSet.run()'s job and is
covered by the existing remote toolset tests.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pantheon.apphost import _construct_kwargs, _resolve_backend

REPO = Path(__file__).resolve().parent.parent


def test_resolve_backend_finds_catalog_apps():
    cls, requires, entry = _resolve_backend("shell")
    assert cls.__name__ == "ShellToolSet"
    assert "proc" in requires and entry.app_id == "shell"


def test_resolve_backend_refuses_unknown_id():
    with pytest.raises(SystemExit, match="unknown app id"):
        _resolve_backend("no-such-app")


def test_construct_kwargs_follow_placement_contract(tmp_path):
    wd = str(tmp_path)
    assert _construct_kwargs("shell", ["proc", "fs:workspace"], wd) == {"workdir": wd}
    assert _construct_kwargs("file-manager", ["fs:workspace"], wd) == {"path": wd}
    assert _construct_kwargs("web", ["net"], wd) == {}


@pytest.mark.parametrize("app_id", ["shell", "file-manager", "web"])
def test_cli_boots_app_without_bus(app_id, tmp_path):
    """The whole CLI path: argparse -> registry -> constructor -> run_setup."""
    env = dict(os.environ, PYTHONPATH=str(REPO))
    proc = subprocess.run(
        [sys.executable, "-m", "pantheon.apphost", "--app-id", app_id,
         "--workdir", str(tmp_path), "--no-remote"],
        env=env, cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
