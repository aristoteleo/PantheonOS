import asyncio
import json
import random
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field

from pantheon.agent import Agent, AgentRunContext, AgentTransfer, _RUN_CONTEXT, _call_agent
from pantheon.internal.memory import Memory
from pantheon.utils.tool_pairing import INCOMPLETE_TOOL_RESULT_PLACEHOLDER
from pantheon.utils.llm_providers import ProviderConfig, ProviderType
from pantheon.utils.vision import vision_input

HERE = Path(__file__).parent


def test_sanitize_messages_repairs_tool_pairing_and_drops_orphans():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_kept",
                    "type": "function",
                    "function": {"name": "tool_a", "arguments": "{}"},
                },
                {
                    "id": "call_missing",
                    "type": "function",
                    "function": {"name": "tool_b", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_kept", "content": "ok"},
        {"role": "tool", "tool_call_id": "orphan", "content": "bad"},
    ]

    sanitized = Agent._sanitize_messages(messages)

    assert len(sanitized) == 4
    assert [tc["id"] for tc in sanitized[1]["tool_calls"]] == [
        "call_kept",
        "call_missing",
    ]
    assert sanitized[2]["tool_call_id"] == "call_kept"
    assert sanitized[3]["tool_call_id"] == "call_missing"
    assert sanitized[3]["content"] == INCOMPLETE_TOOL_RESULT_PLACEHOLDER
    assert sanitized[3]["_recovered"] is True


async def test_acompletion_resanitizes_messages_after_token_optimization(monkeypatch):
    agent = Agent(name="test", instructions="You are helpful.")
    captured = {}

    async def fake_build_llm_view_async(*args, **kwargs):
        return [
            {"role": "system", "content": "You are helpful."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_missing",
                        "type": "function",
                        "function": {"name": "shell", "arguments": "{}"},
                    }
                ],
            },
            {"role": "user", "content": "hello"},
        ]

    async def fake_call_llm_provider(**kwargs):
        captured["messages"] = kwargs["messages"]
        return {"role": "assistant", "content": "ok"}

    monkeypatch.setattr(
        "pantheon.utils.token_optimization.build_llm_view_async",
        fake_build_llm_view_async,
    )
    monkeypatch.setattr(
        "pantheon.utils.token_optimization.supports_explicit_cache_control",
        lambda model: False,
    )
    monkeypatch.setattr(
        "pantheon.agent.call_llm_provider",
        fake_call_llm_provider,
    )
    monkeypatch.setattr(
        "pantheon.agent.detect_provider",
        lambda model, relaxed_schema: ProviderConfig(
            provider_type=ProviderType.OPENAI,
            model_name=model,
            base_url="https://example.invalid",
            api_key="test-key",
            relaxed_schema=relaxed_schema,
        ),
    )

    await agent._acompletion(
        messages=[{"role": "user", "content": "hello"}],
        model="codex/gpt-5.4-mini",
        tool_use=False,
    )

    assert captured["messages"][0] == {"role": "system", "content": "You are helpful."}
    assert captured["messages"][1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_missing",
                "type": "function",
                "function": {"name": "shell", "arguments": "{}"},
            }
        ],
    }
    assert captured["messages"][2]["role"] == "tool"
    assert captured["messages"][2]["tool_call_id"] == "call_missing"
    assert captured["messages"][2]["tool_name"] == "shell"


def test_blank_model_is_treated_as_implicit_default():
    agent = Agent(name="implicit", instructions="x", model="")

    assert agent._model_was_explicit is False
    assert agent.models


async def test_call_agent_prefers_current_provider_for_quality_tag(monkeypatch):
    captured = {}

    async def fake_run(self, messages, **kwargs):
        captured["models"] = list(self.models)
        return type(
            "FakeResult",
            (),
            {
                "content": "ok",
                "details": type("FakeDetails", (), {"messages": []})(),
            },
        )()

    monkeypatch.setattr(Agent, "run", fake_run)

    token = _RUN_CONTEXT.set(
        AgentRunContext(
            agent=Agent(name="parent", instructions="x", model="codex/gpt-5.4-mini"),
            memory=None,
            execution_context_id="ctx-1",
            current_model="codex/gpt-5.4-mini",
        )
    )
    try:
        result = await _call_agent(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="x",
            model="low",
            memory=None,
        )
    finally:
        _RUN_CONTEXT.reset(token)

    assert result["success"] is True
    assert captured["models"][0].startswith("codex/")


async def test_context_injection_sampler_prefers_parent_provider_without_run_model(
    monkeypatch,
):
    captured = {}

    async def fake_call_agent(*, messages, system_prompt, model, memory):
        captured["model"] = model
        return {"success": True, "response": "[]", "_metadata": {}}

    class DummyInjector:
        async def inject(self, input_text, injector_context):
            await injector_context["_call_agent"](
                messages=[{"role": "user", "content": "pick"}],
                system_prompt="x",
                model="low",
                use_memory=False,
            )
            return ""

    monkeypatch.setattr("pantheon.agent._call_agent", fake_call_agent)

    agent = Agent(name="parent", instructions="x", model="codex/gpt-5.4-mini")
    agent.context_injectors = [DummyInjector()]

    await agent._inject_context_to_messages(
        messages=[{"role": "user", "content": "hello"}],
        context_variables={},
    )

    assert isinstance(captured["model"], list)
    assert captured["model"][0].startswith("codex/")


async def test_call_agent_inherits_current_run_model_when_not_specified(monkeypatch):
    captured = {}

    class DummyResult:
        content = "ok"
        details = None

    async def fake_run(self, *args, **kwargs):
        captured["models"] = list(self.models)
        return DummyResult()

    monkeypatch.setattr(Agent, "run", fake_run)

    run_context = AgentRunContext(agent=Agent(name="parent", instructions="parent", model="gemini/gemini-3-flash-preview"), memory=None)
    run_context.current_model = "gemini/gemini-3-flash-preview"
    token = _RUN_CONTEXT.set(run_context)
    try:
        result = await _call_agent(
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="You are helpful.",
            model=None,
        )
    finally:
        _RUN_CONTEXT.reset(token)

    assert result["success"] is True


async def test_prepare_execution_context_uses_resume_recovery(monkeypatch):
    agent = Agent(name="resume-parent", instructions="x")
    memory = Memory("resume-memory")
    memory.add_messages([{"role": "user", "content": "old"}])

    captured = {}

    def fake_loadConversationForResume(source, sourceJsonlFile=None, execution_context_id=None):
        captured["source"] = source
        captured["execution_context_id"] = execution_context_id
        return {
            "messages": [{"role": "assistant", "content": "restored"}],
            "contextCollapseCommits": [],
            "contextCollapseSnapshot": None,
        }

    def fake_restoreSessionStateFromLog(result, setAppState, memory=None):
        captured["restored"] = result["messages"][0]["content"]
        captured["memory_id"] = memory.id if memory else None

    monkeypatch.setattr(
        "pantheon.repl.conversationRecovery.loadConversationForResume",
        fake_loadConversationForResume,
    )
    monkeypatch.setattr(
        "pantheon.repl.sessionRestore.restoreSessionStateFromLog",
        fake_restoreSessionStateFromLog,
    )

    ctx = await agent._prepare_execution_context(
        "continue",
        memory=memory,
        use_memory=True,
    )

    assert captured["source"] is memory
    assert captured["execution_context_id"] is None
    assert captured["restored"] == "restored"
    assert captured["memory_id"] == memory.id
    assert ctx.conversation_history[0]["content"] == "restored"
    assert ctx.conversation_history[-1]["role"] == "user"


async def test_stream():
    agent = Agent(
        name="test",
        instructions="You are a sci-fi fan, you can answer any sci-fi related questions. Just give me a number without any other words and symbols.",
    )
    msgs = [{"role": "user", "content": "What is the meaning of life?"}]
    resp = await agent._run_stream(
        msgs,
        process_chunk=lambda chunk: print(chunk.get("content", ""), end="", flush=True),
    )
    print("\n", resp)
    assert len(resp.messages) == 1


async def test_tool_use():
    instructions = """
You are a weather agent, you can use the weather API to get the weather of a city.

"""
    agent = Agent(
        name="test",
        instructions=instructions,
    )

    @agent.tool
    def get_weather(city: str, unit: str = "celsius"):
        """Get the weather of a city."""
        return {"weather": "sunny", "temperature": 20}

    msgs = [
        {
            "role": "user",
            "content": "What is the weather in Palo Alto and Redwood City?",
        }
    ]
    resp = await agent._run_stream(msgs)
    print(resp)
    call_id = resp.messages[0]["tool_calls"][0]["id"]
    tool_msg = next(m for m in resp.messages if m.get("role") == "tool" and m.get("tool_call_id") == call_id)
    assert tool_msg["raw_content"]["weather"] == "sunny"


async def test_structured_output():
    agent = Agent(
        name="test",
        instructions="You are a sci-fi fan, you can answer any sci-fi related questions.",
    )

    class SciFiBook(BaseModel):
        title: str
        author: str
        year: int

    class SciFiBookList(BaseModel):
        books: List[SciFiBook]

    def assert_chunk(chunk):
        assert isinstance(chunk, dict)

    resp = await agent.run(
        [{"role": "user", "content": "Recommend me 5 sci-fi books."}],
        response_format=SciFiBookList,
        process_chunk=assert_chunk,
    )

    print(resp)
    assert isinstance(resp.details.messages[0]["parsed"], SciFiBookList)
    assert len(resp.details.messages[0]["parsed"].books) == 5

    resp = await agent.run(
        [{"role": "user", "content": "1 + 1 = 2?"}],
        response_format=bool,
    )
    assert resp.content is True


async def test_structured_output_with_tool_use():
    agent = Agent(
        name="test",
        instructions="You are a sci-fi fan, you can answer any sci-fi related questions, and you can use the tool calls to get the rating of each book.",
    )

    class SciFiBook(BaseModel):
        title: str
        author: str
        year: int

    class SciFiBookList(BaseModel):
        books: List[SciFiBook]
        ratings: List[float] = Field(
            description="Use function call to get the rating of each book."
        )

    called = False

    @agent.tool
    def get_book_rating(title: str) -> float:
        """Get the rating of a book."""
        nonlocal called
        called = True
        return random.random()

    answer = await agent.run(
        "Recommend me 5 sci-fi books, and use `get_book_rating` function to get the rating of each book.",
        response_format=SciFiBookList,
    )
    assert called
    assert isinstance(answer.content, SciFiBookList)
    assert len(answer.content.books) == 5
    for r in answer.content.ratings:
        assert 0 <= r <= 1.0


async def test_agent_result_passing():
    scifi_fan = Agent(
        name="scifi_fan",
        instructions="You are a sci-fi fan, you can answer any sci-fi related questions.",
    )

    scifi_critic = Agent(
        name="scifi_critic",
        instructions="You are a scifi critic, you can review a sci-fi book and give a rating.",
    )

    summary_agent = Agent(
        name="summary_agent",
        instructions="You are a summary agent, you can summarize the answer of other agent give a human readable format.",
    )

    class SciFiBook(BaseModel):
        title: str
        author: str
        year: int

    class SciFiBookList(BaseModel):
        books: List[SciFiBook]

    answer_recommend = await scifi_fan.run(
        "Recommend me 5 sci-fi books.",
        response_format=SciFiBookList,
    )
    print(answer_recommend.content)

    class SciFiBookListWithRating(SciFiBookList):
        ratings: List[float] = Field(description="Rate the book from 0 to 10.")
        comments: List[str] = Field(description="Comment on the book.")

    answer_critic = await scifi_critic.run(
        answer_recommend,
        response_format=SciFiBookListWithRating,
    )
    print(answer_critic.content)

    assert isinstance(answer_critic.content, SciFiBookListWithRating)
    assert len(answer_critic.content.books) == 5
    assert len(answer_critic.content.ratings) == 5
    assert len(answer_critic.content.comments) == 5

    answer_summary = await summary_agent.run(
        answer_critic,
    )
    print(answer_summary.content)


async def test_tool_timeout():
    agent = Agent(
        name="test",
        instructions="",
        tool_timeout=1,
        use_memory=False,
    )

    @agent.tool
    def get_weather(city: str, unit: str = "celsius"):
        """Get the weather of a city."""
        return {"weather": "sunny", "temperature": 20}

    sync_tool_messages = await agent._handle_tool_calls(
        tool_calls=[{
            "id": "call_sync_weather",
            "function": {
                "name": "get_weather",
                "arguments": json.dumps({"city": "Palo Alto", "unit": "celsius"}),
            },
        }],
        context_variables={},
        timeout=agent.tool_timeout,
    )
    assert sync_tool_messages
    assert "sunny" in sync_tool_messages[0]["content"].lower()

    agent.functions.clear()

    flag = True

    @agent.tool
    async def get_weather(city: str, unit: str = "celsius"):
        """Get the weather of a city."""
        await asyncio.sleep(3)  # Increased from 2 to 3 for more margin
        nonlocal flag
        flag = False

    tool_messages = await agent._handle_tool_calls(
        tool_calls=[{
            "id": "call_async_weather",
            "function": {
                "name": "get_weather",
                "arguments": json.dumps({"city": "Palo Alto", "unit": "celsius"}),
            },
        }],
        context_variables={},
        timeout=agent.tool_timeout,
    )
    assert tool_messages
    bg_tasks = agent._bg_manager.list_tasks()
    assert bg_tasks, "Timed out tool should be adopted into background execution"
    assert bg_tasks[0].source == "timeout"
    assert flag, "Tool coroutine should continue in background instead of blocking the foreground call"


async def test_agent_transfer():
    scifi_fan = Agent(
        name="scifi_fan",
        instructions="You are a sci-fi fan, you can answer any sci-fi related questions.",
    )

    classic_literature_fan = Agent(
        name="classic_literature_fan",
        instructions="You are a classic literature fan, you can answer any classic literature related questions.",
    )

    @scifi_fan.tool
    def transfer_to_classic_literature_fan():
        """Transfer the question to the classic literature fan."""
        return classic_literature_fan

    resp = await scifi_fan.run("What is the best classic literature book?")
    assert isinstance(resp, AgentTransfer)
    assert resp.from_agent == scifi_fan.name
    assert resp.to_agent == classic_literature_fan.name


async def test_agent_relaxed_schema():
    agent = Agent(
        name="test",
        instructions="",
        relaxed_schema=True,
    )

    resp = await agent.run("What is the weather in Palo Alto?")
    print(resp.content)

    class Book(BaseModel):
        title: str
        author: str

    resp = await agent.run("Recommend me 5 sci-fi books.", response_format=list[Book])
    print(resp.content)


async def test_vision():
    agent = Agent(
        name="test",
        instructions="You are a vision agent, you can answer any vision related questions.",
    )

    resp = await agent.run(
        vision_input(
            "Is there a dog in the image?", HERE / "data/animal.png", from_path=True
        ),
        response_format=bool,
    )
    print(resp.content)
    assert resp.content is True
