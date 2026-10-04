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
from pantheon.toolset import tool
from pantheon.factory.instances import _identifier
from pantheon.dependency_provider import _drain_call


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
                 output_resolver_for=None, close_dependencies=None, model_configuration=None,
                 default_dependencies=None, **kwargs):
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

        data = AgentAppData(root, namespace=namespace, projects=projects, model_configuration=model_configuration)
        try:
            factory = ProvisionedAgentInstanceFactory(data.instances, provisioner, model_scope=model_scope,
                                                       default_dependencies=default_dependencies)
            templates = TemplateManager(settings=settings, seed_settings=False)

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
                create_plugins=plugins, project_memory_dir=data.project_memory_dir,
                prepare_agent_configs=factory.prepare_configs)
            super().__init__(name=name, memory_dir=data.home_memory_dir,
                             environment=environment, **kwargs)
        except BaseException:
            # No async factory request/plugin setup has started in __init__.
            data.close()
            raise
        self.app_data = data
        self.instance_factory = factory
        self._retiring_chats = set(data.instances.retirements())
        self._conversation_deletions = {}

    async def get_team_for_chat(self, chat_id, save_to_memory=True):
        if (chat_id in self._retiring_chats
                and asyncio.current_task() not in getattr(self, '_agent_chat_calls', {}).get(chat_id, {})):
            raise ValueError('Conversation is retiring or deleted')
        return await super().get_team_for_chat(chat_id, save_to_memory=save_to_memory)

    @tool(exclude=True)
    async def delete_chat(self, chat_id: str):
        """Drain this conversation and retire its dependency owners before deletion."""
        if not _identifier(chat_id):
            return {'success': False, 'message': 'Supply a conversation identity'}
        if getattr(self, '_agent_stopping', False):
            return {'success': False, 'message': 'Agent is stopping'}
        if asyncio.current_task() in getattr(self, '_agent_chat_calls', {}).get(chat_id, {}):
            return {'success': False, 'message': 'A running conversation cannot delete itself'}
        # No await before admission closes. A cancellation or RPC timeout never
        # detaches an accepted deletion from its durable recovery task.
        self._retiring_chats.add(chat_id)
        task = self._conversation_deletions.get(chat_id)
        if task is None or task.done() and (task.cancelled() or task.exception() is not None):
            task = self._conversation_deletions[chat_id] = asyncio.create_task(self._delete_conversation(chat_id))
        try:
            return await _drain_call(task)
        except Exception:
            return {'success': False, 'message': 'Conversation retirement is incomplete; retry deletion. Its history is retained.'}

    async def _delete_conversation(self, chat_id):
        await asyncio.to_thread(self.app_data.instances.begin_retirement, chat_id)
        while True:
            pending = set(getattr(self, '_agent_chat_calls', {}).get(chat_id, {}))
            pending.update(task for task, chat in getattr(self, '_agent_continuation_chats', {}).items() if chat == chat_id)
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
        if getattr(self, '_agent_save_error', None) is not None:
            raise RuntimeError('Conversation save needs recovery before deletion')
        receipts = await self.instance_factory.retire(chat_id)
        manager = await asyncio.to_thread(self.memory_manager.mgr_for_chat, chat_id)
        memory = manager.memory_store.get(chat_id)
        if memory is not None:
            # A pending metadata debounce must not recreate the history after
            # unlink. Flush just this chat; sibling conversations keep running.
            await memory.flush(strict=True)
        response = await super().delete_chat(chat_id)
        if not response.get('success'):
            raise RuntimeError('Conversation history deletion needs recovery')
        self.chat_teams.pop(chat_id, None)
        self._team_init_locks.pop(chat_id, None)
        # Lost provider/lease state is distinct from confirmed release. Keep
        # that outcome visible instead of claiming all remote resources stopped.
        response['resource_outcomes'] = {key: value['resources'] for key, value in receipts.items()}
        return response

    async def _stop_auxiliary_services(self):
        # Internal callers can delete without an HTTP host. Do not close the
        # store or allocator while their accepted retirements are in progress.
        await asyncio.gather(*tuple(self._conversation_deletions.values()), return_exceptions=True)
        await super()._stop_auxiliary_services()
