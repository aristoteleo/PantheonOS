"""Names from before the platform moved to `pantheonos`.

The platform's Python side (Apps, hosting, local owner, Store, model access
and shared infrastructure) moved out of this package into `pantheonos`. Code
still naming it here (installed App manifests, the Agent's Store repository,
saved configs, `python -m pantheon.apps ...`) gets the very same modules:
`pantheon.utils.log` IS `pantheonos.utils.log`.
"""
import importlib as _importlib
import importlib.abc as _abc
import importlib.util as _util
import sys as _sys

_MOVED = (
    ("pantheon.apps.general_agent_release", "pantheon.agent_release"),
    ("pantheon.utils.image_detection", "pantheon.image_detection"),
    ("pantheon.internal.package_runtime.context", "pantheonos.package_runtime.context"),
    ("pantheon.apps", "pantheonos.appkit"),
    *((f"pantheon.{name}", f"pantheonos.{name}") for name in (
        "local", "store", "utils", "remote", "models", "model_contracts", "funcdesc",
        "toolset", "settings", "constant", "apphost")),
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

    # runpy (`python -m pantheon.apps ...`) runs the moved module's code.
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
