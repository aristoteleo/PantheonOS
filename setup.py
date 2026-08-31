"""Build hook: fold the repo-root App tree into the wheel.

The first-party Apps live at <repo>/apps — outside the pantheon package,
where the design wants them (each one a future git repo of its own). An
installed wheel has no repo root, so at build time the tree's runtime face
(manifests, Python backends, frontend bundles, assets) is copied into
pantheon/apps/builtin/, which is exactly where the import bridge and the
registry fall back to when no source tree is present. Go sources, tests
and frontend build inputs stay out — the wheel ships what runs, not what
compiles.
"""

import fnmatch
import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py

_EXCLUDE = (
    "*.go", "go.mod", "go.sum", "*_test.go",
    "*.pyc", "__pycache__", ".DS_Store",
    "frontend-src", "node_modules",
)


def _keep(name: str) -> bool:
    return not any(fnmatch.fnmatch(name, pat) for pat in _EXCLUDE)


class BuildPyWithApps(build_py):
    def run(self):
        super().run()
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apps")
        if not os.path.isdir(src):
            return
        dst = os.path.join(self.build_lib, "pantheon", "apps", "builtin")
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if _keep(d)]
            rel = os.path.relpath(root, src)
            for fname in files:
                if not _keep(fname):
                    continue
                out_dir = os.path.join(dst, rel) if rel != "." else dst
                os.makedirs(out_dir, exist_ok=True)
                shutil.copy2(os.path.join(root, fname), os.path.join(out_dir, fname))


setup(cmdclass={"build_py": BuildPyWithApps})
