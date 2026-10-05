"""Evolution as an ordinary App consuming an explicitly bound Agent execution.

Each service owns its sessions and state. Reasoning never discovers or embeds
an Agent. Node execution retains the original trusted native-App boundary;
remote isolation is supplied by a prepared placement owner.
"""
import asyncio
from collections.abc import Mapping
from functools import wraps
from pathlib import Path
import re
import ssl
from urllib.parse import urlsplit

from pantheon.apps.agent_execution_client import AgentExecutionClient
from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.toolset_backend import register_toolset
from pantheon.evolution.remote_execution import RemoteEvolutionBinding, OwnedMutationTool
from pantheon.evolution.lifetime import join_cleanup
from .evolution_toolset import EvolutionToolSet, EvolutionManager


# Deployment policy, not parameters that a tool caller may change mid-run.
OPTIONS = frozenset(('num_workers', 'evaluation_timeout', 'mutation_timeout',
    'function_weight', 'llm_weight', 'single_agent_mutation', 'use_analyzer',
    'analyzer_use_python', 'mutation_web_search', 'max_evaluations_per_mutation',
    'max_mutation_turns', 'max_tool_calls_per_mutation', 'sandbox_inspirations'))


def _configuration(config, credentials):
    if (not isinstance(config, Mapping) or set(config) - {'agent_credential', 'agent_ca_pem', 'options', 'execution', 'placement'}
            or not {'agent_credential', 'options', 'execution'} <= config.keys()
            or config['agent_credential'] != 'agent'
            or config['agent_credential'] not in credentials
            or not isinstance(config['options'], Mapping) or config['options'].keys() - OPTIONS
            or config['execution'] not in ('node', 'isolated')):
        raise ValueError('Evolution requires a prepared Agent dependency and execution policy')
    if ('placement' in config) != (config['execution'] == 'isolated'):
        raise ValueError('Supply placement only for isolated Evolution')
    options = dict(config['options'])
    import math
    for key, value in options.items():
        if key in ('function_weight', 'llm_weight'):
            valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
        elif key in ('single_agent_mutation', 'use_analyzer', 'analyzer_use_python', 'mutation_web_search', 'sandbox_inspirations'):
            valid = type(value) is bool
        else:
            valid = (value is None and key.startswith('max_')) or type(value) is int and 1 <= value <= 86400
        if not valid:
            raise ValueError('Invalid Evolution execution policy')
    credential = credentials[config['agent_credential']]
    tls = None
    if 'agent_ca_pem' in config:
        if urlsplit(credential.endpoint).hostname not in ('127.0.0.1', '::1', 'localhost'):
            raise ValueError('Private Agent trust is restricted to explicit loopback composition')
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls.load_verify_locations(cadata=config['agent_ca_pem'])
    return DependencyClient(credential, tls_context=tls), options


class ManagedEvolution(EvolutionToolSet):
    async def cleanup(self):
        # A failed run stop retains the execution client for reconciliation.
        await super().cleanup()
        if self._dependency_close is None:
            self._dependency_close = asyncio.create_task(self.execution_client.close())
        await join_cleanup(self._dependency_close)
        if self._placement_owner is not None:
            await self._placement_owner.close()


def create_service(config, credentials, workspace, state_directory, *, sandbox_factory=None, client_factory=None):
    dependency, options = _configuration(config, credentials)
    if (config['execution'] == 'isolated') != (sandbox_factory is not None):
        raise ValueError('Isolated Evolution requires its prepared placement owner')
    options['sandbox_mutation'] = config['execution'] == 'isolated'
    if not Path(workspace).is_absolute() or not Path(state_directory).is_absolute():
        raise ValueError('Evolution paths must be absolute')
    workspace, state = Path(workspace).resolve(), Path(state_directory).resolve()
    if not workspace.is_dir() or not state.is_dir() or workspace == state or state.is_relative_to(workspace):
        raise ValueError('Evolution requires separate existing workspace and private App state')
    client = (client_factory or AgentExecutionClient)(dependency)
    work = state / 'sessions'
    work.mkdir(exist_ok=True)
    manager = EvolutionManager(work)

    async def tools(workdir):
        from pantheon.apps.builtin.file import FileManagerToolSet
        from pantheon.apps.builtin.python import PythonInterpreterToolSet
        from pantheon.evolution.local_shell import LocalShellToolSet
        from pantheon.settings import Settings
        settings = Settings(state / 'settings', isolated_env=True, environment={}, user_home=state / 'home')
        provided = {
            'files': OwnedMutationTool(FileManagerToolSet('files', str(workdir), file_settings=settings, template_fallback=False)),
            'shell': OwnedMutationTool(LocalShellToolSet('shell', str(workdir)), cancel_on_stop=True),
            'python': OwnedMutationTool(PythonInterpreterToolSet('python', str(workdir), strict_lifecycle=True),
                                       cancel_on_stop=True, reset_after_iteration=True),
        }
        if options.get('mutation_web_search'):
            from pantheon.apps.builtin.web import WebToolSet
            search = WebToolSet('web')
            search.functions = {key: value for key, value in search.functions.items()
                                if key == 'duckduckgo_search'}
            provided['web'] = OwnedMutationTool(search)
        return provided

    async def analyzer_tools(workdir):
        from pantheon.apps.builtin.python import PythonInterpreterToolSet
        return {'python': OwnedMutationTool(PythonInterpreterToolSet('analysis', str(workdir), strict_lifecycle=True),
                                            cancel_on_stop=True, reset_after_iteration=True)}

    def binding(identity):
        return RemoteEvolutionBinding(client, state / 'receipts', run_id=identity,
            binding_id='evolution-agent', tool_factory=tools, analyzer_tool_factory=analyzer_tools,
            sandbox_factory=sandbox_factory)
    service = ManagedEvolution('evolution', str(work), manager=manager,
        execution_binding_factory=binding, execution_options=options)
    service.execution_client, service._dependency_close = client, None
    service._placement_owner = None

    def scoped(method):
        @wraps(method)
        async def call(**args):
            args.pop('context_variables', None)
            for key in ('codebase_path', 'output_path'):
                if args.get(key) is not None:
                    path = Path(args[key]).expanduser()
                    path = (workspace / path).resolve() if not path.is_absolute() else path.resolve()
                    if not path.is_relative_to(workspace):
                        raise ValueError('Evolution file paths must stay in its prepared workspace')
                    args[key] = str(path)
            return await method(**args, context_variables={})
        return call
    service.functions = {name: (scoped(method), metadata) for name, (method, metadata) in service.functions.items()}
    return service


async def register(ctx):
    configuration = load_runtime_configuration(required=True)
    config = configuration.values.get('evolution')
    _configuration(config, configuration.credentials)  # Reject before credentials/network use.
    modal_owner = None
    service = None
    async def cleanup():
        if service is not None:
            await service.cleanup()
        if modal_owner is not None:
            await modal_owner.close()
    try:
        factory = None
        if config['execution'] == 'isolated':
            from pantheon.apps.modal_credentials import ModalCredentialOwner
            from pantheon.apps.modal_image import ModalAppImage
            from pantheon.apps.modal_placement import PreparedModalApp
            placement = config['placement']
            if (not isinstance(placement, Mapping)
                    or set(placement) != {'kind', 'image', 'app_name', 'timeout', 'cpu', 'memory', 'gpu', 'credential'}
                    or placement['kind'] != 'modal' or not isinstance(placement['image'], Mapping)):
                raise ValueError('Invalid prepared isolated App placement')
            image = ModalAppImage(**placement['image'])
            if (image.app_id != 'evolution-tools'
                    or not isinstance(image.version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', image.version)
                    or not isinstance(image.artifact_sha256, str) or not re.fullmatch('[a-f0-9]{64}', image.artifact_sha256)
                    or not isinstance(image.base_image_id, str) or not re.fullmatch('im-[A-Za-z0-9]+', image.base_image_id)
                    or placement['credential'] != 'modal' or 'modal' not in configuration.credentials):
                raise ValueError('Evolution requires the reviewed isolated tool App')
            # Validate resource/image policy before opening the control-plane client.
            factory = PreparedModalApp(image, app_name=placement['app_name'],
                timeout=placement['timeout'], cpu=placement['cpu'], memory=placement['memory'], gpu=placement['gpu'])
            # The credential stays on this controller node, never in tool inputs.
            modal_owner = ModalCredentialOwner(configuration.credentials[placement['credential']])
            client = await modal_owner.start()
            factory.modal_client = client
        service = create_service(config, configuration.credentials, ctx.workspace, ctx.state_dir, sandbox_factory=factory)
        service._placement_owner = modal_owner
        await register_toolset(ctx, service)
    except BaseException:
        await cleanup()
        raise
