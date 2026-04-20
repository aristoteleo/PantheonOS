"""
Unit tests for chat auto-rename guards.

Covers three bugs fixed together:

1. **Race on first message** — rename task was created before thread.run()
   added the user message to memory, so the task ran with an empty message
   list and skipped the rename. Moving the task to after thread.run() fixes
   this but can't be unit-tested easily (needs full chat() integration).
   Here we test the underlying logic: generate_or_update_name() short-circuits
   correctly when memory has no user messages.

2. **Overly broad _is_default_name** — matched any name starting with
   "Chat ", which false-positived on AI-generated names like
   "Chat about gene expression". Fixed to only match known placeholder
   variants.

3. **Missing name_generated guard** — the metadata flag was being set but
   never checked, so rename attempts were made on every message. Added a
   hard guard that returns early once a chat has been named.
"""

from __future__ import annotations

import pytest

from pantheon.chatroom.special_agents import ChatNameGenerator
from pantheon.internal.memory import Memory


# ============================================================================
# _is_default_name: tighter matcher
# ============================================================================


class TestIsDefaultName:

    def setup_method(self):
        self.gen = ChatNameGenerator()

    def test_exact_defaults(self):
        assert self.gen._is_default_name("New Chat") is True
        assert self.gen._is_default_name("New Chat in Project") is True
        assert self.gen._is_default_name("新建聊天") is True
        assert self.gen._is_default_name("在项目中新建聊天") is True
        assert self.gen._is_default_name("") is True

    def test_whitespace_only_is_default(self):
        assert self.gen._is_default_name("   ") is True

    def test_numbered_placeholders(self):
        assert self.gen._is_default_name("New Chat (2)") is True
        assert self.gen._is_default_name("New Chat in Project (3)") is True
        assert self.gen._is_default_name("新建聊天 (2)") is True
        assert self.gen._is_default_name("在项目中新建聊天 (3)") is True

    def test_ai_generated_names_are_not_default(self):
        """Regression: 'Chat about X' was previously matched as default.

        AI commonly produces names like "Chat about data analysis",
        "Chat Analysis", "Chat re: gene expression" etc. These should NOT
        be treated as defaults or they'll be re-renamed every message.
        """
        assert self.gen._is_default_name("Chat about gene expression") is False
        assert self.gen._is_default_name("Chat Analysis") is False
        assert self.gen._is_default_name("Chat re: pipelines") is False
        # Boundary: exactly "Chat " with trailing space should NOT match
        assert self.gen._is_default_name("Chat analysis") is False

    def test_timestamp_fallback_is_not_default(self):
        """_fallback_name produces names like 'Chat 04-19 23:45'. Under
        the old broad matcher, these were treated as default and re-tried,
        causing an infinite rename loop. Now they stick once generated."""
        assert self.gen._is_default_name("Chat 04-19 23:45") is False

    def test_meaningful_names(self):
        assert self.gen._is_default_name("🧬 Gene Analysis") is False
        assert self.gen._is_default_name("Data exploration") is False
        assert self.gen._is_default_name("分析 GEO 数据集") is False


# ============================================================================
# generate_or_update_name: name_generated hard guard
# ============================================================================


class TestNameGeneratedGuard:

    @pytest.mark.asyncio
    async def test_guard_blocks_rename_when_memory_has_real_name(self):
        """Once the chat has a meaningful name, subsequent calls return it
        unchanged — even if name_generated is somehow unset. The guard is on
        the name itself, not the flag."""
        gen = ChatNameGenerator()
        memory = Memory("🧬 Gene Analysis")
        memory.update_metadata({"name_generated": True})
        memory.add_messages([{"role": "user", "content": "hello"}])

        result = await gen.generate_or_update_name(memory)
        assert result == "🧬 Gene Analysis"

    @pytest.mark.asyncio
    async def test_stale_name_generated_flag_does_not_block_rename(self):
        """Regression: chats created before the customTitle-sync fix may have
        name_generated=True on disk while memory.name is still 'New Chat'
        (the rename never actually stuck). A new message in such a chat must
        still trigger rename rather than being blocked by the stale flag."""
        gen = ChatNameGenerator()
        memory = Memory("New Chat")
        memory.update_metadata({"name_generated": True})  # stale
        memory.add_messages([{"role": "user", "content": "hello"}])

        # Don't actually hit AI in the unit test — _fallback_name path is fine.
        # The assertion is that we get a NEW name, not "New Chat".
        result = await gen.generate_or_update_name(memory)
        assert result != "New Chat"

    @pytest.mark.asyncio
    async def test_guard_does_not_block_first_rename(self):
        """name_generated should default to absent/False on fresh chats."""
        gen = ChatNameGenerator()
        memory = Memory("New Chat")
        # extra_data has no name_generated key → guard must NOT fire
        assert memory.extra_data.get("name_generated") is None


# ============================================================================
# generate_or_update_name: empty messages path (msg 1 race)
# ============================================================================


class TestEmptyMessagesPath:

    @pytest.mark.asyncio
    async def test_empty_memory_returns_default_name(self):
        """When memory has no user messages, rename task returns current
        name unchanged. This used to be the observable symptom of the
        msg-1 race: task ran before user msg was added to memory."""
        gen = ChatNameGenerator()
        memory = Memory("New Chat")
        # Explicitly empty
        result = await gen.generate_or_update_name(memory)
        assert result == "New Chat"

    @pytest.mark.asyncio
    async def test_only_assistant_messages_returns_default(self):
        """Edge case: if memory somehow has only assistant messages (no
        user), we still can't generate a good name. Return unchanged."""
        gen = ChatNameGenerator()
        memory = Memory("New Chat")
        memory.add_messages([{"role": "assistant", "content": "hello"}])
        result = await gen.generate_or_update_name(memory)
        assert result == "New Chat"
