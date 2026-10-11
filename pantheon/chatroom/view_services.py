"""Explicit, project-scoped dependencies for the Agent App's human interface.

These clients are separate from per-Agent execution sessions and plugin clients.
They use ordinary dependency grants; no ambient discovery or filesystem access.
"""
import asyncio

from pantheon.factory.bindings import _bindings_from_spec


class AgentViewServices:
    def __init__(self, configuration, projects, spec, tls_context=None, *, profiles=None, allocate=None):
        if not isinstance(spec, dict):
            raise ValueError('Invalid Agent view dependencies')
        known = {project['id'] for project in projects.list_projects()}
        if not spec.keys() <= known:
            raise ValueError('View dependency project is outside the App snapshot')
        self._projects = projects
        self._bindings = {}
        self._closed = False
        for project_id, entry in spec.items():
            if not isinstance(entry, dict) or set(entry) != {'toolsets'}:
                raise ValueError('View dependencies require explicit service bindings')
            self._bindings[project_id] = _bindings_from_spec(configuration, entry, tls_context, profiles=profiles,
                                                              allocate=allocate)

    async def call(self, workspace_path, service, method, args):
        if self._closed:
            raise RuntimeError('Agent view dependencies are closed')
        if not isinstance(workspace_path, str) or not workspace_path:
            raise ValueError('Select an attached App workspace')
        project = self._projects.get_project(workspace_path)
        bindings = self._bindings.get(project.id) if project else None
        provider = bindings.toolsets.get(service) if bindings and isinstance(service, str) else None
        if provider is None:
            raise ValueError('Service is not bound to this App workspace')
        # The grant, not workspace_path or a caller-supplied session, determines
        # the provider's root and authority. Schemas admit only caller arguments.
        return await provider.call_tool(method, args)

    async def close(self):
        self._closed = True
        # Projects may share one allocated provider; close each once.
        providers = list({id(provider): provider for bindings in self._bindings.values()
                          for provider in bindings.toolsets.values()}.values())
        results = await asyncio.gather(*(provider.shutdown() for provider in providers),
                                       return_exceptions=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            from pantheon.apps.host_lifecycle import AppShutdownError
            raise AppShutdownError(errors) from errors[0]
