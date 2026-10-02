"""App discovery and invocation without an Agent or conversation dependency."""

import os
from pantheon.toolset import tool
from pantheon.utils.log import logger


async def invoke_app_tool(
    method_name: str,
    args: dict | None = None,
    toolset_name: str | None = None,
    *, workdir: str | None = None,
) -> dict:
    """Invoke a tool on a toolset's App instance.

    The caller supplies a workspace explicitly; this routing layer has no
    conversation, Agent, or memory state.

    Args:
        method_name: The tool to call.
        args: Arguments to pass to the tool.
        toolset_name: The toolset (catalog service name) to call.

    Returns:
        The result from the tool call.
    """
    try:
        logger.debug(
            f"platform app call: method_name={method_name}, toolset_name={toolset_name}"
        )

        if not toolset_name:
            # Pre-bundling frontends call the packaged-app plane bare —
            # these two names have exactly one home, so route them there
            # instead of stranding every viewer window on older shells.
            if method_name in ("app_call", "app_registry"):
                toolset_name = "desktop"
            else:
                return {
                    "success": False,
                    "error": "toolset_name is required (the endpoint that "
                             "used to answer bare calls is retired)",
                }

        from pantheon.apps.proxy import ToolsetProxy
        from pantheon.apps.resolver import get_shared_resolver

        args = dict(args or {})
        target_node = args.pop('_node_id', None)
        if target_node is not None and not isinstance(target_node, str):
            return {'success': False, 'error': 'node_id must be a string'}
        if target_node:
            if toolset_name not in ('file_manager', 'file_transfer', 'pty'):
                return {'success': False, 'error': 'Explicit node routing supports Files and PTY services'}
            resolver = get_shared_resolver()
            if resolver is None:
                return {'success': False, 'error': 'Fleet is not connected'}
            if toolset_name == 'file_transfer':
                args = {'method': method_name, 'args': args}
                method_name = 'file_transfer'
            service = 'pty' if toolset_name == 'pty' else 'file_manager'
            sid = await resolver.ensure_instance(service, node_id=target_node)
            try:
                result = await ToolsetProxy.from_toolset(sid).invoke(method_name, args)
            except Exception as e:
                if not ToolsetProxy._has_no_responders(e):
                    raise
                # No receiver means the operation never ran. A rejoined
                # Fleet runner may have lost Files as well as PTY; discard
                # its old service/node snapshot and restore only this node.
                # Never replay a timeout or an application-level failure.
                resolver.invalidate(service, node_id=target_node)
                sid = await resolver.ensure_instance(service, node_id=target_node)
                result = await ToolsetProxy.from_toolset(sid).invoke(method_name, args)
            if service == 'pty':
                result = {**result, 'node_id': target_node}
            return result

        resolver = get_shared_resolver()
        if resolver is None:
            return {"success": False, "error": "App resolver not wired"}
        if not resolver.resolves(toolset_name):
            # A packaged (user-scope) app is not a bus service: its
            # backend runs credential-less under the desktop supervisor.
            # Say so, or this reads as "not installed" to someone who
            # can see the app on their desktop.
            hint = ""
            try:
                from pantheon.apps.registry import packaged_apps, default_scope_roots
                from pantheon.settings import get_settings
                from pathlib import Path

                ws = Path(get_settings().workspace)
                ids = {a.manifest.id for a in packaged_apps(default_scope_roots(ws))}
                if toolset_name in ids or toolset_name.replace("_", "-") in ids:
                    hint = (" — it is installed as a packaged app; its backend "
                            "is reached via the desktop toolset's app_call, "
                            "not as a bus service")
            except Exception:
                pass
            return {
                "success": False,
                "error": f"'{toolset_name}' is not a known App in the catalog{hint}",
            }

        proj_dir = workdir

        async def _ensure() -> str:
            if proj_dir:
                return await resolver.ensure_instance(
                    toolset_name,
                    scope=resolver.project_scope(proj_dir),
                    workdir=proj_dir,
                )
            # No chat/project context (desktop UI calls, global tools):
            # land on the DEFAULT workspace's instance — the very one
            # prestart warmed — never a parallel app-scoped twin. Two
            # desktops for one user split every piece of per-display
            # state (and the second Chromium dies on the first one's
            # profile lock).
            cwd = os.getcwd()
            return await resolver.ensure_instance(
                toolset_name,
                scope=resolver.project_scope(cwd),
                workdir=cwd,
            )

        from nats.errors import NoRespondersError

        sid = await _ensure()
        proxy = ToolsetProxy.from_toolset(sid)
        try:
            return await proxy.invoke(method_name, args or {})
        except NoRespondersError:
            if proxy.has_instance_binding:
                # The pooled proxy already tried its one exact recovery.
                raise
            # The cached instance is gone — its process died, or its
            # runner did. Forget it, ensure a fresh one (the runner
            # restarts or recreates it), and dial once more.
            logger.warning(
                f"[apps] instance {sid[:12]}… of '{toolset_name}' answers "
                f"nobody — re-ensuring")
            resolver.invalidate(toolset_name, scope=resolver.project_scope(proj_dir or os.getcwd()))
            sid = await _ensure()
            return await ToolsetProxy.from_toolset(sid).invoke(
                method_name, args or {}
            )

    except Exception as e:
        logger.error(
            f"Error calling toolset method {method_name} on {toolset_name}: {e}"
        )
        return {"success": False, "error": str(e)}


class AppServicesAPI:
    @tool
    async def get_toolsets(self) -> dict:
        """Get all available toolsets (the App catalog + live instances).

        Returns:
            - success: Whether the operation was successful.
            - services: A list of available toolset services (one per catalog
              App: name, app_id, status "running"|"available", service_id when
              an instance is already up).
        """
        try:
            from pantheon.apps.registry import by_service_type
            from pantheon.apps.resolver import get_shared_resolver

            resolver = get_shared_resolver()
            started = dict(getattr(resolver, "_started", {})) if resolver else {}
            services = []
            for service_type, app in sorted(by_service_type().items()):
                sid = started.get((service_type, "app"))
                services.append({
                    "name": service_type,
                    "app_id": app.manifest.id,
                    "description": app.manifest.description,
                    "status": "running" if sid else "available",
                    "service_id": sid,
                })
            return {"success": True, "services": services}
        except Exception as e:
            logger.error(f"Error getting toolsets: {e}")
            return {"success": False, "error": str(e)}


    @tool(exclude=True)
    async def call_app_service(self, method_name: str, args: dict | None = None,
                               toolset_name: str | None = None,
                               workdir: str | None = None) -> dict:
        """Call an App in the authenticated user's workspace or explicit node.

        workdir is a workspace path on the target service node, not a chat id.
        This uses the existing per-user resolver and Fleet authorization.
        """
        return await invoke_app_tool(method_name, args, toolset_name,
                                     workdir=workdir or getattr(self, 'workspace_path', None))
