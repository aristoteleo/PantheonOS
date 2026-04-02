import tempfile
import unittest
import asyncio
from pathlib import Path

from pantheon.claw.bridge import ChatRoomGatewayBridge
from pantheon.claw.config import ClawConfigStore
from pantheon.claw.manager import GatewayChannelManager
from pantheon.claw.registry import ClawRouteRegistry, ConversationRoute


class DummyBridge:
    async def list_sessions(self):
        return []


class ClawConfigStoreTests(unittest.TestCase):
    def test_save_preserves_masked_secrets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            store = ClawConfigStore(path)
            store.save({"slack": {"app_token": "xapp-secret", "bot_token": "xoxb-secret"}})

            saved = store.save({"slack": {"app_token": "***********", "bot_token": ""}})
            masked = store.load_masked()

            self.assertEqual(saved["slack"]["app_token"], "xapp-secret")
            self.assertEqual(saved["slack"]["bot_token"], "xoxb-secret")
            self.assertNotEqual(masked["slack"]["app_token"], "xapp-secret")
            self.assertNotEqual(masked["slack"]["bot_token"], "xoxb-secret")


class ClawRouteRegistryTests(unittest.TestCase):
    def test_route_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ClawRouteRegistry(Path(tmpdir) / "routes.json")
            route = ConversationRoute(
                channel="telegram",
                scope_type="dm",
                scope_id="user-1",
                thread_id="thread-1",
            )
            registry.set(route, chat_id="chat-123", chat_name="route-chat")

            entry = registry.get(route)

            self.assertIsNotNone(entry)
            self.assertEqual(entry["chat_id"], "chat-123")
            self.assertEqual(entry["route_key"], route.route_key())


class GatewayChannelManagerTests(unittest.TestCase):
    def test_list_states_without_optional_channel_dependencies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ClawConfigStore(Path(tmpdir) / "config.json")
            store.save(
                {
                    "telegram": {"token": "token-1"},
                    "wechat": {"token": "wechat-token"},
                    "feishu": {"app_id": "cli_xxx", "app_secret": "secret"},
                    "qq": {"app_id": "app-id", "client_secret": "client-secret"},
                }
            )
            manager = GatewayChannelManager(
                chatroom=object(),
                loop=None,
                config_store=store,
                registry=ClawRouteRegistry(Path(tmpdir) / "routes.json"),
            )

            states = {item["channel"]: item for item in manager.list_states()}

            self.assertEqual(states["telegram"]["status"], "stopped")
            self.assertTrue(states["telegram"]["configured"])
            self.assertTrue(states["telegram"]["supported"])
            self.assertTrue(states["wechat"]["supported"])
            self.assertTrue(states["feishu"]["supported"])
            self.assertTrue(states["qq"]["supported"])
            self.assertTrue(states["imessage"]["supported"])


class DummyChatroom:
    def __init__(self):
        self.threads = set()
        self._chat_id = "chat-1"
        self._active_agent = "Leader"
        self._agents = [
            {"name": "Leader", "model": "openai/gpt-5.4", "models": ["openai/gpt-5.4"]},
            {"name": "Reviewer", "model": "openai/gpt-5.2", "models": ["openai/gpt-5.2"]},
        ]
        self._template = {"id": "default", "name": "Default Team"}
        self._team_files = [
            {"id": "default", "name": "Default Team", "path": "teams/default.md"},
            {"id": "science", "name": "Science Team", "path": "teams/science.md"},
        ]
        self._created_chats = [
            {
                "id": "chat-1",
                "name": "route-chat",
                "last_activity_date": "2026-03-23T00:00:00",
                "project": {
                    "name": "pantheon-claw",
                    "channel": "telegram",
                    "scope_type": "dm",
                    "scope_id": "user-1",
                    "thread_id": None,
                },
            },
            {
                "id": "chat-old",
                "name": "older-chat",
                "last_activity_date": "2026-03-22T00:00:00",
                "project": {
                    "name": "pantheon-claw",
                    "channel": "telegram",
                    "scope_type": "dm",
                    "scope_id": "user-1",
                    "thread_id": None,
                },
            },
        ]

    async def get_chat_messages(self, chat_id: str, filter_out_images: bool = True):
        return {"success": any(chat["id"] == chat_id for chat in self._created_chats)}

    async def create_chat(self, chat_name: str | None = None, project_name: str | None = None, workspace_mode: str = "project"):
        if not any(chat["id"] == self._chat_id for chat in self._created_chats):
            self._created_chats.insert(
                0,
                {
                    "id": self._chat_id,
                    "name": chat_name or "route-chat",
                    "last_activity_date": "2026-03-23T12:00:00",
                    "project": {"name": project_name or "pantheon-claw"},
                },
            )
        return {"chat_id": self._chat_id, "chat_name": chat_name or "route-chat"}

    async def set_chat_project(self, **kwargs):
        chat_id = kwargs.get("chat_id")
        for chat in self._created_chats:
            if chat["id"] == chat_id:
                chat["project"] = {
                    "name": kwargs.get("project_name"),
                    "channel": kwargs.get("channel"),
                    "scope_type": kwargs.get("scope_type"),
                    "scope_id": kwargs.get("scope_id"),
                    "thread_id": kwargs.get("thread_id"),
                }
        return {"success": True}

    async def stop_chat(self, chat_id: str):
        return {"success": True, "message": f"Stopped {chat_id}"}

    async def delete_chat(self, chat_id: str):
        self._created_chats = [chat for chat in self._created_chats if chat["id"] != chat_id]
        return {"success": True, "message": f"Deleted {chat_id}"}

    async def get_agents(self, chat_id: str):
        return {"success": True, "agents": self._agents}

    async def get_active_agent(self, chat_name: str):
        return {"success": True, "agent": self._active_agent}

    async def set_active_agent(self, chat_name: str, agent_name: str):
        self._active_agent = agent_name
        return {"success": True, "message": f"Agent '{agent_name}' set as active"}

    async def get_chat_template(self, chat_id: str):
        return {"success": True, "template": self._template}

    async def list_template_files(self, file_type: str = "teams"):
        return {"success": True, "files": self._team_files}

    async def read_template_file(self, file_path: str, resolve_refs: bool = False):
        for item in self._team_files:
            if item["path"] == file_path:
                return {"success": True, "content": {"id": item["id"], "name": item["name"]}}
        return {"success": False, "message": "not found"}

    async def validate_template(self, template: dict):
        return {"success": True, "compatible": True}

    async def setup_team_for_chat(self, chat_id: str, template_obj: dict, save_to_memory: bool = True):
        self._template = dict(template_obj)
        return {"success": True, "message": "team updated"}

    async def list_available_models(self):
        return {
            "success": True,
            "supported_tags": ["high", "normal", "low"],
            "models_by_provider": {
                "openai": ["openai/gpt-5.4", "openai/gpt-5.2"],
            },
        }

    async def list_chats(self, project_name: str | None = None):
        if project_name is None:
            chats = self._created_chats
        else:
            chats = [chat for chat in self._created_chats if (chat.get("project") or {}).get("name") == project_name]
        return {"success": True, "chats": chats}

    async def set_agent_model(self, chat_id: str, agent_name: str, model: str, validate: bool = True):
        for agent in self._agents:
            if agent["name"] == agent_name:
                agent["model"] = model
                agent["models"] = [model]
                return {"success": True, "agent": agent_name, "model": model, "resolved_models": [model]}
        return {"success": False, "message": "agent not found"}


class ChatRoomGatewayBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.registry = ClawRouteRegistry(Path(self.tmpdir.name) / "routes.json")
        self.chatroom = DummyChatroom()
        self.bridge = ChatRoomGatewayBridge(
            chatroom=self.chatroom,
            registry=self.registry,
            loop=asyncio.get_running_loop(),
        )
        self.route = ConversationRoute(
            channel="telegram",
            scope_type="dm",
            scope_id="user-1",
            sender_id="user-1",
        )

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    async def test_help_menu_includes_cli_commands(self):
        result = await self.bridge.handle_control_command(self.route, "/help")
        self.assertTrue(result["handled"])
        self.assertIn("/model", result["message"])
        self.assertIn("/reset", result["message"])

    async def test_model_command_updates_active_agent_state(self):
        await self.bridge.ensure_chat(self.route)

        model_result = await self.bridge.handle_control_command(self.route, "/model high")
        self.assertIn("Model updated for Leader: high", model_result["message"])

    async def test_agents_and_team_commands_match_repl_style(self):
        await self.bridge.ensure_chat(self.route)

        agents_result = await self.bridge.handle_control_command(self.route, "/agents")
        self.assertIn("Leader", agents_result["message"])
        self.assertIn("Reviewer", agents_result["message"])

        team_result = await self.bridge.handle_control_command(self.route, "/team science")
        self.assertIn("Switched team to Science Team", team_result["message"])

    async def test_resume_command_rebinds_route_to_previous_chat(self):
        await self.bridge.ensure_chat(self.route)

        result = await self.bridge.handle_control_command(self.route, "/resume chat-old")
        self.assertTrue(result["handled"])
        self.assertIn("Resumed chat: older-chat (chat-old)", result["message"])
        entry = self.registry.get(self.route)
        self.assertEqual(entry["chat_id"], "chat-old")

    async def test_reset_command_clears_pending_state(self):
        await self.bridge.ensure_chat(self.route)
        result = await self.bridge.handle_control_command(self.route, "/reset")
        self.assertTrue(result["handled"])
        self.assertTrue(result["clear_pending"])
        self.assertIn("Deleted chat-1", result["message"])


if __name__ == "__main__":
    unittest.main()
