"""Notebook engine and GUI as an ordinary, explicitly configured Fleet App."""
from collections.abc import Mapping
from functools import wraps
import os
from pathlib import Path
import platform
import sys

from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.toolset_backend import register_toolset
from pantheon.toolset import tool
from .integrated_notebook import IntegratedNotebookToolSet


class ManagedNotebook(IntegratedNotebookToolSet):
    @tool(exclude=True)
    async def execution_host(self) -> dict:
        """Describe the node that owns this notebook workspace and its kernels."""
        return {'hostname': platform.node(), 'os': platform.system(),
                'workspace': self.workdir, 'python': sys.executable}


def create_service(config, workspace, state_directory):
    if (not isinstance(config, Mapping)
            or set(config) != {'execution_timeout', 'execution_logging'}
            or type(config['execution_timeout']) is not int or config['execution_timeout'] < 1
            or type(config['execution_logging']) is not bool):
        raise ValueError('Notebook requires explicit execution_timeout and execution_logging')
    workspace, state = Path(workspace), Path(state_directory)
    if any(not p.is_absolute() or not p.is_dir() for p in (workspace, state)):
        raise ValueError('Notebook needs existing absolute workspace and App state directories')
    # Kernel selection is workspace state, shared by GUI and authorized Agents.
    # Never inherit another notebook service's Jupyter data directory.
    os.environ['JUPYTER_DATA_DIR'] = str(workspace / '.pantheon' / 'jupyter')
    service = ManagedNotebook('integrated_notebook', workdir=str(workspace),
        streaming_mode='local', execution_timeout=config['execution_timeout'],
        execution_logging=config['execution_logging'], execution_log_dir=state / 'logs',
        strict_lifecycle=True)
    service.persistence_dir = state
    service.persistence_file = state / 'notebook_contexts.json'

    def bind(method):
        @wraps(method)
        async def call(**args):
            args.pop('context_variables', None)  # Only the generic host injects this.
            if isinstance(args.get('notebook_path'), str):
                valid, _, path = service.notebook_contents._validate_path(args['notebook_path'])
                if valid:
                    args['notebook_path'] = str(path.resolve())
            return await method(**args, context_variables={
                'client_id': 'desktop', 'workdir': str(workspace)})
        return call
    service.functions = {name: (bind(method), metadata)
                         for name, (method, metadata) in service.functions.items()}
    return service


async def register(ctx):
    configuration = load_runtime_configuration(required=True)
    service = create_service(configuration.values.get('notebook'), ctx.workspace, ctx.state_dir)
    await register_toolset(ctx, service)
