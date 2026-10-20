"""Names from before parts of `pantheonos` moved.

The App framework was `pantheonos.apps`; it is `pantheonos.appkit`, so the
name no longer reads like the Apps themselves (<repo>/apps), and the General
Team's composition it held is `pantheonos.general_team`. Code still naming
the old place (an installed App's manifest or backend, a saved config,
`python -m pantheonos.apps ...` from an older launcher) gets the very same
modules: `pantheonos.apps.registry` IS `pantheonos.appkit.registry`.

Entries are checked in order: a specific name comes before its package's.
"""
import importlib as _importlib
import importlib.abc as _abc
import importlib.util as _util
import sys as _sys

_MOVED = (
    # The General Team's composition left the App framework.
    ("pantheonos.apps.general_team", "pantheonos.general_team.team"),
    ("pantheonos.apps.general_agent_preset", "pantheonos.general_team.agent_preset"),
    *((f"pantheonos.apps.{name}", f"pantheonos.general_team.{name}") for name in (
        "agent_defaults", "agent_deployment", "local_agent", "release_profile")),
    ("pantheonos.apps", "pantheonos.appkit"),
)


def _moved_to(fullname):
    for old, new in _MOVED:
        if fullname == old or fullname.startswith(old + "."):
            return new + fullname[len(old):]
    return None


class _MovedFinder(_abc.MetaPathFinder, _abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        new = _moved_to(fullname)
        if new is None:
            return None
        spec = _util.find_spec(new)
        if spec is None:
            return None
        return _util.spec_from_loader(fullname, self, is_package=spec.submodule_search_locations is not None)

    def create_module(self, spec):
        return _importlib.import_module(_moved_to(spec.name))

    def exec_module(self, module):
        pass

    # runpy (`python -m pantheonos.apps ...`) runs the moved module's code.
    def get_code(self, fullname):
        new = _moved_to(fullname)
        spec = _util.find_spec(new)
        return spec.loader.get_code(new) if spec and spec.loader else None

    def is_package(self, fullname):
        spec = _util.find_spec(_moved_to(fullname))
        return bool(spec and spec.submodule_search_locations is not None)


def install():
    if not any(isinstance(f, _MovedFinder) for f in _sys.meta_path):
        _sys.meta_path.insert(0, _MovedFinder())
