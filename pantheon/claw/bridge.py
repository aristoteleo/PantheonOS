from __future__ import annotations

import asyncio
from typing import Any

from .registry import ClawRouteRegistry, ConversationRoute


class ChatRoomGatewayBridge:
    """Route mobile channel conversations into the active ChatRoom."""

    def __init__(
        self,
        *,
        chatroom: Any,
        registry: ClawRouteRegistry,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._chatroom = chatroom
        self._registry = registry
        self._loop = loop

    async def _dispatch(self, coro):
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is self._loop:
            return await coro

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        if current_loop is None:
            return future.result()
        return await asyncio.wrap_future(future)

    async def _chat_exists(self, chat_id: str) -> bool:
        try:
            result = await self._chatroom.get_chat_messages(chat_id=chat_id, filter_out_images=True)
        except Exception:
            return False
        return bool(result.get("success", False))

    def _chat_name_for_route(self, route: ConversationRoute) -> str:
        base = f"{route.channel}:{route.scope_id}"
        if route.thread_id:
            base = f"{base}:{route.thread_id}"
        if len(base) <= 80:
            return base
        return f"{route.channel}:{route.stable_short_id}"

    @staticmethod
    def _command_parts(text: str) -> tuple[str, str]:
        text = (text or "").strip()
        if not text.startswith("/"):
            return "", text
        parts = text.split(maxsplit=1)
        return parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""

    @staticmethod
    def _resolve_agent_token(agents: list[dict[str, Any]], token: str) -> str | None:
        token = str(token or "").strip()
        if not token:
            return None
        if token.isdigit():
            idx = int(token)
            if 1 <= idx <= len(agents):
                return str(agents[idx - 1].get("name") or "")

        lower = token.lower()
        for agent in agents:
            name = str(agent.get("name") or "")
            if name.lower() == lower:
                return name
        for agent in agents:
            name = str(agent.get("name") or "")
            if name.lower().startswith(lower):
                return name
        return None

    async def _get_entry(self, route: ConversationRoute, *, create: bool = False) -> dict[str, Any] | None:
        entry = self._registry.get(route)
        if entry is None and create:
            entry = await self.ensure_chat(route)
        return entry

    async def _get_chat_id(self, route: ConversationRoute) -> str:
        entry = await self._get_entry(route, create=True)
        if entry is None:
            raise RuntimeError("Failed to initialize routed chat")
        return str(entry["chat_id"])

    async def _get_agents_payload(self, chat_id: str) -> tuple[list[dict[str, Any]], str | None]:
        agents_resp = await self._dispatch(self._chatroom.get_agents(chat_id=chat_id))
        active_resp = await self._dispatch(self._chatroom.get_active_agent(chat_name=chat_id))
        agents = agents_resp.get("agents", []) if isinstance(agents_resp, dict) else []
        active = active_resp.get("agent") if isinstance(active_resp, dict) and active_resp.get("success") else None
        return list(agents), active

    async def _format_help_menu(self) -> str:
        return (
            "PantheonClaw Commands\n"
            "/menu or /help  Show this menu\n"
            "/status         Show routed chat status\n"
            "/agents         Show agents in the current team\n"
            "/agent <sel>    Switch active agent by index or name\n"
            "/team           Show current team and usage\n"
            "/team list      List available team templates\n"
            "/team <sel>     Switch team by id, name, index, or path\n"
            "/model          Show active model info and tags\n"
            "/model list     List providers and sample models\n"
            "/model <spec>   Set model for the active agent\n"
            "/new            Start a fresh routed chat for this channel scope\n"
            "/list           List routed chats for this channel scope\n"
            "/resume <sel>   Resume a previous routed chat by index, id, or name\n"
            "/cancel         Cancel the running analysis\n"
            "/reset          Delete the routed chat and mapping\n"
        )

    async def _format_status(self, route: ConversationRoute) -> str:
        status = await self.get_route_status(route)
        if not status["mapped"]:
            return "Status: idle\nRoute is not mapped yet. Send a message to create the routed chat."

        chat_id = str(status["chat_id"])
        template_resp = await self._dispatch(self._chatroom.get_chat_template(chat_id=chat_id))
        template = template_resp.get("template", {}) if isinstance(template_resp, dict) else {}
        team_name = template.get("name") or template.get("id") or "default"
        active_resp = await self._dispatch(self._chatroom.get_active_agent(chat_name=chat_id))
        active_agent = active_resp.get("agent") if isinstance(active_resp, dict) and active_resp.get("success") else "unknown"
        return (
            f"Status: {'running' if status['running'] else 'idle'}\n"
            f"Chat: {status.get('chat_name') or chat_id} ({chat_id})\n"
            f"Team: {team_name}\n"
            f"Active agent: {active_agent}"
        )

    async def _format_agents(self, route: ConversationRoute) -> str:
        chat_id = await self._get_chat_id(route)
        agents, active_agent = await self._get_agents_payload(chat_id)
        if not agents:
            return "No agents loaded for this routed chat."

        lines = ["Agents"]
        for idx, agent in enumerate(agents, 1):
            name = str(agent.get("name") or f"agent-{idx}")
            model = agent.get("model") or (agent.get("models") or [None])[0] or "unknown"
            marker = "*" if name == active_agent else " "
            lines.append(f"{idx}. {marker} {name} [{model}]")
        lines.append("")
        lines.append("Use /agent <index|name> to switch.")
        return "\n".join(lines)

    async def _handle_agent_command(self, route: ConversationRoute, args: str) -> str:
        chat_id = await self._get_chat_id(route)
        agents, _active_agent = await self._get_agents_payload(chat_id)
        if not args:
            return await self._format_agents(route)

        target = self._resolve_agent_token(agents, args)
        if not target:
            return f"Agent not found: {args}\nUse /agents to see available choices."

        result = await self._dispatch(self._chatroom.set_active_agent(chat_name=chat_id, agent_name=target))
        if not result.get("success"):
            return result.get("message", f"Failed to switch agent to {target}.")
        return f"Active agent: {target}"

    async def _list_team_files(self) -> list[dict[str, Any]]:
        result = await self._dispatch(self._chatroom.list_template_files(file_type="teams"))
        if not result.get("success"):
            raise RuntimeError(result.get("error") or result.get("message") or "Failed to list team templates")
        return list(result.get("files") or [])

    async def _handle_team_command(self, route: ConversationRoute, args: str) -> str:
        args = (args or "").strip()
        if not args:
            chat_id = await self._get_chat_id(route)
            template_resp = await self._dispatch(self._chatroom.get_chat_template(chat_id=chat_id))
            template = template_resp.get("template", {}) if isinstance(template_resp, dict) else {}
            current_name = template.get("name") or template.get("id") or "default"
            return (
                f"Current team: {current_name}\n"
                "Use /team list to see templates.\n"
                "Use /team <index|id|name|path> to switch."
            )

        if args.lower().startswith("list"):
            files = await self._list_team_files()
            if not files:
                return "No team templates found."
            lines = ["Teams"]
            for idx, item in enumerate(files, 1):
                name = item.get("name") or "Unnamed"
                team_id = item.get("id") or "unknown"
                lines.append(f"{idx}. {name} [{team_id}]")
            return "\n".join(lines)

        files = await self._list_team_files()
        selection = args
        file_path = None
        if selection.isdigit():
            idx = int(selection)
            if 1 <= idx <= len(files):
                file_path = files[idx - 1].get("path")
        elif "/" in selection or selection.endswith(".md"):
            file_path = selection
        else:
            lower = selection.lower()
            for item in files:
                item_id = str(item.get("id") or "")
                item_name = str(item.get("name") or "")
                if item_id.lower() == lower or item_name.lower() == lower:
                    file_path = item.get("path")
                    break
            if file_path is None:
                for item in files:
                    item_id = str(item.get("id") or "")
                    item_name = str(item.get("name") or "")
                    if item_id.lower().startswith(lower) or item_name.lower().startswith(lower):
                        file_path = item.get("path")
                        break
            if file_path is None:
                file_path = f"teams/{selection}.md"

        read_res = await self._dispatch(self._chatroom.read_template_file(file_path=file_path, resolve_refs=True))
        if not read_res.get("success"):
            return f"Template not found: {selection}"
        template = read_res.get("content") or {}
        validation = await self._dispatch(self._chatroom.validate_template(template=template))
        if not validation.get("success") or validation.get("compatible") is False:
            message = validation.get("message") or "; ".join(validation.get("validation_errors", []) or [])
            return f"Template not compatible: {message or selection}"

        chat_id = await self._get_chat_id(route)
        setup = await self._dispatch(self._chatroom.setup_team_for_chat(chat_id=chat_id, template_obj=template))
        if not setup.get("success"):
            return setup.get("message", f"Failed to apply team {selection}.")

        tpl_name = template.get("name") or template.get("id") or file_path
        agents_text = await self._format_agents(route)
        return f"Switched team to {tpl_name}\n\n{agents_text}"

    async def _handle_model_command(self, route: ConversationRoute, args: str) -> str:
        chat_id = await self._get_chat_id(route)
        agents, active_agent = await self._get_agents_payload(chat_id)
        if not agents:
            return "No agents loaded for this routed chat."

        models_resp = await self._dispatch(self._chatroom.list_available_models())
        tags = list(models_resp.get("supported_tags") or []) if isinstance(models_resp, dict) else []
        args = (args or "").strip()

        if not args or args in {"help", "?"}:
            current_name = active_agent or str(agents[0].get("name") or "agent")
            current_agent = next((agent for agent in agents if agent.get("name") == current_name), agents[0])
            current_model = current_agent.get("model") or (current_agent.get("models") or [None])[0] or "unknown"
            lines = [
                f"Active agent: {current_name}",
                f"Current model: {current_model}",
            ]
            if tags:
                lines.append(f"Model tags: {', '.join(tags)}")
            lines.append("Use /model <spec> or /model <agent> <spec>.")
            return "\n".join(lines)

        if args.lower() == "list":
            if not isinstance(models_resp, dict) or not models_resp.get("success"):
                return "Unable to list models for the current environment."
            providers = models_resp.get("models_by_provider") or {}
            if not providers:
                return "No providers are available. Configure API keys first."
            lines = ["Available models"]
            for provider, models in providers.items():
                preview = ", ".join(list(models)[:6])
                lines.append(f"- {provider}: {preview}")
            if tags:
                lines.append(f"Tags: {', '.join(tags)}")
            return "\n".join(lines)

        parts = args.split()
        target_agent = active_agent or str(agents[0].get("name") or "")
        model_spec = args
        if len(parts) >= 2:
            matched_agent = self._resolve_agent_token(agents, parts[0])
            if matched_agent:
                target_agent = matched_agent
                model_spec = " ".join(parts[1:]).strip()
        if not model_spec:
            return "Missing model spec. Use /model high or /model <agent> openai/gpt-5.4."

        result = await self._dispatch(
            self._chatroom.set_agent_model(
                chat_id=chat_id,
                agent_name=target_agent,
                model=model_spec,
                validate=True,
            )
        )
        if not result.get("success"):
            return result.get("message", f"Failed to set model for {target_agent}.")
        resolved = ", ".join(result.get("resolved_models", [])[:4])
        suffix = f"\nResolved: {resolved}" if resolved else ""
        return f"Model updated for {target_agent}: {model_spec}{suffix}"

    def _matches_route(self, route: ConversationRoute, project: Any) -> bool:
        if not isinstance(project, dict):
            return False
        if project.get("name") != "pantheon-claw":
            return False
        if project.get("channel") != route.channel:
            return False
        if project.get("scope_type") != route.scope_type:
            return False
        if project.get("scope_id") != route.scope_id:
            return False
        if (project.get("thread_id") or None) != route.thread_id:
            return False
        return True

    async def _list_route_chats(self, route: ConversationRoute) -> list[dict[str, Any]]:
        chats_resp = await self._dispatch(self._chatroom.list_chats(project_name=None))
        chats = list(chats_resp.get("chats") or []) if isinstance(chats_resp, dict) else []
        return [chat for chat in chats if self._matches_route(route, chat.get("project"))]

    async def _handle_new_chat_command(self, route: ConversationRoute) -> str:
        old_entry = self._registry.remove(route)
        created = await self.ensure_chat(route)
        message = f"Started new chat: {created['chat_name']} ({created['chat_id']})"
        if old_entry and old_entry.get("chat_id") != created.get("chat_id"):
            message += f"\nPrevious chat preserved: {old_entry.get('chat_name') or old_entry.get('chat_id')}"
        return message

    async def _handle_list_chats_command(self, route: ConversationRoute) -> str:
        chats = await self._list_route_chats(route)
        if not chats:
            return "No routed chats found for this channel scope."
        current_entry = self._registry.get(route)
        current_chat_id = current_entry.get("chat_id") if current_entry else None
        lines = ["Routed chats"]
        for idx, chat in enumerate(chats, 1):
            marker = "*" if chat.get("id") == current_chat_id else " "
            chat_name = chat.get("name") or chat.get("id") or f"chat-{idx}"
            last_active = chat.get("last_activity_date") or "unknown"
            lines.append(f"{idx}. {marker} {chat_name} [{chat.get('id')}]")
            lines.append(f"   last active: {last_active}")
        lines.append("")
        lines.append("Use /resume <index|id|name> to remap this route.")
        return "\n".join(lines)

    async def _handle_resume_chat_command(self, route: ConversationRoute, args: str) -> str:
        selection = (args or "").strip()
        if not selection:
            return "Usage: /resume <index|id|name>"
        chats = await self._list_route_chats(route)
        if not chats:
            return "No routed chats found for this channel scope."

        target = None
        if selection.isdigit():
            idx = int(selection)
            if 1 <= idx <= len(chats):
                target = chats[idx - 1]
        if target is None:
            lower = selection.lower()
            for chat in chats:
                chat_id = str(chat.get("id") or "")
                chat_name = str(chat.get("name") or "")
                if chat_id == selection or chat_id.startswith(selection) or chat_name.lower() == lower or chat_name.lower().startswith(lower):
                    target = chat
                    break
        if target is None:
            return f"Chat not found: {selection}\nUse /list to see available routed chats."

        entry = self._registry.set(
            route,
            chat_id=str(target.get("id")),
            chat_name=str(target.get("name") or target.get("id") or "routed-chat"),
        )
        return f"Resumed chat: {entry['chat_name']} ({entry['chat_id']})"

    async def handle_control_command(self, route: ConversationRoute, text: str) -> dict[str, Any]:
        cmd, args = self._command_parts(text)
        if not cmd:
            return {"handled": False, "message": None}

        if cmd in {"/help", "/menu"}:
            return {"handled": True, "message": await self._format_help_menu()}
        if cmd == "/status":
            return {"handled": True, "message": await self._format_status(route)}
        if cmd == "/agents":
            return {"handled": True, "message": await self._format_agents(route)}
        if cmd == "/agent":
            return {"handled": True, "message": await self._handle_agent_command(route, args)}
        if cmd == "/team":
            return {"handled": True, "message": await self._handle_team_command(route, args)}
        if cmd in {"/new", "/new-chat"}:
            return {"handled": True, "message": await self._handle_new_chat_command(route), "clear_pending": True}
        if cmd in {"/list", "/chats"}:
            return {"handled": True, "message": await self._handle_list_chats_command(route)}
        if cmd == "/resume":
            return {"handled": True, "message": await self._handle_resume_chat_command(route, args)}
        if cmd == "/cancel":
            result = await self.cancel_route(route)
            return {"handled": True, "message": result.get("message", "cancelled"), "clear_pending": True}
        if cmd == "/reset":
            result = await self.reset_route(route, delete_chat=True)
            return {"handled": True, "message": result.get("message", "reset"), "clear_pending": True}
        if cmd == "/model":
            return {"handled": True, "message": await self._handle_model_command(route, args)}

        return {
            "handled": True,
            "message": f"Unknown command: {cmd}\nUse /menu to see the PantheonClaw command menu.",
        }

    async def ensure_chat(self, route: ConversationRoute) -> dict[str, Any]:
        entry = self._registry.get(route)
        if entry is not None and await self._dispatch(self._chat_exists(entry["chat_id"])):
            self._registry.touch(route)
            return entry

        async def _create():
            created = await self._chatroom.create_chat(
                chat_name=self._chat_name_for_route(route),
                project_name="pantheon-claw",
                workspace_mode="isolated",
            )
            chat_id = created["chat_id"]
            await self._chatroom.set_chat_project(
                chat_id=chat_id,
                project_name="pantheon-claw",
                workspace_mode="isolated",
                channel=route.channel,
                scope_type=route.scope_type,
                scope_id=route.scope_id,
                thread_id=route.thread_id,
                sender_id=route.sender_id,
            )
            return created

        created = await self._dispatch(_create())
        return self._registry.set(
            route,
            chat_id=created["chat_id"],
            chat_name=created["chat_name"],
        )

    async def run_chat(
        self,
        route: ConversationRoute,
        user_text: str,
        *,
        process_chunk=None,
        process_step_message=None,
    ) -> dict[str, Any]:
        entry = await self.ensure_chat(route)
        return await self._dispatch(
            self._chatroom.chat(
                chat_id=entry["chat_id"],
                message=[{"role": "user", "content": user_text}],
                process_chunk=process_chunk,
                process_step_message=process_step_message,
            )
        )

    async def cancel_route(self, route: ConversationRoute) -> dict[str, Any]:
        entry = self._registry.get(route)
        if entry is None:
            return {"success": True, "message": "Route not active"}
        return await self._dispatch(self._chatroom.stop_chat(chat_id=entry["chat_id"]))

    async def reset_route(self, route: ConversationRoute, *, delete_chat: bool = True) -> dict[str, Any]:
        entry = self._registry.remove(route)
        if entry is None:
            return {"success": True, "message": "Route already reset"}
        if delete_chat:
            return await self._dispatch(self._chatroom.delete_chat(chat_id=entry["chat_id"]))
        return {"success": True, "message": "Route mapping cleared"}

    async def get_route_status(self, route: ConversationRoute) -> dict[str, Any]:
        entry = self._registry.get(route)
        if entry is None:
            return {"mapped": False, "running": False}

        async def _running():
            return entry["chat_id"] in self._chatroom.threads

        running = await self._dispatch(_running())
        return {
            "mapped": True,
            "running": bool(running),
            "chat_id": entry["chat_id"],
            "chat_name": entry.get("chat_name"),
        }

    async def list_sessions(self) -> list[dict[str, Any]]:
        route_entries = self._registry.list_entries()

        async def _list_chats():
            return await self._chatroom.list_chats(project_name=None)

        chats_resp = await self._dispatch(_list_chats())
        chats = {
            item["id"]: item
            for item in chats_resp.get("chats", [])
        }
        sessions: list[dict[str, Any]] = []
        for entry in route_entries:
            chat = chats.get(entry["chat_id"], {})
            sessions.append(
                {
                    **entry,
                    "running": entry["chat_id"] in self._chatroom.threads,
                    "project": chat.get("project"),
                    "last_activity_date": chat.get("last_activity_date"),
                }
            )
        return sessions
