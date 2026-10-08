"""Serialized Agent App backend for the generic App host.

This is opt-in until the frontend package and owner-side bootstrap are ready.
Prepared configuration carries only this generation's bindings; no combined
ChatRoom, platform host, global resolver or Fleet owner credential is loaded.
"""
from pathlib import Path, PurePosixPath, PureWindowsPath
import asyncio
import ssl

from pantheon.apps.runtime_config import load_runtime_configuration, RuntimeConfiguration
from pantheon.apps.agent_defaults import dependency_defaults
from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.dependency_binding_client import RemoteDependencyBindings
from pantheon.chatroom.app_data import AppProjects
from pantheon.chatroom.app_models import AppModels
from pantheon.chatroom.application import AgentApplication
from pantheon.chatroom.view_services import AgentViewServices
from pantheon.factory.bindings import _thaw, _bindings_from_spec
from pantheon.factory.dependency_provisioner import DependencyInstanceProvisioner
from pantheon.toolset import tool


class ConfiguredAgentApplication(AgentApplication):
    def __init__(self, name, *, data_dir, workdir=None, configuration=None,
                 dependency_ca_file=None, **kwargs):
        configuration = (load_runtime_configuration(required=True) if configuration is None
                         else configuration)
        if (not isinstance(configuration, RuntimeConfiguration) or configuration.component != 'backend'
                or not configuration.owner or not configuration.node_id):
            raise ValueError('Supply a prepared Agent backend configuration')
        try:
            spec = _thaw(configuration.values['agent'])
            if (not isinstance(spec, dict) or set(spec) - {
                    'protocol', 'namespace', 'projects', 'active_project', 'default_project',
                    'settings', 'models', 'dependencies', 'auxiliary', 'view_dependencies', 'trust_roots_pem', 'rpc_origin'}
                    or type(spec.get('protocol')) is not int or spec['protocol'] != 1):
                raise ValueError
            projects = AppProjects(spec['projects'], active_id=spec.get('active_project'),
                                   default_id=spec.get('default_project'))
            dependencies = spec['dependencies']
            if (not isinstance(dependencies, dict) or not {'allocator', 'profiles'} <= dependencies.keys()
                    or dependencies.keys() - {'allocator', 'profiles', 'defaults'}):
                raise ValueError
            profiles = dependencies['profiles']
            defaults = dependency_defaults(dependencies.get('defaults', {'toolsets': [], 'mcp_servers': []}),
                                           profiles=profiles)
            consumer = dict(node_id=configuration.node_id, instance_id=configuration.instance_id,
                            revision=configuration.revision, generation=configuration.generation)
            tls = ssl.create_default_context(cafile=dependency_ca_file) if dependency_ca_file else None
            if 'trust_roots_pem' in spec:
                pem = spec['trust_roots_pem']
                if not isinstance(pem, str) or not pem or len(pem) > 16384 or dependency_ca_file:
                    raise ValueError
                # Prepared trust travels with this release's dependency grants.
                # It does not change process-wide CA/proxy settings or model API
                # credentials, and never falls back to ambient trust on error.
                tls = ssl.create_default_context(cadata=pem)
            if 'rpc_origin' in spec and (not isinstance(spec['rpc_origin'], str)
                    or configuration.credentials[dependencies['allocator']].endpoint != spec['rpc_origin'] + '/rpc'):
                raise ValueError
            allocator = RemoteDependencyBindings(DependencyClient(
                configuration.credentials[dependencies['allocator']], tls_context=tls))
            provisioner = DependencyInstanceProvisioner(allocator, consumer=consumer,
                profiles=profiles, tls_context=tls, owner=configuration.owner, rpc_origin=spec.get('rpc_origin'))
            models = AppModels(Path(data_dir).absolute(), defaults=spec.get('settings', {}),
                               config=spec['models'], credentials=configuration.credentials, tls_context=tls)
            auxiliary = _bindings_from_spec(configuration, spec['auxiliary'], tls, profiles=profiles) if 'auxiliary' in spec else None
            views = AgentViewServices(configuration, projects, spec.get('view_dependencies', {}), tls, profiles=profiles)
        except (KeyError, TypeError, ValueError, AttributeError, ssl.SSLError):
            raise ValueError('Agent launch configuration is invalid or incomplete') from None

        async def ensure(kind, names):
            group = {'toolset': 'toolsets', 'mcp': 'mcp_servers'}.get(kind)
            if group is None:
                raise ValueError('Unknown Agent dependency kind')
            builtins = {'think', 'task'} if kind == 'toolset' else set()
            missing = sorted(set(names) - builtins - set(profiles[group]))
            if missing:
                raise ValueError('Agent requested a dependency absent from its launch configuration: '
                                 + ', '.join(missing) + '. Configure these App bindings before running this team.')
            # Allocation itself happens only after reserving the durable Agent
            # identity, rather than sharing one startup Shell across all chats.
            await models.refresh()

        def output_resolver_for(agent):
            bindings = self.instance_factory.bindings_for(agent)
            files = bindings.toolsets.get('file_manager')
            if files is None:
                raise ValueError('Task output registration requires this Agent instance\'s Files binding')

            async def resolve(path, context, node_id=None):
                if not isinstance(path, str) or not path:
                    raise ValueError('Supply an output path')
                # The exact Files grant owns workspace/path authorization. Do
                # not resolve paths against the Agent process's filesystem.
                absolute = PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()
                if node_id and not absolute:
                    raise ValueError('An explicit node requires an absolute output path')
                result = await files.call_tool('stat_path', {'file_path': path})
                if (not isinstance(result, dict) or result.get('success') is not True
                        or type(result.get('exists')) is not bool or type(result.get('is_dir')) is not bool
                        or not all(isinstance(result.get(key), str) and result[key]
                                   for key in ('node_id', 'path', 'store_path'))
                        or node_id and node_id != result['node_id']):
                    raise ValueError('Files returned unavailable or mismatched output metadata')
                return {**result, 'source': {'node_id': result['node_id'], 'path': result['path']}}
            return resolve

        async def close_dependencies():
            results = await asyncio.gather(views.close(), allocator.shutdown(), models.aclose(), return_exceptions=True)
            errors = [result for result in results if isinstance(result, BaseException)]
            if errors:
                from pantheon.apps.host_lifecycle import AppShutdownError
                raise AppShutdownError(errors) from errors[0]

        super().__init__(name, data_dir=data_dir, namespace=spec['namespace'], projects=projects,
            settings=models.settings, model_scope=models.scope, provisioner=provisioner,
            default_dependencies=defaults,
            ensure_services=ensure, validate_model=models.validate, auxiliary_bindings=auxiliary,
            output_resolver_for=output_resolver_for, close_dependencies=close_dependencies,
            model_configuration={'owner': configuration.owner, 'node_id': configuration.node_id,
                                 'models': spec['models'], 'credentials': {
                                     alias: credential.endpoint for alias, credential in configuration.credentials.items()}},
            dependency_configuration={'owner': configuration.owner, 'node_id': configuration.node_id,
                                      'profiles': profiles, 'defaults': defaults},
            **kwargs)
        self.app_models = models
        self.view_services = views

    @tool(exclude=True)
    async def call_view_service(self, workspace_path: str, service: str, method: str, args: dict) -> dict:
        """Call a GUI dependency granted for this attached workspace.

        Never resolves an Agent execution session or a global service. The
        provider's grant owns workspace enforcement and argument injection.
        """
        return await self.view_services.call(workspace_path, service, method, args)

    async def run_setup(self):
        await self.app_models.refresh()
        await super().run_setup()

    @tool(exclude=True)
    async def get_model_details(self, model: str) -> dict:
        """Detail card for the picker's info dialog, for models this App can use.

        A Fleet model published from the platform's OpenRouter catalog shows that
        public catalog entry (price, context, modalities); any other Fleet model
        shows what its service published. Nothing here selects or calls a model.
        """
        try:
            if model.startswith('fleet-model://'):
                from pantheon.models.client import parse_ref
                deployment, published = parse_ref(model)
            else:
                deployment, published = '', model
            if published.startswith('openrouter/'):
                from pantheon.utils import openrouter_catalog
                try:
                    await openrouter_catalog.ensure_fresh()
                except Exception:
                    pass
                card = openrouter_catalog.get_model_card(published)
                if card:
                    return {'success': True, 'source': 'openrouter', 'info': {**card, 'model': model}}
            if deployment:
                info = self.app_models.scope.model_info(model) or {}
                return {'success': True, 'source': 'fleet', 'info': {
                    'model': model, 'name': published, 'vendor': deployment,
                    'max_input_tokens': info.get('max_input_tokens') or info.get('context'),
                    'max_output_tokens': None, 'input_cost_per_million': None, 'output_cost_per_million': None,
                    'capabilities': {k: info.get('supports_' + k, info.get(k)) for k in ('vision', 'tools', 'reasoning')},
                    'modalities': {'image': info.get('supports_vision', info.get('vision')), 'pdf': None, 'audio': None}}}
            return {'success': True, 'source': None, 'info': {'model': model, 'name': model}}
        except Exception as exc:
            return {'success': False, 'message': str(exc) or type(exc).__name__}

    @tool(exclude=True)
    async def list_available_models(self):
        """List only this App's model bindings, never another runtime's catalog."""
        await self.app_models.refresh()
        return self.app_models.catalog()
