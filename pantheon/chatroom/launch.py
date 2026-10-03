"""Serialized Agent App backend for the generic App host.

This is opt-in until the frontend package and owner-side bootstrap are ready.
Prepared configuration carries only this generation's bindings; no combined
ChatRoom, platform host, global resolver or Fleet owner credential is loaded.
"""
from pathlib import Path, PurePosixPath, PureWindowsPath
import asyncio
import ssl

from pantheon.apps.runtime_config import load_runtime_configuration, RuntimeConfiguration
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
                    'settings', 'models', 'dependencies', 'auxiliary', 'view_dependencies'}
                    or type(spec.get('protocol')) is not int or spec['protocol'] != 1):
                raise ValueError
            projects = AppProjects(spec['projects'], active_id=spec.get('active_project'),
                                   default_id=spec.get('default_project'))
            dependencies = spec['dependencies']
            if not isinstance(dependencies, dict) or set(dependencies) != {'allocator', 'profiles'}:
                raise ValueError
            profiles = dependencies['profiles']
            consumer = dict(node_id=configuration.node_id, instance_id=configuration.instance_id,
                            revision=configuration.revision, generation=configuration.generation)
            tls = ssl.create_default_context(cafile=dependency_ca_file) if dependency_ca_file else None
            allocator = RemoteDependencyBindings(DependencyClient(
                configuration.credentials[dependencies['allocator']], tls_context=tls))
            provisioner = DependencyInstanceProvisioner(allocator, consumer=consumer,
                                                        profiles=profiles, tls_context=tls)
            models = AppModels(Path(data_dir).absolute(), defaults=spec.get('settings', {}),
                               config=spec['models'], credentials=configuration.credentials)
            auxiliary = _bindings_from_spec(configuration, spec['auxiliary'], tls) if 'auxiliary' in spec else None
            views = AgentViewServices(configuration, projects, spec.get('view_dependencies', {}), tls)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError('Agent launch configuration is invalid or incomplete') from None

        async def ensure(kind, names):
            group = {'toolset': 'toolsets', 'mcp': 'mcp_servers'}.get(kind)
            if group is None:
                raise ValueError('Unknown Agent dependency kind')
            builtins = {'think', 'task'} if kind == 'toolset' else set()
            if set(names) - builtins - set(profiles[group]):
                raise ValueError('Agent requested a dependency absent from its launch configuration')
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
            results = await asyncio.gather(views.close(), allocator.shutdown(), return_exceptions=True)
            errors = [result for result in results if isinstance(result, BaseException)]
            if errors:
                from pantheon.apps.host_lifecycle import AppShutdownError
                raise AppShutdownError(errors) from errors[0]

        super().__init__(name, data_dir=data_dir, namespace=spec['namespace'], projects=projects,
            settings=models.settings, model_scope=models.scope, provisioner=provisioner,
            ensure_services=ensure, validate_model=models.validate, auxiliary_bindings=auxiliary,
            output_resolver_for=output_resolver_for, close_dependencies=close_dependencies, **kwargs)
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
    async def list_available_models(self):
        """List only this App's model bindings, never another runtime's catalog."""
        await self.app_models.refresh()
        return self.app_models.selector.list_available_models()
