from __future__ import annotations

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
        self._last_chat_call: dict | None = None
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

    async def chat(
        self,
        chat_id: str,
        message: list[dict],
        process_chunk=None,
        process_step_message=None,
    ):
        self._last_chat_call = {
            "chat_id": chat_id,
            "message": message,
        }
        # Simulate a tool result with an image (list format, matching python_interpreter)
        if process_step_message is not None:
            await process_step_message({
                "role": "tool",
                "tool_name": "python",
                "raw_content": {
                    "base64_uri": ["data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="],
                    "stdout": "plot generated",
                },
            })
        if process_chunk is not None:
            await process_chunk({"content": "Here is the result."})
        return {"response": "Here is the result.", "messages": []}


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


class BridgeImageTests(unittest.IsolatedAsyncioTestCase):
    """Tests for image sending and receiving through the bridge."""

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

    # ── _build_message tests ─────────────────────────────────────────────────

    def test_build_message_text_only(self):
        """Text-only messages should use the simple string content format."""
        msgs = ChatRoomGatewayBridge._build_message("hello")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"], "hello")
        self.assertNotIn("_llm_content", msgs[0])

    def test_build_message_with_images(self):
        """Messages with images should use the multimodal content-array format."""
        uri = "data:image/png;base64,abc123"
        msgs = ChatRoomGatewayBridge._build_message("describe this", [uri])
        self.assertEqual(len(msgs), 1)
        msg = msgs[0]

        # Display content should be the text
        self.assertEqual(msg["content"], "describe this")

        # LLM content should be a list with text + image parts
        llm = msg["_llm_content"]
        self.assertIsInstance(llm, list)
        self.assertEqual(len(llm), 2)
        self.assertEqual(llm[0], {"type": "text", "text": "describe this"})
        self.assertEqual(llm[1], {"type": "image_url", "image_url": {"url": uri}})

    def test_build_message_image_only_no_text(self):
        """Image-only messages should use a placeholder for display content."""
        uri = "data:image/jpeg;base64,xyz"
        msgs = ChatRoomGatewayBridge._build_message("", [uri])
        msg = msgs[0]

        self.assertEqual(msg["content"], "[1 image(s)]")
        llm = msg["_llm_content"]
        self.assertEqual(len(llm), 1)
        self.assertEqual(llm[0]["type"], "image_url")

    def test_build_message_multiple_images(self):
        """Multiple images should all appear in _llm_content."""
        uris = [
            "data:image/png;base64,aaa",
            "data:image/jpeg;base64,bbb",
            "data:image/gif;base64,ccc",
        ]
        msgs = ChatRoomGatewayBridge._build_message("compare these", uris)
        llm = msgs[0]["_llm_content"]
        self.assertEqual(len(llm), 4)  # 1 text + 3 images
        image_parts = [p for p in llm if p["type"] == "image_url"]
        self.assertEqual(len(image_parts), 3)
        self.assertEqual(image_parts[0]["image_url"]["url"], uris[0])
        self.assertEqual(image_parts[2]["image_url"]["url"], uris[2])

    # ── run_chat image flow tests ────────────────────────────────────────────

    async def test_run_chat_sends_images_to_chatroom(self):
        """run_chat should pass image_uris through to the chatroom as multimodal content."""
        uri = "data:image/png;base64,testimage"
        await self.bridge.run_chat(
            self.route,
            "analyze this image",
            image_uris=[uri],
        )

        # Verify the chatroom received the multimodal message
        call = self.chatroom._last_chat_call
        self.assertIsNotNone(call, "chatroom.chat() was never called")
        msg = call["message"][0]
        self.assertIn("_llm_content", msg)
        llm = msg["_llm_content"]
        image_parts = [p for p in llm if p["type"] == "image_url"]
        self.assertEqual(len(image_parts), 1)
        self.assertEqual(image_parts[0]["image_url"]["url"], uri)

    async def test_run_chat_text_only_no_llm_content(self):
        """run_chat without images should send plain text (no _llm_content key)."""
        await self.bridge.run_chat(self.route, "just text")

        call = self.chatroom._last_chat_call
        self.assertIsNotNone(call)
        msg = call["message"][0]
        self.assertEqual(msg["content"], "just text")
        self.assertNotIn("_llm_content", msg)

    async def test_run_chat_collects_images_from_step_callback(self):
        """The image step callback should collect base64_uri from tool results."""
        from pantheon.claw.runtime import ChannelRuntime

        runtime = ChannelRuntime(bridge=self.bridge)
        image_buf: list[str] = []
        llm_buf: list[str] = []
        on_step = runtime.make_image_step_callback(llm_buf, image_buf)

        # Simulate a tool result with base64_uri as a string
        await on_step({
            "role": "tool",
            "tool_name": "python",
            "raw_content": {
                "base64_uri": "data:image/png;base64,fakechart",
                "stdout": "done",
            },
        })

        self.assertEqual(len(image_buf), 1)
        self.assertEqual(image_buf[0], "data:image/png;base64,fakechart")

    async def test_run_chat_collects_images_from_step_callback_list_format(self):
        """The image step callback should handle base64_uri as a list (python_interpreter format)."""
        from pantheon.claw.runtime import ChannelRuntime

        runtime = ChannelRuntime(bridge=self.bridge)
        image_buf: list[str] = []
        llm_buf: list[str] = []
        on_step = runtime.make_image_step_callback(llm_buf, image_buf)

        await on_step({
            "role": "tool",
            "tool_name": "python",
            "raw_content": {
                "base64_uri": ["data:image/png;base64,chart1", "data:image/png;base64,chart2"],
                "stdout": "done",
            },
        })

        self.assertEqual(len(image_buf), 2)
        self.assertEqual(image_buf[0], "data:image/png;base64,chart1")
        self.assertEqual(image_buf[1], "data:image/png;base64,chart2")

    async def test_run_chat_step_callback_ignores_non_image_tools(self):
        """Tool results without base64_uri should not add to image_buf."""
        from pantheon.claw.runtime import ChannelRuntime

        runtime = ChannelRuntime(bridge=self.bridge)
        image_buf: list[str] = []
        llm_buf: list[str] = []
        on_step = runtime.make_image_step_callback(llm_buf, image_buf)

        await on_step({
            "role": "tool",
            "tool_name": "search",
            "raw_content": {"stdout": "found 3 results"},
        })

        self.assertEqual(len(image_buf), 0)

    async def test_run_chat_step_callback_ignores_transfers(self):
        """Agent transfers should not add to image_buf."""
        from pantheon.claw.runtime import ChannelRuntime

        runtime = ChannelRuntime(bridge=self.bridge)
        image_buf: list[str] = []
        llm_buf: list[str] = []
        on_step = runtime.make_image_step_callback(llm_buf, image_buf)

        await on_step({"role": "tool", "transfer": True, "content": "Reviewer"})

        self.assertEqual(len(image_buf), 0)

    async def test_end_to_end_image_round_trip(self):
        """Full round-trip: send image → chatroom responds with image → collect it."""
        from pantheon.claw.runtime import ChannelRuntime

        runtime = ChannelRuntime(bridge=self.bridge)
        llm_buf: list[str] = []
        image_buf: list[str] = []

        on_chunk = runtime.make_chunk_callback(llm_buf)
        on_step = runtime.make_image_step_callback(llm_buf, image_buf)

        inbound_uri = "data:image/png;base64,userinput"
        result = await self.bridge.run_chat(
            self.route,
            "what is in this image?",
            image_uris=[inbound_uri],
            process_chunk=on_chunk,
            process_step_message=on_step,
        )

        # Verify inbound: chatroom received the image
        call = self.chatroom._last_chat_call
        llm = call["message"][0]["_llm_content"]
        self.assertTrue(any(
            p.get("image_url", {}).get("url") == inbound_uri
            for p in llm if p["type"] == "image_url"
        ))

        # Verify outbound: step callback collected the server's response image
        self.assertEqual(len(image_buf), 1)
        self.assertEqual(image_buf[0], "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==")

        # Verify text response still works
        self.assertEqual("".join(llm_buf), "Here is the result.")


class RuntimeImageUtilTests(unittest.TestCase):
    """Tests for the image utility functions in runtime.py."""

    def test_bytes_to_data_uri_and_back(self):
        from pantheon.claw.runtime import bytes_to_data_uri, data_uri_to_bytes

        original = b"\x89PNG\r\n\x1a\nfake"
        uri = bytes_to_data_uri(original, "chart.png")

        self.assertTrue(uri.startswith("data:image/png;base64,"))

        recovered, mime = data_uri_to_bytes(uri)
        self.assertEqual(recovered, original)
        self.assertEqual(mime, "image/png")

    def test_bytes_to_data_uri_jpeg(self):
        from pantheon.claw.runtime import bytes_to_data_uri

        uri = bytes_to_data_uri(b"\xff\xd8\xff", "photo.jpg")
        self.assertTrue(uri.startswith("data:image/jpeg;base64,"))

    def test_data_uri_to_bytes_invalid(self):
        from pantheon.claw.runtime import data_uri_to_bytes

        raw, mime = data_uri_to_bytes("")
        self.assertEqual(raw, b"")
        self.assertEqual(mime, "")

        raw2, mime2 = data_uri_to_bytes("not-a-uri")
        self.assertEqual(raw2, b"")

    def test_bytes_to_data_uri_unknown_extension(self):
        from pantheon.claw.runtime import bytes_to_data_uri

        uri = bytes_to_data_uri(b"data", "")
        self.assertTrue(uri.startswith("data:image/png;base64,"))


class MarkdownConverterTests(unittest.TestCase):
    """Tests for the Markdown format converters in runtime.py."""

    def test_md_to_slack_bold(self):
        from pantheon.claw.runtime import md_to_slack
        self.assertEqual(md_to_slack("**hello**"), "*hello*")

    def test_md_to_slack_italic(self):
        from pantheon.claw.runtime import md_to_slack
        self.assertEqual(md_to_slack("*hello*"), "_hello_")

    def test_md_to_slack_bold_and_italic(self):
        from pantheon.claw.runtime import md_to_slack
        result = md_to_slack("**bold** and *italic*")
        self.assertEqual(result, "*bold* and _italic_")

    def test_md_to_slack_strikethrough(self):
        from pantheon.claw.runtime import md_to_slack
        self.assertEqual(md_to_slack("~~deleted~~"), "~deleted~")

    def test_md_to_slack_link(self):
        from pantheon.claw.runtime import md_to_slack
        self.assertEqual(md_to_slack("[text](https://example.com)"), "<https://example.com|text>")

    def test_md_to_slack_header(self):
        from pantheon.claw.runtime import md_to_slack
        self.assertEqual(md_to_slack("## Title"), "*Title*")

    def test_md_to_slack_unordered_list(self):
        from pantheon.claw.runtime import md_to_slack
        self.assertEqual(md_to_slack("- item one\n- item two"), "• item one\n• item two")

    def test_md_to_slack_code_block_preserved(self):
        from pantheon.claw.runtime import md_to_slack
        text = "```python\n**not bold**\n```"
        result = md_to_slack(text)
        self.assertIn("**not bold**", result)

    def test_md_to_slack_inline_code_preserved(self):
        from pantheon.claw.runtime import md_to_slack
        result = md_to_slack("use `**raw**` here")
        self.assertIn("`**raw**`", result)

    # ── md_to_telegram (MarkdownV2) ─────────────────────────────────────────

    def test_md_to_telegram_bold(self):
        from pantheon.claw.runtime import md_to_telegram
        self.assertEqual(md_to_telegram("**hello**"), "*hello*")

    def test_md_to_telegram_italic(self):
        from pantheon.claw.runtime import md_to_telegram
        self.assertEqual(md_to_telegram("*hello*"), "_hello_")

    def test_md_to_telegram_bold_and_italic(self):
        from pantheon.claw.runtime import md_to_telegram
        result = md_to_telegram("**bold** and *italic*")
        self.assertEqual(result, "*bold* and _italic_")

    def test_md_to_telegram_strikethrough(self):
        from pantheon.claw.runtime import md_to_telegram
        self.assertEqual(md_to_telegram("~~deleted~~"), "~deleted~")

    def test_md_to_telegram_escapes_special_chars(self):
        from pantheon.claw.runtime import md_to_telegram
        result = md_to_telegram("hello! (world) test.end")
        self.assertIn("\\!", result)
        self.assertIn("\\(", result)
        self.assertIn("\\.", result)

    def test_md_to_telegram_link(self):
        from pantheon.claw.runtime import md_to_telegram
        result = md_to_telegram("[click](https://example.com)")
        self.assertIn("[click](https://example.com)", result)

    def test_md_to_telegram_header(self):
        from pantheon.claw.runtime import md_to_telegram
        self.assertEqual(md_to_telegram("## Title"), "*Title*")

    def test_md_to_telegram_code_block_not_escaped(self):
        from pantheon.claw.runtime import md_to_telegram
        text = "```\nhello! (world)\n```"
        result = md_to_telegram(text)
        # Content inside code blocks should NOT be escaped
        self.assertIn("hello! (world)", result)

    def test_md_to_telegram_inline_code_not_escaped(self):
        from pantheon.claw.runtime import md_to_telegram
        result = md_to_telegram("use `a.b!c` here")
        self.assertIn("`a.b!c`", result)

    # ── md_to_plain ─────────────────────────────────────────────────────────

    def test_md_to_plain_strips_bold(self):
        from pantheon.claw.runtime import md_to_plain
        self.assertEqual(md_to_plain("**hello**"), "hello")

    def test_md_to_plain_strips_italic(self):
        from pantheon.claw.runtime import md_to_plain
        self.assertEqual(md_to_plain("*hello*"), "hello")

    def test_md_to_plain_strips_strikethrough(self):
        from pantheon.claw.runtime import md_to_plain
        self.assertEqual(md_to_plain("~~deleted~~"), "deleted")

    def test_md_to_plain_link_shows_text_and_url(self):
        from pantheon.claw.runtime import md_to_plain
        result = md_to_plain("[click](https://example.com)")
        self.assertIn("click", result)
        self.assertIn("https://example.com", result)

    def test_md_to_plain_strips_header(self):
        from pantheon.claw.runtime import md_to_plain
        self.assertEqual(md_to_plain("## Title"), "Title")

    def test_md_to_plain_strips_code_fences(self):
        from pantheon.claw.runtime import md_to_plain
        result = md_to_plain("```python\nprint('hi')\n```")
        self.assertIn("print('hi')", result)
        self.assertNotIn("```", result)

    def test_md_to_plain_strips_inline_code(self):
        from pantheon.claw.runtime import md_to_plain
        self.assertEqual(md_to_plain("use `code` here"), "use code here")

    def test_md_to_plain_list(self):
        from pantheon.claw.runtime import md_to_plain
        self.assertEqual(md_to_plain("- item"), "• item")

    # ── Combined / edge cases ───────────────────────────────────────────────

    def test_all_converters_handle_empty_string(self):
        from pantheon.claw.runtime import md_to_slack, md_to_telegram, md_to_plain
        self.assertEqual(md_to_slack(""), "")
        self.assertEqual(md_to_telegram(""), "")
        self.assertEqual(md_to_plain(""), "")

    def test_all_converters_handle_plain_text(self):
        from pantheon.claw.runtime import md_to_slack, md_to_telegram, md_to_plain
        self.assertEqual(md_to_slack("hello world"), "hello world")
        self.assertEqual(md_to_plain("hello world"), "hello world")

    def test_slack_full_message(self):
        from pantheon.claw.runtime import md_to_slack
        md = "## Summary\n**Bold** and *italic*\n- Item 1\n- Item 2\n[link](https://x.com)"
        result = md_to_slack(md)
        self.assertIn("*Summary*", result)
        self.assertIn("*Bold*", result)
        self.assertIn("_italic_", result)
        self.assertIn("• Item 1", result)
        self.assertIn("<https://x.com|link>", result)

    def test_telegram_full_message(self):
        from pantheon.claw.runtime import md_to_telegram
        md = "## Summary\n**Bold** and *italic*\n- Item 1\n~~old~~"
        result = md_to_telegram(md)
        self.assertIn("*Summary*", result)
        self.assertIn("*Bold*", result)
        self.assertIn("_italic_", result)
        self.assertIn("~old~", result)


if __name__ == "__main__":
    unittest.main()
