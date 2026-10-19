"""Runtime sources an App release vendors (its backend/_vendor).

App builds name the runtime files they ship as they were named when one
package held the platform and the Agent (``utils/log.py``,
``evolution/team.py``). The platform's now live in ``pantheonos``, the
Agent's in ``pantheon``; a release vendors each under its own package, so
the code it carries imports as it does here.
"""
from __future__ import annotations

import shutil
from pathlib import Path

#: The directory that holds both packages (a checkout's root, site-packages).
ROOT = Path(__file__).resolve().parents[2]

_PLATFORM_DIRS = ('apps/', 'local/', 'store/', 'utils/', 'remote/', 'models/', 'model_contracts/',
                  'funcdesc/', 'data/', 'package_runtime/')
_PLATFORM_FILES = ('toolset.py', 'settings.py', 'constant.py', 'apphost.py')
_RENAMED = {
    'internal/package_runtime/context.py': ('pantheonos', 'package_runtime/context.py'),
    'apps/general_agent_release.py': ('pantheon', 'agent_release.py'),
    'utils/image_detection.py': ('pantheon', 'image_detection.py'),
}


def runtime_source(name: str) -> tuple[str, str]:
    """(package, path in it) of a runtime file named as before the split."""
    name = name.lstrip('/')
    if name in _RENAMED:
        return _RENAMED[name]
    if name in _PLATFORM_FILES or name.startswith(_PLATFORM_DIRS):
        return 'pantheonos', name
    return 'pantheon', name


def runtime_path(name: str, root: Path = ROOT) -> Path:
    package, path = runtime_source(name)
    return root / package / path


def vendor_file(name: str, vendor: Path, root: Path = ROOT) -> Path:
    """Copy a runtime file into vendor/<package>/<path>."""
    package, path = runtime_source(name)
    origin = root / package / path
    if origin.is_symlink() or not origin.is_file():
        raise ValueError(f'App release input must be a regular file: {origin}')
    target = vendor / package / path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origin, target)
    return target


def vendor_tree(name: str, vendor: Path, root: Path = ROOT) -> Path:
    """Copy a runtime package directory (``funcdesc``) into the vendor tree."""
    package, path = runtime_source(name.rstrip('/') + '/')
    target = vendor / package / path.rstrip('/')
    shutil.copytree(root / package / path.rstrip('/'), target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return target


def builtin_app_dir(vendor: Path) -> Path:
    """Where vendored first-party App modules go (pantheonos.apps.builtin)."""
    return vendor / 'pantheonos' / 'apps' / 'builtin'


def vendor_compat(vendor: Path, root: Path = ROOT) -> None:
    """A vendored ``pantheon`` that resolves the platform's names from before
    the split (``pantheon.toolset`` is ``pantheonos.toolset``): App code
    written against them keeps working."""
    moved = root / 'pantheon' / '_moved.py'
    package = vendor / 'pantheon'
    if not moved.is_file() or (package / '__init__.py').exists():
        return
    package.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(moved, package / '_moved.py')
    (package / '__init__.py').write_text('from ._moved import install\ninstall()\n')


def add_package_inits(vendor: Path) -> None:
    """An empty __init__.py in every vendored directory that has none; a
    vendored ``pantheon`` also resolves the names from before the split."""
    if (vendor / 'pantheon').is_dir():
        vendor_compat(vendor)
    for directory in [vendor / 'pantheonos', vendor / 'pantheon',
                      *[p for p in vendor.rglob('*') if p.is_dir()]]:
        if directory.is_dir() and directory != vendor and not (directory / '__init__.py').exists():
            (directory / '__init__.py').write_text('')
