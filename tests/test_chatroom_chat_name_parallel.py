from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from pantheon.chatroom.room import ChatRoom


class _FakeMemory:
    def __init__(self):
        self.id = "chat-1"
        self.name = "New Chat"
        self.extra_data = {"project": {}}
        self.updated_metadata = []
        self.dirty = False

    def update_metadata(self, data):
        self.updated_metadata.append(data)
        self.extra_data.update(data)

    def mark_dirty(self):
        self.dirty = True


class _FakeThread:
    def __init__(self, team_getter, memory, message, context_variables=None):
        self.team_getter = team_getter
        self.memory = memory
        self.message = message
        self.context_variables = context_variables
        self.steer_queue = SimpleNamespace(drain=lambda: [])
        self._stop_flag = False
        self.run_calls = 0
        self.response = {"success": True}

    async def run(self):
        self.run_calls += 1

    def add_chunk_hook(self, hook):
        pass

    def add_step_message_hook(self, hook):
        pass


class _FakeTeam:
    def __init__(self):
        self.agents = {}

    def get_active_agent(self, memory):
        return type("Agent", (), {"models": ["anthropic/claude-opus-4-8"]})()


@pytest.mark.asyncio
async def test_chat_starts_title_generation_before_thread_run(monkeypatch):
    chatroom = ChatRoom.__new__(ChatRoom)
    chatroom.check_before_chat = None
    chatroom._enable_auto_chat_name = True
    chatroom._background_tasks = set()
    chatroom.threads = {}
    chatroom.chat_teams = {}
    chatroom._nats_adapter = None
    chatroom.memory_manager = type(
        "MM",
        (),
        {
            "get_memory": lambda self, chat_id, auto_fix=True: _FakeMemory(),
            "save_one": lambda self, memory_id: None,
        },
    )()
    async def fake_project_dir_for_chat(chat_id):
        return None

    chatroom._project_dir_for_chat = fake_project_dir_for_chat
    async def fake_get_team_for_chat(chat_id, save_to_memory=True):
        return _FakeTeam()

    chatroom.get_team_for_chat = fake_get_team_for_chat
    chatroom._setup_bg_auto_notify = lambda chat_id, team: None
    async def fake_attach_hooks(*args, **kwargs):
        return None

    chatroom.attach_hooks = fake_attach_hooks

    thread_instance = _FakeThread
    monkeypatch.setattr("pantheon.chatroom.room.Thread", thread_instance)

    events = []

    class DummyTask:
        def __init__(self, coro):
            self.coro = coro

        def add_done_callback(self, callback):
            callback(self)

        def done(self):
            return True

    def fake_create_task(coro):
        events.append("create_task")
        coro.close()
        return DummyTask(coro)

    async def fake_run(self):
        events.append("thread_run")

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(_FakeThread, "run", fake_run)

    result = await ChatRoom.chat(
        chatroom,
        chat_id="chat-1",
        message=[{"role": "user", "content": "Need a title"}],
    )

    assert result == {"success": True}
    assert events[0] == "create_task"
    assert "thread_run" in events
    assert events.index("create_task") < events.index("thread_run")


@pytest.mark.asyncio
async def test_chat_publishes_rename_before_thread_run_finishes(monkeypatch):
    chatroom = ChatRoom.__new__(ChatRoom)
    chatroom.check_before_chat = None
    chatroom._enable_auto_chat_name = True
    chatroom._background_tasks = set()
    chatroom.threads = {}
    chatroom.chat_teams = {}

    events = []
    rename_published = asyncio.Event()
    allow_thread_finish = asyncio.Event()
    memory = _FakeMemory()

    class FakeNats:
        def create_hooks(self, chat_id):
            async def chunk_hook(chunk):
                pass

            async def step_hook(step):
                pass

            return chunk_hook, step_hook

        async def publish(self, chat_id, subject, payload):
            if subject == "chat_renamed":
                events.append("chat_renamed")
                rename_published.set()

        async def publish_chat_finished(self, chat_id):
            events.append("chat_finished")

    chatroom._nats_adapter = FakeNats()
    chatroom.memory_manager = type(
        "MM",
        (),
        {
            "get_memory": lambda self, chat_id, auto_fix=True: memory,
            "save_one": lambda self, memory_id: events.append("save_one"),
        },
    )()

    async def fake_project_dir_for_chat(chat_id):
        return None

    chatroom._project_dir_for_chat = fake_project_dir_for_chat

    async def fake_get_team_for_chat(chat_id, save_to_memory=True):
        return _FakeTeam()

    chatroom.get_team_for_chat = fake_get_team_for_chat
    chatroom._setup_bg_auto_notify = lambda chat_id, team: None

    async def fake_attach_hooks(*args, **kwargs):
        return None

    chatroom.attach_hooks = fake_attach_hooks

    async def fake_run(self):
        events.append("thread_run_start")
        await allow_thread_finish.wait()
        events.append("thread_run_end")

    monkeypatch.setattr("pantheon.chatroom.room.Thread", _FakeThread)
    monkeypatch.setattr(_FakeThread, "run", fake_run)

    class FakeNameGenerator:
        async def generate_name_candidate(self, messages, preferred_model=None):
            events.append("title_candidate")
            return "Fast Title"

        async def generate_or_update_name(self, memory, messages=None, preferred_model=None):
            return "Fast Title"

        def _is_default_name(self, name):
            return name == "New Chat"

        def _update_metadata(self, memory, message_count):
            memory.update_metadata({"name_generated": True})

    monkeypatch.setattr(
        "pantheon.chatroom.special_agents.get_chat_name_generator",
        lambda: FakeNameGenerator(),
    )

    chat_task = asyncio.create_task(
        ChatRoom.chat(
            chatroom,
            chat_id="chat-1",
            message=[{"role": "user", "content": "Need a title"}],
        )
    )

    await asyncio.wait_for(rename_published.wait(), timeout=0.2)
    assert "thread_run_end" not in events
    assert memory.name == "Fast Title"

    allow_thread_finish.set()
    await chat_task

    assert events.index("chat_renamed") < events.index("thread_run_end")
    assert memory.extra_data["name_generated"] is True


@pytest.mark.asyncio
async def test_background_rename_does_not_overwrite_manual_title(monkeypatch):
    chatroom = ChatRoom.__new__(ChatRoom)
    chatroom._background_tasks = set()
    chatroom._nats_adapter = None
    memory = _FakeMemory()
    saves = []
    chatroom.memory_manager = type(
        "MM",
        (),
        {"save_one": lambda self, memory_id: saves.append(memory_id)},
    )()

    class FakeNameGenerator:
        def _is_default_name(self, name):
            return name == "New Chat"

        def _update_metadata(self, memory, message_count):
            memory.update_metadata({"name_generated": True})

        async def generate_or_update_name(self, memory, messages=None, preferred_model=None):
            return "Generated Title"

    monkeypatch.setattr(
        "pantheon.chatroom.special_agents.get_chat_name_generator",
        lambda: FakeNameGenerator(),
    )

    candidate_task = asyncio.create_task(asyncio.sleep(0, result="Generated Title"))
    memory.name = "Manual Title"

    await ChatRoom._background_rename_chat(
        chatroom,
        memory,
        messages=[{"role": "user", "content": "Need a title"}],
        candidate_task=candidate_task,
    )

    assert memory.name == "Manual Title"
    assert saves == []
