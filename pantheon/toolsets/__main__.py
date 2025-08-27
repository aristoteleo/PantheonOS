import fire
import ast
import importlib
from pathlib import Path

from ..constant import HYPHA_SERVER_URL


HERE = Path(__file__).parent

def get_toolset_modules():
    return [
        f.stem for f in HERE.glob("*")
        if (not f.stem.startswith("__")) and (f.stem) not in ["bio"]
    ]


def extract_exported_classes(content: str):
    """Extract exported classes from a module."""
    tree = ast.parse(content)
    classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if isinstance(node.targets[0], ast.Name) and node.targets[0].id == "__all__":
                classes.extend(node.value.elts)
    return [c.s for c in classes]


def import_toolset_class(module_name: str):
    module_path = HERE / module_name
    if module_path.is_dir():
        content = (module_path / "__init__.py").read_text()
    else:
        content = (HERE / f"{module_name}.py").read_text()
    classes = extract_exported_classes(content)
    if len(classes) == 1:
        class_name = classes[0]
        mod = importlib.import_module(f"pantheon.toolsets.{module_name}")
        return getattr(mod, class_name)
    else:
        raise ImportError(f"Module {module_name} has incorrect number of classes: {classes}")


def list_toolsets():
    """List all available toolsets."""
    print("Available toolsets:")
    for module_name in sorted(get_toolset_modules()):
        toolset_class = import_toolset_class(module_name)
        doc = toolset_class.__doc__
        if doc:
            lines = doc.split("\n")
            for line in lines:
                if line.strip() != "":
                    break
            print(f"- {module_name}: {line.strip()}")
        else:
            print(f"- {module_name}")

def detail(toolset_name: str):
    """View detailed information about a toolset."""
    toolset_class = import_toolset_class(toolset_name)
    doc = toolset_class.__doc__
    print(doc)


async def start(
    toolset_name: str,
    service_name: str = None,
    mcp: bool = False,
    mcp_kwargs: dict = {},
    hypha: bool = False,
    hypha_server_url: str | None = None,
    hypha_kwargs: dict = {},
    **kwargs,
    ):
    """Start a remote toolset.

    Args:
        toolset_name: The name of the toolset to run.
        mcp: Whether to run the toolset as an MCP server.
        mcp_kwargs: The keyword arguments for the MCP server.
        hypha: Whether to run the toolset as a Hypha service.
        hypha_server_url: The URL of the Hypha server.
        hypha_kwargs: The keyword arguments for the Hypha service.
    """
    if service_name is None:
        service_name = toolset_name
    toolset_class = import_toolset_class(toolset_name)
    toolset = toolset_class(service_name, **kwargs)
    if mcp:
        await toolset.run_as_mcp(**mcp_kwargs)
    elif hypha:
        if hypha_server_url is None:
            hypha_server_url = HYPHA_SERVER_URL
        await toolset.run_as_hypha_service(hypha_server_url, **hypha_kwargs)
    else:
        await toolset.run()


fire.Fire({
    "start": start,
    "list": list_toolsets,
    "detail": detail,
})