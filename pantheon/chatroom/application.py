"""Compose the Agent App from explicit launcher-supplied integrations.

Local and Fleet launchers share this application. This is the owned composition
root, not the legacy ChatRoom facade. Serializing/delivering its capabilities and
switching the shipped entrypoints are separate launcher responsibilities.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.environment import AgentEnvironment
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.factory.bindings import AgentToolBindings
from pantheon.factory.provisioned_instances import ProvisionedAgentInstanceFactory
from pantheon.factory.template_manager import TemplateManager
from pantheon.internal.app_plugins import create_app_plugins
from pantheon.utils.model_scope import ModelCallScope


class AgentApplication(AgentRuntime):
    """One deployment, with conversation data and dependencies owned together.

    Settings/model scope must already be isolated and rooted inside data_dir.
    The launcher must fence legacy writers before explicitly migrating data;
    nothing here reads/imports the project's existing .pantheon directory.

    Ownership of delivered instance/auxiliary clients transfers on successful
    construction. The provisioner itself is a borrowed capability;
    close_dependencies closes the allocator (if owned here) only after
    Agent runs, plugins and instance clients drain. No global tool/model resolver
    is installed. Missing enabled plugin capabilities fail setup visibly.
    """
    def __init__(self, name, *, data_dir, namespace, projects: AppProjects,
                 settings, model_scope: ModelCallScope, provisioner,
                 ensure_services, validate_model, auxiliary_bindings=None,
                 output_resolver_for=None, close_dependencies=None, **kwargs):
        root = Path(data_dir).absolute()
        if (not isinstance(model_scope, ModelCallScope) or model_scope.settings is not settings
                or getattr(settings, '_environment', None) is None):
            raise ValueError('Agent App requires isolated settings and their own model scope')
        for path in (settings.work_dir, settings.user_home):
            if not Path(path).resolve().is_relative_to(root.resolve()):
                raise ValueError('Agent App configuration must belong to its data directory')
        if not callable(ensure_services) or not callable(validate_model):
            raise ValueError('Agent App requires explicit dependency and model validation')
        if auxiliary_bindings is not None and not isinstance(auxiliary_bindings, AgentToolBindings):
            raise ValueError('Invalid Agent auxiliary bindings')
        if close_dependencies is not None and not callable(close_dependencies):
            raise ValueError('Invalid Agent dependency cleanup')

        data = AgentAppData(root, namespace=namespace, projects=projects)
        try:
            factory = ProvisionedAgentInstanceFactory(data.instances, provisioner, model_scope=model_scope)
            templates = TemplateManager(settings=settings)

            async def plugins():
                return await create_app_plugins(settings=settings, model_scope=model_scope,
                    bindings_for=factory.bindings_for, auxiliary_bindings=auxiliary_bindings,
                    output_resolver_for=output_resolver_for)

            async def close():
                # Runtime calls this after its save/plugin/provider drain. Keep
                # cleanup failures visible while still closing the other owners.
                errors = []
                try:
                    await factory.shutdown()
                except Exception as exc:
                    errors.append(exc)
                if auxiliary_bindings is not None:
                    providers = {id(p): p for p in (*auxiliary_bindings.toolsets.values(),
                                                    *auxiliary_bindings.mcp_servers.values())}
                    results = await asyncio.gather(*(p.shutdown() for p in providers.values()),
                                                   return_exceptions=True)
                    errors.extend(r for r in results if isinstance(r, BaseException))
                if close_dependencies is not None:
                    try:
                        await close_dependencies()
                    except Exception as exc:
                        errors.append(exc)
                if errors:
                    from pantheon.apps.host_lifecycle import AppShutdownError
                    raise AppShutdownError(errors) from errors[0]

            environment = AgentEnvironment(projects=projects, templates=templates,
                settings=lambda: settings, ensure_services=ensure_services,
                create_agents=factory, validate_model=validate_model, close_agents=close,
                create_plugins=plugins, project_memory_dir=data.project_memory_dir)
            super().__init__(name=name, memory_dir=data.home_memory_dir,
                             environment=environment, **kwargs)
        except BaseException:
            # No async factory request/plugin setup has started in __init__.
            data.close()
            raise
        self.app_data = data
        self.instance_factory = factory
