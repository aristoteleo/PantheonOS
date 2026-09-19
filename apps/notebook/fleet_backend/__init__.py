"""Run the existing notebook engine on a Fleet node, without an Agent server."""
import importlib.util
import platform
import sys
from functools import wraps
from pathlib import Path


async def register(ctx):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        'fleet_notebook', root / '__init__.py', submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    notebook = module.IntegratedNotebookToolSet(
        'integrated_notebook', workdir=str(ctx.workspace),
        streaming_mode='local', execution_logging=False)
    # Keep kernel/session bookkeeping outside the user's notebook directory,
    # including when legacy Agent notebooks still use that same directory.
    notebook.persistence_dir = ctx.state_dir
    notebook.persistence_file = ctx.state_dir / 'notebook_contexts.json'
    await notebook.run_setup()
    # All calls in this node-local App share kernels, including UI actions and
    # Agent calls. Caller-supplied cloud contexts cannot redirect local paths.
    def expose(method):
        @wraps(method)
        async def call(**args):
            args.pop('context_variables', None)
            args.pop('session_id', None)
            return await method(**args, context_variables={
                'client_id': 'desktop', 'workdir': str(ctx.workspace)})
        ctx.method(call)
        ctx.concurrent_methods.add(call.__name__)
    for method, _ in notebook.functions.values():
        expose(method)

    @ctx.method
    def execution_host():
        return {'hostname': platform.node(), 'os': platform.system(),
                'workspace': str(ctx.workspace), 'python': sys.executable}

    ctx.on_cleanup(notebook.cleanup)
