"""Run code against a package's own vendored tree, and nothing else.

`python -I` still loads the environment's site-packages, where an editable
install of this checkout answers for `pantheon` and `pantheonos`: its meta path
finder resolves any submodule the vendored tree lacks from the checkout, so a
package missing a module it imports would pass. The prelude removes that finder
and the checkout's directories before the code runs.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def isolated(code: str) -> str:
    return (
        "import sys\n"
        "sys.meta_path[:] = [f for f in sys.meta_path if 'editable' not in (\n"
        "    str(getattr(f, '__module__', '')) + str(getattr(f, '__name__', type(f).__name__))).lower()]\n"
        f"sys.path[:] = [p for p in sys.path if not (p or '.').startswith({str(ROOT)!r})]\n"
        "for _name in [m for m in sys.modules if m.split('.')[0] in ('pantheon', 'pantheonos')]:\n"
        "    del sys.modules[_name]\n"
        + code
    )
