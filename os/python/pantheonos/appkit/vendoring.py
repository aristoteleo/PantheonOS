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

#: The platform package's directory.
PLATFORM_ROOT = Path(__file__).resolve().parents[1]


def agent_package_dir() -> Path | None:
    """The installed Agent package's directory (site-packages/pantheon, or a
    checkout's apps/agent/pantheon): located by name, not imported."""
    import importlib.util
    try:
        spec = importlib.util.find_spec('pantheon')
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0]).resolve()


def package_root(package: str) -> Path:
    """Where a runtime package's files are: pantheonos, or the Agent's."""
    if package == 'pantheonos':
        return PLATFORM_ROOT
    root = agent_package_dir()
    if root is None:
        raise FileNotFoundError('The Agent package (pantheon) is not installed here')
    return root

_PLATFORM_DIRS = ('appkit/', 'general_team/', 'local/', 'store/', 'utils/', 'remote/', 'models/', 'model_contracts/',
                  'funcdesc/', 'data/', 'package_runtime/')
_PLATFORM_FILES = ('toolset.py', 'settings.py', 'constant.py', 'apphost.py')
_RENAMED = {
    'internal/package_runtime/context.py': ('pantheonos', 'package_runtime/context.py'),
    'apps/general_agent_release.py': ('pantheon', 'agent_release.py'),
    'utils/image_detection.py': ('pantheon', 'image_detection.py'),
    'apps/general_team.py': ('pantheonos', 'general_team/team.py'),
    'apps/general_agent_preset.py': ('pantheonos', 'general_team/agent_preset.py'),
    **{f'apps/{name}.py': ('pantheonos', f'general_team/{name}.py') for name in (
        'agent_defaults', 'agent_deployment', 'local_agent', 'release_profile')},
}


def runtime_source(name: str) -> tuple[str, str]:
    """(package, path in it) of a runtime file named as before the split."""
    name = name.lstrip('/')
    if name in _RENAMED:
        return _RENAMED[name]
    if name.startswith('apps/'):
        # The App framework was pantheon/apps, then pantheonos/apps.
        name = 'appkit/' + name[len('apps/'):]
    if name in _PLATFORM_FILES or name.startswith(_PLATFORM_DIRS):
        return 'pantheonos', name
    return 'pantheon', name


def runtime_path(name: str) -> Path:
    package, path = runtime_source(name)
    return package_root(package) / path


def vendor_file(name: str, vendor: Path) -> Path:
    """Copy a runtime file into vendor/<package>/<path>."""
    package, path = runtime_source(name)
    origin = package_root(package) / path
    if origin.is_symlink() or not origin.is_file():
        raise ValueError(f'App release input must be a regular file: {origin}')
    target = vendor / package / path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origin, target)
    return target


def vendor_tree(name: str, vendor: Path) -> Path:
    """Copy a runtime package directory (``funcdesc``) into the vendor tree."""
    package, path = runtime_source(name.rstrip('/') + '/')
    target = vendor / package / path.rstrip('/')
    shutil.copytree(package_root(package) / path.rstrip('/'), target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return target


def builtin_app_dir(vendor: Path) -> Path:
    """Where vendored first-party App modules go (pantheonos.appkit.builtin)."""
    return vendor / 'pantheonos' / 'appkit' / 'builtin'


def vendor_compat(vendor: Path) -> None:
    """A vendored ``pantheon`` that resolves the platform's names from before
    the split (``pantheon.toolset`` is ``pantheonos.toolset``): App code
    written against them keeps working."""
    agent = agent_package_dir()
    moved = agent / '_moved.py' if agent else None
    package = vendor / 'pantheon'
    if moved is None or not moved.is_file() or (package / '__init__.py').exists():
        return
    package.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(moved, package / '_moved.py')
    (package / '__init__.py').write_text('from ._moved import install\ninstall()\n')


def vendor_platform_compat(vendor: Path) -> None:
    """A vendored ``pantheonos`` that resolves the names of modules that moved
    (``pantheonos.apps`` is ``pantheonos.appkit``), as the installed one does."""
    package = vendor / 'pantheonos'
    if (package / '__init__.py').exists():
        return
    shutil.copyfile(PLATFORM_ROOT / '_moved.py', package / '_moved.py')
    (package / '__init__.py').write_text('from ._moved import install\ninstall()\n')


def add_package_inits(vendor: Path) -> None:
    """An empty __init__.py in every vendored directory that has none; a
    vendored ``pantheon`` or ``pantheonos`` also resolves the names from before
    a move."""
    if (vendor / 'pantheon').is_dir():
        vendor_compat(vendor)
    if (vendor / 'pantheonos').is_dir():
        vendor_platform_compat(vendor)
    for directory in [vendor / 'pantheonos', vendor / 'pantheon',
                      *[p for p in vendor.rglob('*') if p.is_dir()]]:
        if directory.is_dir() and directory != vendor and not (directory / '__init__.py').exists():
            (directory / '__init__.py').write_text('')
