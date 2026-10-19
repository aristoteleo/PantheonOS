"""The Agent's PantheonOS interface to Apps (docs/pantheonos-app-interface.md).

Seven tools list, describe, call, start, stop, manage and develop every App
the Agent may use: the deployment's Apps (its toolsets), the owner's library
Apps (through the Desktop) and MCP servers. A template's ``apps`` filter
decides which of them one agent may use; it grants nothing.
"""
from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from .agent_core_tools import BUILTIN_PROVIDERS, DEPLOYMENT_DESCRIPTIONS, DIRECT_APP_TOOLS
from pantheonos.apps.filter import _NAME, AppFilter, app_name  # noqa: F401 (their home is the platform)

if TYPE_CHECKING:
    from .agent import Agent


class PantheonOS:
    """The seven pantheonos_* tools of one Agent."""

    def __init__(self, agent: "Agent"):
        self.agent = agent

    # ---- what the Agent can reach ----

    def _allowed(self, name: str) -> bool:
        flt = getattr(self.agent, 'app_filter', None)
        return name not in BUILTIN_PROVIDERS and (flt is None or flt.allows(name))

    async def _apps(self, *, refresh: bool = False) -> dict:
        """The Agent's Apps by name: its providers that are Apps and pass its filter."""
        await self.agent._load_app_directory(force=refresh)
        return {name: provider for name, provider in self.agent.providers.items() if self._allowed(name)}

    async def _app(self, app: str):
        apps = await self._apps()
        if app not in apps:
            apps = await self._apps(refresh=True)  # An App installed during the conversation.
        if app not in apps:
            raise LookupError(app)
        return apps[app]

    def _unknown(self, app: str, apps) -> dict:
        return {"error": f"Unknown App '{app}'.", "apps": sorted(apps)}

    @staticmethod
    def _source(provider) -> str:
        from .agent_app_directory import LibraryAppProvider
        from .providers import MCPProvider
        if isinstance(provider, LibraryAppProvider):
            return "library"
        if isinstance(provider, MCPProvider):
            return "mcp"
        return "deployment"

    @staticmethod
    def _deployment_node(provider) -> str | None:
        """The node a deployment App runs on, once its provider is bound
        (never binding it just to say where)."""
        bound = getattr(provider, '_bound', None)
        return getattr(bound, 'node_id', None) or getattr(provider, 'node_id', None)

    async def _node_names(self) -> dict:
        from .agent_nodes import list_nodes
        try:
            nodes = await asyncio.wait_for(list_nodes(self.agent), 10)
        except Exception:
            return {}
        return {n["node_id"]: n.get("name") or n["node_id"] for n in nodes}

    def _directory(self):
        directory = getattr(self.agent, 'app_directory', None)
        if directory is None:
            raise RuntimeError("The owner's library Apps are not reachable in this deployment")
        return directory

    # ---- the tools ----

    async def pantheonos_list_apps(self, query: str = "") -> dict:
        apps = await self._apps(refresh=True)
        live = {}
        if any(self._source(p) == "library" for p in apps.values()):
            try:
                live = await self._directory().state()
            except Exception as exc:
                live = {"_error": str(exc)}
        out, names = [], None
        for name, provider in sorted(apps.items()):
            source = self._source(provider)
            if source == "library":
                entry = live.get(provider.app_id) or provider.entry
                item = {"app": name, "source": source, "description": provider.description,
                        "tools": [f["name"] for f in entry.get("tools", [])],
                        "actions": [f["name"] for f in entry.get("actions", [])],
                        "window": entry.get("window", False), "policy": entry.get("policy", "on_demand")}
                if "instances" in entry:
                    item["running"] = [{"node": i.get("node") or i.get("node_id"), "state": i.get("state"),
                                        "version": i.get("version")} for i in entry["instances"]]
                    item["default_node"] = entry.get("default_node")
            else:
                tools = [t.name for t in await provider.list_tools()]
                item = {"app": name, "source": source, "description": DEPLOYMENT_DESCRIPTIONS.get(name, ""),
                        "tools": tools, "running": "always (the user manages it in Fleet)"}
                node = self._deployment_node(provider)
                if node:
                    if names is None:
                        names = await self._node_names()
                    item["node"] = names.get(node, node)
            text = f"{name} {item['description']}".lower()
            if not query or query.lower() in text:
                out.append(item)
        result = {"apps": out}
        if "_error" in live:
            result["note"] = f"Running state unavailable: {live['_error']}"
        return result

    async def pantheonos_describe_app(self, app: str) -> dict:
        try:
            provider = await self._app(app)
        except LookupError:
            return self._unknown(app, await self._apps())
        source = self._source(provider)
        tools = []
        for info in await provider.list_tools():
            if f"{app}__{info.name}" not in DIRECT_APP_TOOLS:
                tools.append({**info.inputSchema, "name": info.name})
        if source != "library":
            return {"app": app, "source": source, "description": DEPLOYMENT_DESCRIPTIONS.get(app, ""),
                    "tools": tools}
        reply = await self._directory().operation('agent_app_describe', {'app_id': provider.app_id})
        if isinstance(reply, dict) and reply.get('success'):
            details = {k: reply.get(k) for k in ('window', 'backend', 'policy', 'version', 'branch', 'branches',
                                                  'default_node', 'instances') if reply.get(k) is not None}
        else:
            details = {"note": (reply or {}).get('error', 'details unavailable') if isinstance(reply, dict) else ''}
        actions = {f["name"] for f in provider.entry.get("actions", [])}
        return {"app": app, "source": source, "description": provider.entry.get("description", ""),
                "tools": [t for t in tools if t["name"] not in actions],
                "actions": [t for t in tools if t["name"] in actions], **details}

    async def pantheonos_call_app(self, app: str, tool: str, args: dict | None = None) -> dict:
        # Reached only when the call could not be resolved to a real tool
        # (Agent._expand_app_tool_calls runs the ones that can).
        try:
            provider = await self._app(app)
        except LookupError:
            return self._unknown(app, await self._apps())
        if tool in {t.name for t in await provider.list_tools()}:
            return await self.agent.call_tool(f"{app}__{tool}", args or {})
        index = await self.agent._app_tool_index()
        return {"error": f"App '{app}' has no tool '{tool}'.", "tools": index.get(app, [])}

    async def pantheonos_start_app(self, app: str, node: str = "", version: str = "", window: bool = False,
                                   job: dict | None = None) -> dict:
        try:
            provider = await self._app(app)
        except LookupError:
            return self._unknown(app, await self._apps())
        if self._source(provider) != "library":
            return {"app": app, "running": True,
                    "note": "A deployment App is always running; the user manages it in Fleet."}
        directory = self._directory()
        reply = await directory.operation('agent_app_start', {
            'app_id': provider.app_id, 'node': node, 'version': version, 'window': bool(window),
            'job': job, 'holder': directory.holder})
        if isinstance(reply, dict) and reply.get('success') and job is None and (node or version):
            # Later calls in this conversation go to this instance.
            provider.target = {k: v for k, v in (('node', node), ('version', version)) if v}
        return reply

    async def pantheonos_stop_app(self, app: str, node: str = "") -> dict:
        try:
            provider = await self._app(app)
        except LookupError:
            return self._unknown(app, await self._apps())
        if self._source(provider) != "library":
            return {"error": f"'{app}' is a deployment App: the user starts and stops it in Fleet."}
        directory = self._directory()
        reply = await directory.operation('agent_app_stop', {
            'app_id': provider.app_id, 'node': node, 'holder': directory.holder})
        if isinstance(reply, dict) and reply.get('success') and (not node or provider.target.get('node') == node):
            provider.target = {}
        return reply

    async def _library_operation(self, method: str, action: str, app: str, args) -> dict:
        app_id = ""
        if app:
            apps = await self._apps()
            provider = apps.get(app)
            if provider is not None and self._source(provider) != "library":
                return {"error": f"'{app}' is a deployment App; it is managed with its deployment in Fleet."}
            if provider is not None:
                app_id = provider.app_id
            else:
                # Not (yet) installed, or not offered: name it by its id. A
                # name the filter excludes is refused either way.
                if not self._allowed(app_name(app)):
                    return {"error": f"'{app}' is not among the Apps this agent may use."}
                app_id = app
        reply = await self._directory().operation(method, {'action': action, 'app_id': app_id, 'args': args or {}})
        if action in ('install', 'uninstall', 'restore', 'create', 'fork'):
            await self.agent._load_app_directory(force=True)
        return reply

    async def pantheonos_manage_app(self, action: str, app: str = "", args: dict | None = None) -> dict:
        return await self._library_operation('agent_app_manage', action, app, args)

    async def pantheonos_develop_app(self, action: str, app: str = "", args: dict | None = None) -> dict:
        return await self._library_operation('agent_app_develop', action, app, args)
