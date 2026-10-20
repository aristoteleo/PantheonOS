"""The platform runs without the Agent: `pantheonos` never imports `pantheon`
(docs/agent/agent-app-extraction.md, Ownership). It may look for the installed
Agent package by name only where listed here, each for a stated reason."""
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "pantheonos"

# file -> why it may load the Agent package by name (and work without it)
BY_NAME = {
    "toolset.py": "a tool service samples a model through the Agent's sampler when it is installed",
    "local/cli.py": "`pantheon agent chat` runs the Agent App's terminal frontend",
    "settings.py": "the Agent's factory templates are located, and its package manager reset after a reload",
    "appkit/vendoring.py": "App builds vendor Agent files, and the local owner carries the Agent's terminal",
}
LOADS_BY_NAME = re.compile(r"""(import_module|find_spec|sys\.modules\.get)\(\s*["']pantheon[."']""")


def _platform_files():
    return [p for p in PLATFORM.rglob("*.py") if "__pycache__" not in p.parts]


def _imports(path):
    tree = ast.parse(path.read_text())
    module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    if path.name == "__init__.py":
        package = module[: -len(".__init__")]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                yield node.lineno, ".".join(base + ([node.module] if node.module else []))
            else:
                yield node.lineno, node.module or ""


def test_the_platform_never_imports_the_agent():
    found = [f"{p.relative_to(ROOT)}:{line}: {name}" for p in _platform_files()
             for line, name in _imports(p) if name == "pantheon" or name.startswith("pantheon.")]
    assert not found, "pantheonos imports pantheon:\n" + "\n".join(found)


def test_the_platform_looks_for_the_agent_by_name_only_where_it_says_why():
    found = sorted({str(p.relative_to(PLATFORM)) for p in _platform_files() if LOADS_BY_NAME.search(p.read_text())})
    assert found == sorted(BY_NAME), found


def test_names_from_before_the_split_are_the_same_modules():
    code = ("import pantheon.utils.log as a, pantheonos.utils.log as b; assert a is b; "
            "from pantheon.toolset import ToolSet as A; from pantheonos.toolset import ToolSet as B; assert A is B; "
            "import pantheon.apps.general_agent_release as r, pantheon.agent_release as s; assert r is s; "
            "from pantheon.internal.package_runtime.context import load_context; "
            "from pantheonos.package_runtime.context import load_context as l2; assert load_context is l2")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
    # `python -m` with a former name runs the moved module.
    out = subprocess.run([sys.executable, "-m", "pantheon.apps", "schema"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    assert '"$schema"' in out or '"properties"' in out


def test_names_from_before_the_appkit_rename_are_the_same_modules():
    # The App framework was pantheonos.apps (and pantheon.apps before that).
    code = ("import pantheonos.apps.registry as a, pantheonos.appkit.registry as b; assert a is b; "
            "import pantheon.apps.vendoring as c, pantheonos.appkit.vendoring as d; assert c is d; "
            "from pantheonos.apps.builtin.file import FileManagerToolSet as F; "
            "from pantheonos.appkit.builtin.file import FileManagerToolSet as G; assert F is G")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
    out = subprocess.run([sys.executable, "-m", "pantheonos.apps", "schema"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    assert '"$schema"' in out or '"properties"' in out


def test_a_vendored_platform_resolves_the_old_names(tmp_path):
    from pantheonos.appkit.vendoring import add_package_inits, vendor_file
    # App builds may still name the framework's files as before.
    assert vendor_file("apps/runtime_config.py", tmp_path) == tmp_path / "pantheonos/appkit/runtime_config.py"
    add_package_inits(tmp_path)
    code = (f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
            "import pantheonos.apps.runtime_config as a, pantheonos.appkit.runtime_config as b; "
            f"assert a is b and b.__file__.startswith({str(tmp_path)!r}), b.__file__")
    subprocess.run([sys.executable, "-I", "-c", code], check=True)


def test_importing_the_platform_loads_no_agent_module():
    code = ("import sys, pantheonos.appkit.registry, pantheonos.toolset, pantheonos.settings, pantheonos.models.client; "
            "loaded = [m for m in sys.modules if m == 'pantheon' or m.startswith('pantheon.')]; "
            "assert not loaded, loaded")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)
