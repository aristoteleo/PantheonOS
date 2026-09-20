"""Run the existing notebook engine on a Fleet node, without an Agent server."""
import importlib.util
import os
import platform
import sys
from functools import wraps
from pathlib import Path


async def register(ctx):
    # HOME belongs to one Fleet instance; selected kernels belong to the node's
    # notebook workspace and must survive App upgrades and parallel instances.
    os.environ.setdefault('JUPYTER_DATA_DIR', str(ctx.workspace / '.pantheon' / 'jupyter'))
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
            if isinstance(args.get('notebook_path'), str):
                valid, _, path = notebook.notebook_contents._validate_path(args['notebook_path'])
                if valid:
                    # Agent calls use absolute paths, while the file picker
                    # uses relative paths. They must address the same kernel.
                    args['notebook_path'] = str(path.resolve())
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
