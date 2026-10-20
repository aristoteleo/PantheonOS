"""Build hook: fold the repo-root App tree into the wheel.

The first-party Apps live at <repo>/apps — outside the pantheonos package,
where the design wants them (each one a future git repo of its own). An
installed wheel has no repo root, so at build time the tree's runtime face
(manifests, Python backends, frontend bundles, assets) is copied into
pantheonos/apps/builtin/, which is exactly where the import bridge and the
registry fall back to when no source tree is present. Go sources, tests
and frontend build inputs stay out — the wheel ships what runs, not what
compiles.
"""

import fnmatch
import os
import shutil

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py

# Pantheon-Agent is an App like the others: its package lives with its
# manifest in apps/agent. The platform's (pantheonos) is at the root.
AGENT = os.path.join("apps", "agent")
PACKAGES = (find_packages(".", include=["pantheonos", "pantheonos.*"])
            + find_packages(AGENT, include=["pantheon", "pantheon.*"]))

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
        import runpy
        STORE_APPS = runpy.run_path(os.path.join(os.path.dirname(__file__), 'pantheonos/apps/distribution.py'))['STORE_APPS']
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apps")
        if not os.path.isdir(src):
            return
        dst = os.path.join(self.build_lib, "pantheonos", "apps", "builtin")
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if _keep(d)]
            if root == src:
                # QuPath's trusted display driver is OS integration code; its
                # App manifest/frontend live only in the Store repository.
                dirs[:] = [d for d in dirs if d not in STORE_APPS or d == 'qupath']
            rel = os.path.relpath(root, src)
            if rel == "agent":
                # The Agent's own package is installed as `pantheon`, not
                # folded into the builtin Apps.
                dirs[:] = [d for d in dirs if d != "pantheon"]
            for fname in files:
                if not _keep(fname):
                    continue
                if rel == 'qupath' and fname not in ('__init__.py', 'native.py', 'bridge.py'):
                    continue
                if rel.startswith('qupath' + os.sep) and not rel.startswith('qupath' + os.sep + 'bridge'):
                    continue
                out_dir = os.path.join(dst, rel) if rel != "." else dst
                os.makedirs(out_dir, exist_ok=True)
                shutil.copy2(os.path.join(root, fname), os.path.join(out_dir, fname))


setup(packages=PACKAGES, package_dir={"pantheon": os.path.join(AGENT, "pantheon")},
      cmdclass={"build_py": BuildPyWithApps})
