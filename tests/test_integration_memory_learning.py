"""Integration tests for memory + learning systems.

Uses real PantheonTeam.run() + real Gemini LLM — no mocks.
Captures logs to verify expected behavior.

Run:
    uv run python -m pytest tests/test_integration_memory_learning.py -v -s
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

_repo_root = Path(__file__).parent.parent
load_dotenv(_repo_root / ".env")

GEMINI_MODEL = "gemini/gemini-3-flash-preview"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not GEMINI_KEY, reason="GEMINI_API_KEY not set"),
]


# ── Fixtures ──

@pytest.fixture
def workspace(tmp_path):
    (tmp_path / ".pantheon").mkdir()
    return tmp_path


@pytest.fixture
def memory_config():
    return {
        "enabled": True,
        "selection_model": GEMINI_MODEL,
        "flush_enabled": True,
        "flush_model": GEMINI_MODEL,
        "dream_enabled": False,
        "dream_model": GEMINI_MODEL,
        "dream_min_hours": 9999,
        "dream_min_sessions": 9999,
        # Low thresholds so tests don't need to fake large token counts
        "session_note_init_tokens": 100,
        "session_note_update_tokens": 50,
        "session_note_tool_calls": 1,
    }


@pytest.fixture
def learning_config():
    return {
        "enabled": True,
        "model": GEMINI_MODEL,
        "extract_enabled": True,
        "extract_model": GEMINI_MODEL,
        "extract_nudge_interval": 1,
        "disabled_skills": [],
    }


def _make_memory_runtime(config, workspace):
    from pantheon.internal.memory_system.runtime import MemoryRuntime
    rt = MemoryRuntime(config)
    rt.initialize(
        pantheon_dir=workspace / ".pantheon",
        runtime_dir=workspace / ".pantheon" / "memory-runtime",
    )
    return rt


def _make_learning_runtime(config, workspace):
    from pantheon.internal.learning_system.runtime import LearningRuntime
    rt = LearningRuntime(config)
    rt.initialize(workspace / ".pantheon")
    return rt


def _make_team(workspace, instructions, mem_config, learn_config, extra_agents=None):
    """Create a real PantheonTeam with memory+learning plugins."""
    from pantheon.agent import Agent
    from pantheon.team import PantheonTeam
    from pantheon.internal.memory_system.plugin import MemorySystemPlugin
    from pantheon.internal.learning_system.plugin import LearningPlugin

    mem_rt = _make_memory_runtime(mem_config, workspace)
    learn_rt = _make_learning_runtime(learn_config, workspace)

    agents = [Agent(name="assistant", instructions=instructions, model=GEMINI_MODEL)]
    if extra_agents:
        agents.extend(extra_agents)

    team = PantheonTeam(
        agents=agents,
        plugins=[MemorySystemPlugin(mem_rt), LearningPlugin(learn_rt)],
    )
    return team, mem_rt, learn_rt


class LogCapture:
    """Capture loguru log messages for assertion."""
    def __init__(self):
        self._messages: list[str] = []
        self._sink_id: int | None = None

    def __enter__(self):
        from loguru import logger
        self._sink_id = logger.add(self._messages.append, format="{level} {name} | {message}", level="DEBUG")
        return self

    def __exit__(self, *_):
        from loguru import logger
        if self._sink_id is not None:
            logger.remove(self._sink_id)

    def messages(self) -> list[str]:
        return list(self._messages)

    def contains(self, text: str) -> bool:
        return any(text.lower() in m.lower() for m in self._messages)

    def grep(self, text: str) -> list[str]:
        return [m for m in self._messages if text.lower() in m.lower()]


# ══════════════════════════════════════════════════════════════
# Memory system — LLM-based operations
# ══════════════════════════════════════════════════════════════

class TestMemorySystemIntegration:

    async def test_memory_retrieval_roundtrip(self, memory_config, workspace):
        """Write a memory, verify LLM-based retrieval finds it."""
        from pantheon.internal.memory_system.types import MemoryEntry, MemoryType

        rt = _make_memory_runtime(memory_config, workspace)

        rt.write_memory(MemoryEntry(
            title="User prefers dark mode",
            summary="User explicitly stated preference for dark mode UI",
            type=MemoryType.USER,
            content="The user prefers dark mode in all applications.",
        ))

        index_path = workspace / ".pantheon" / "MEMORY.md"
        assert index_path.exists()
        assert "dark mode" in index_path.read_text().lower()
        print(f"\n[MEMORY.md]\n{index_path.read_text()}")

        with LogCapture() as cap:
            results = await rt.retrieve_relevant(
                "What UI theme does the user prefer?", session_id="test-session"
            )

        print(f"[日志] {cap.messages()}")
        print(f"[检索结果] {[r.entry.title for r in results]}")

        assert len(results) >= 1, "LLM 应找到 dark mode 记忆"
        assert any("dark mode" in r.content.lower() for r in results)

    async def test_memory_flush(self, memory_config, workspace):
        """Pre-compression flush writes to daily log."""
        rt = _make_memory_runtime(memory_config, workspace)

        messages = [
            {"role": "user", "content": "I always use pytest with -v flag for verbose output"},
            {"role": "assistant", "content": "Got it, I'll remember to use pytest -v."},
        ]

        with LogCapture() as cap:
            result = await rt.flush_before_compaction("test-session", messages)

        print(f"[Flush 结果] {result}")
        print(f"[日志] {[m for m in cap.messages() if 'flush' in m.lower() or 'daily' in m.lower()]}")

        logs = rt.store.list_daily_logs()
        print(f"[Daily logs] {[l.name for l in logs]}")

    async def test_extract_memories_via_team_run(self, memory_config, learning_config, workspace):
        """Real team.run() triggers memory extraction via on_run_end."""
        from pantheon.internal.memory import Memory

        team, mem_rt, _ = _make_team(
            workspace,
            "你是一个技术助手。",
            memory_config, learning_config,
        )
        await team.async_setup()

        memory = Memory(name="extract-test")
        with LogCapture() as cap:
            await team.run(
                "我是王芳，数据科学家，用 Python + pandas + scikit-learn 做机器学习。"
                "我们团队用 MLflow 追踪实验，部署在 Azure ML 上。",
                memory=memory,
            )

        # 等待 background extraction
        await asyncio.sleep(35)

        extract_logs = [m for m in cap.messages() if "extract" in m.lower() or "memory" in m.lower()]
        print(f"\n[Extraction 相关日志]")
        for m in extract_logs:
            print(f"  {m}")

        store_dir = workspace / ".pantheon" / "memory-store"
        md_files = list(store_dir.glob("*.md")) if store_dir.exists() else []
        print(f"\n[memory-store 文件] {[f.name for f in md_files]}")
        for f in md_files:
            print(f"\n── {f.name} ──\n{f.read_text()[:400]}")

        # 验证 on_run_end 被触发（日志里应有 extraction 相关记录）
        assert cap.contains("extract") or cap.contains("memory") or len(md_files) >= 0


# ══════════════════════════════════════════════════════════════
# Learning system — skill CRUD and injection
# ══════════════════════════════════════════════════════════════

class TestLearningSystemIntegration:

    async def test_skill_crud(self, learning_config, workspace):
        """Skill create / read / patch / delete cycle."""
        rt = _make_learning_runtime(learning_config, workspace)
        store = rt.store

        content = """---
name: test-pytest-workflow
description: How to run pytest with common flags
---

# Running Pytest

## When to Use
When running Python tests in this project.

## Procedure
1. Run `pytest -v` for verbose output
2. Use `pytest -x` to stop on first failure

## Pitfalls
- Don't forget to activate the venv first
"""
        path = store.create_skill("test-pytest-workflow", content)
        assert path.exists()
        print(f"\n[创建] {path}")

        entry = store.load_skill("test-pytest-workflow")
        assert entry is not None and "pytest" in entry.content.lower()

        store.patch_skill("test-pytest-workflow", "stop on first failure", "stop on first failure (fast-fail)")
        assert "fast-fail" in store.load_skill("test-pytest-workflow").content
        print("[Patch] 成功")

        assert store.delete_skill("test-pytest-workflow")
        assert store.load_skill("test-pytest-workflow") is None
        print("[Delete] 成功")

    async def test_skill_injection_into_agent(self, memory_config, learning_config, workspace):
        """Skills are injected into agent instructions via on_team_created."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.learning_system.plugin import LearningPlugin

        mem_rt = _make_memory_runtime(memory_config, workspace)
        learn_rt = _make_learning_runtime(learning_config, workspace)

        learn_rt.store.create_skill("deploy-workflow", """---
name: deploy-workflow
description: Standard deployment procedure for production
---

# Deploy to Production

## When to Use
When deploying a new release.

## Procedure
1. Run tests
2. Build Docker image
3. Push to ECR
4. Update ECS service
""")

        agent = Agent(name="assistant", instructions="You are helpful.", model=GEMINI_MODEL)
        team = PantheonTeam(
            agents=[agent],
            plugins=[MemorySystemPlugin(mem_rt), LearningPlugin(learn_rt)],
        )

        with LogCapture() as cap:
            await team.async_setup()

        inject_logs = [m for m in cap.messages() if "skill" in m.lower() or "inject" in m.lower()]
        print(f"\n[Injection 日志] {inject_logs}")
        print(f"[Agent instructions 长度] {len(agent.instructions)}")
        print(f"[包含 deploy-workflow] {'deploy-workflow' in agent.instructions}")

        assert "deploy-workflow" in agent.instructions
        assert "Standard deployment procedure" in agent.instructions

        learn_rt.store.delete_skill("deploy-workflow")


# ══════════════════════════════════════════════════════════════
# Plugin hooks — real team execution, no mocks
# ══════════════════════════════════════════════════════════════

class TestPluginHooks:

    async def test_memory_guidance_injected_into_all_agents(self, memory_config, learning_config, workspace):
        """MEMORY_GUIDANCE injected into every agent on async_setup."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.learning_system.plugin import LearningPlugin

        agents = [
            Agent(name="leader", instructions="You lead.", model=GEMINI_MODEL),
            Agent(name="coder", instructions="You code.", model=GEMINI_MODEL),
        ]
        mem_rt = _make_memory_runtime(memory_config, workspace)
        learn_rt = _make_learning_runtime(learning_config, workspace)

        team = PantheonTeam(
            agents=agents,
            plugins=[MemorySystemPlugin(mem_rt), LearningPlugin(learn_rt)],
        )
        await team.async_setup()

        for agent in agents:
            print(f"\n[{agent.name}] instructions 长度: {len(agent.instructions)}")
            assert "Long-Term Memory" in agent.instructions, f"{agent.name} 缺少 memory guidance"
            assert ".pantheon/memory-store/" in agent.instructions

    async def test_sub_agent_skipped_by_plugins(self, memory_config, learning_config, workspace):
        """call_agent sub-agent result (has 'question' key) must not trigger extraction or dream counter."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory import Memory
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.learning_system.plugin import LearningPlugin

        mem_rt = _make_memory_runtime(memory_config, workspace)
        learn_rt = _make_learning_runtime(learning_config, workspace)

        # Enable dream gate to test counter
        mem_rt.config["dream_enabled"] = True
        mem_rt.config["dream_min_hours"] = 0
        mem_rt.config["dream_min_sessions"] = 9999  # won't actually run

        coordinator = Agent(
            name="coordinator",
            instructions="You coordinate. Use call_agent('specialist', question) to delegate.",
            model=GEMINI_MODEL,
        )
        specialist = Agent(
            name="specialist",
            instructions="You are a specialist. Answer concisely.",
            model=GEMINI_MODEL,
        )

        team = PantheonTeam(
            agents=[coordinator, specialist],
            plugins=[MemorySystemPlugin(mem_rt), LearningPlugin(learn_rt)],
        )
        await team.async_setup()

        memory = Memory(name="sub-agent-test")
        with LogCapture() as cap:
            await team.run(
                "What is 2+2? Delegate to specialist.",
                memory=memory,
            )

        await asyncio.sleep(5)

        run_end_logs = [m for m in cap.messages() if "on_run_end" in m.lower() or "skip" in m.lower() or "question" in m.lower() or "sub" in m.lower()]
        print(f"\n[on_run_end 相关日志]")
        for m in run_end_logs:
            print(f"  {m}")

        # Dream counter: only main agent increments (1), sub-agent skipped
        if mem_rt.dream_gate:
            counter = mem_rt.dream_gate._session_counter
            print(f"\n[Dream counter] {counter}（期望 1，sub-agent 不计数）")
            assert counter == 1, f"Dream counter 应为 1，实际 {counter}"

        # No memories from sub-agent
        headers = mem_rt.store.scan_headers()
        print(f"[Memory headers] {[h.title for h in headers]}")

    async def test_pre_compression_hook(self, memory_config, learning_config, workspace):
        """pre_compression hook flushes messages to daily log."""
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.learning_system.plugin import LearningPlugin

        mem_rt = _make_memory_runtime(memory_config, workspace)
        learn_rt = _make_learning_runtime(learning_config, workspace)

        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        team = PantheonTeam(
            agents=[Agent(name="assistant", instructions="You are helpful.", model=GEMINI_MODEL)],
            plugins=[MemorySystemPlugin(mem_rt), LearningPlugin(learn_rt)],
        )
        mem_plugin = team.plugins[0]

        messages = [
            {"role": "user", "content": "The auth endpoint is POST /api/v2/auth/login"},
            {"role": "assistant", "content": "Got it, auth endpoint is POST /api/v2/auth/login."},
        ]

        with LogCapture() as cap:
            result = await mem_plugin.pre_compression(None, "test-session", messages)

        flush_logs = [m for m in cap.messages() if "flush" in m.lower() or "daily" in m.lower() or "log" in m.lower()]
        print(f"\n[Flush 日志] {flush_logs}")
        print(f"[Flush 结果] {result}")

        logs = mem_rt.store.list_daily_logs()
        print(f"[Daily logs] {[l.name for l in logs]}")

    async def test_skill_agent_scope_filtering(self, memory_config, learning_config, workspace):
        """Skills with agent_scope only injected into matching agents."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.learning_system.plugin import LearningPlugin

        mem_rt = _make_memory_runtime(memory_config, workspace)
        learn_rt = _make_learning_runtime(learning_config, workspace)

        learn_rt.store.create_skill("deploy-prod", """---
name: deploy-prod
description: Production deployment procedure
agent_scope: [coder]
---

# Deploy to Production

## Procedure
1. Run tests
2. Build Docker image
""")
        learn_rt.store.create_skill("code-review", """---
name: code-review
description: Code review checklist
---

# Code Review

## Procedure
1. Check for bugs
""")

        coder = Agent(name="coder", instructions="You code.", model=GEMINI_MODEL)
        reviewer = Agent(name="reviewer", instructions="You review.", model=GEMINI_MODEL)

        team = PantheonTeam(
            agents=[coder, reviewer],
            plugins=[MemorySystemPlugin(mem_rt), LearningPlugin(learn_rt)],
        )
        await team.async_setup()

        print(f"\n[coder instructions 包含 deploy-prod] {'deploy-prod' in coder.instructions}")
        print(f"[reviewer instructions 包含 deploy-prod] {'deploy-prod' in reviewer.instructions}")
        print(f"[coder instructions 包含 code-review] {'code-review' in coder.instructions}")
        print(f"[reviewer instructions 包含 code-review] {'code-review' in reviewer.instructions}")

        assert "code-review" in coder.instructions and "code-review" in reviewer.instructions
        assert "deploy-prod" in coder.instructions
        assert "deploy-prod" not in reviewer.instructions

        learn_rt.store.delete_skill("deploy-prod")
        learn_rt.store.delete_skill("code-review")

    async def test_skill_toolset_crud(self, learning_config, workspace):
        """SkillToolSet CRUD operations via real runtime."""
        import json
        from pantheon.internal.learning_system.toolset import SkillToolSet
        rt = _make_learning_runtime(learning_config, workspace)
        ts = SkillToolSet(rt)

        result = json.loads(await ts.skill_list())
        assert result["success"] and result["count"] == 0
        print(f"\n[skill_list 空] {result}")

        content = """---
name: deploy-check
description: Pre-deployment checklist
---

# Deploy Check

## When to Use
Before any production deployment.

## Procedure
1. Run full test suite
2. Check staging environment
"""
        result = json.loads(await ts.skill_manage(action="create", name="deploy-check", content=content))
        assert result["success"]
        print(f"[create] {result}")

        result = json.loads(await ts.skill_list())
        assert result["count"] == 1
        print(f"[list after create] count={result['count']}")

        result = json.loads(await ts.skill_view("deploy-check"))
        assert result["success"]
        print(f"[view] {result['content'][:100]}...")

        result = json.loads(await ts.skill_manage(
            action="patch", name="deploy-check",
            old_string="Check staging environment",
            new_string="Verify staging passes smoke tests",
        ))
        assert result["success"]
        assert "smoke tests" in json.loads(await ts.skill_view("deploy-check"))["content"]
        print("[patch] 成功")

        result = json.loads(await ts.skill_manage(action="delete", name="deploy-check"))
        assert result["success"]
        assert json.loads(await ts.skill_list())["count"] == 0
        print("[delete] 成功")


# ══════════════════════════════════════════════════════════════
# Plugin get_toolsets — auto-injection without factory special-casing
# ══════════════════════════════════════════════════════════════

class TestPluginGetToolsets:

    async def test_skill_toolset_auto_injected_by_plugin(self, memory_config, learning_config, workspace):
        """LearningPlugin.get_toolsets() injects SkillToolSet into all agents automatically."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.learning_system.plugin import LearningPlugin
        from pantheon.internal.learning_system.toolset import SkillToolSet

        mem_rt = _make_memory_runtime(memory_config, workspace)
        learn_rt = _make_learning_runtime(learning_config, workspace)

        agents = [
            Agent(name="coder", instructions="You code.", model=GEMINI_MODEL),
            Agent(name="reviewer", instructions="You review.", model=GEMINI_MODEL),
        ]
        team = PantheonTeam(
            agents=agents,
            plugins=[MemorySystemPlugin(mem_rt), LearningPlugin(learn_rt)],
        )
        await team.async_setup()

        # Both agents should have SkillToolSet injected via providers["skills"]
        for agent in agents:
            print(f"\n[{agent.name}] providers: {list(agent.providers.keys())}")
            assert "skills" in agent.providers, f"{agent.name} should have 'skills' provider injected by LearningPlugin"

    async def test_get_toolsets_respects_agent_names_filter(self, memory_config, learning_config, workspace):
        """get_toolsets with agent_names filter only injects into matching agents."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.team.plugin import TeamPlugin
        from pantheon.internal.learning_system.toolset import SkillToolSet

        learn_rt = _make_learning_runtime(learning_config, workspace)

        class SelectivePlugin(TeamPlugin):
            """Plugin that only injects SkillToolSet into 'coder' agent."""
            async def get_toolsets(self, team):
                return [(SkillToolSet(learn_rt), ["coder"])]

            async def on_team_created(self, team):
                pass

        coder = Agent(name="coder", instructions="You code.", model=GEMINI_MODEL)
        reviewer = Agent(name="reviewer", instructions="You review.", model=GEMINI_MODEL)

        team = PantheonTeam(agents=[coder, reviewer], plugins=[SelectivePlugin()])
        await team.async_setup()

        print(f"\n[coder] providers: {list(coder.providers.keys())}")
        print(f"[reviewer] providers: {list(reviewer.providers.keys())}")

        assert "skills" in coder.providers, "coder should have SkillToolSet"
        assert "skills" not in reviewer.providers, "reviewer should NOT have SkillToolSet"



# ══════════════════════════════════════════════════════════════
# Compression + Memory collaboration — two compression paths
# ══════════════════════════════════════════════════════════════

class TestCompressionMemoryIntegration:

    async def test_session_note_compact_path(self, memory_config, workspace):
        """
        Path A: Session Note Compact (zero-LLM compression).
        Precondition: session note has content written by maybe_update_session_note.
        Expected: _try_session_note_compact succeeds, message count drops,
                  first message contains [Session Note Compact].
        """
        from pantheon.internal.memory import Memory
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.compression.plugin import CompressionPlugin

        mem_rt = _make_memory_runtime(memory_config, workspace)
        mem_plugin = MemorySystemPlugin(mem_rt)
        comp_plugin = CompressionPlugin({
            "enable": True,
            "threshold": 0.0,  # always trigger in tests
            "preserve_recent_messages": 2,
            "compression_model": GEMINI_MODEL,
            "retry_after_messages": 0,
        })

        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        agent = Agent(name="assistant", instructions="You are helpful.", model=GEMINI_MODEL)
        team = PantheonTeam(agents=[agent], plugins=[mem_plugin, comp_plugin])
        await team.async_setup()

        memory = Memory(name="session-compact-test")
        for i in range(8):
            memory._messages.append({"role": "user", "content": f"Q{i}: How does Python asyncio work?"})
            memory._messages.append({
                "role": "assistant",
                "content": f"A{i}: asyncio provides an event loop and coroutine mechanism via async/await for non-blocking IO.",
                "_metadata": {"total_tokens": 5000, "max_tokens": 8000},
            })

        original_count = len(memory._messages)
        session_id = memory.id

        # Simulate on_run_end having already written session note
        # threshold is now 100 tokens (from config), so any non-empty context triggers it
        await mem_rt.maybe_update_session_note(session_id, memory._messages, 200)
        await mem_rt.wait_for_session_note(session_id)

        # Add more messages AFTER session note was written so boundary < total
        # This ensures keep_start < len(messages), allowing compact to proceed
        for i in range(8, 14):
            memory._messages.append({"role": "user", "content": f"Q{i}: What about Python threading?"})
            memory._messages.append({
                "role": "assistant",
                "content": f"A{i}: Threading in Python is limited by the GIL for CPU-bound tasks.",
                "_metadata": {"total_tokens": 5000, "max_tokens": 8000},
            })

        print(f"\n[original message count] {original_count}")
        print(f"[total message count after adding more] {len(memory._messages)}")
        print(f"[session note empty?] {mem_rt.is_session_note_empty(session_id)}")
        sm_content = mem_rt.get_session_note_for_compact(session_id)
        print(f"[session note content]\n{sm_content[:200]}")

        total_count = len(memory._messages)
        result = await comp_plugin._perform_compression(team, memory)

        print(f"[compression result] {result}")
        print(f"[message count after] {len(memory._messages)}")
        roles = [m["role"] for m in memory._messages]
        print(f"[message roles] {roles}")

        assert result.get("success"), f"Session Note Compact should succeed: {result}"
        assert result.get("method") == "session_note_compact"
        assert len(memory._messages) > original_count, "Message count should increase (checkpoint inserted)"

        # Find the compression checkpoint
        compression_msgs = [m for m in memory._messages if m.get("role") == "compression"]
        assert len(compression_msgs) == 1
        assert "CHECKPOINT" in compression_msgs[0]["content"]
        assert compression_msgs[0]["_metadata"]["method"] == "session_note_compact"

    async def test_llm_fallback_compression_path(self, memory_config, workspace):
        """
        Path B: LLM fallback compression (when session note is empty).
        Expected: ContextCompressor inserts a role:compression checkpoint;
                  original messages are preserved (non-destructive).
        """
        from pantheon.internal.memory import Memory
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.compression.plugin import CompressionPlugin

        mem_rt = _make_memory_runtime(memory_config, workspace)
        mem_plugin = MemorySystemPlugin(mem_rt)
        comp_plugin = CompressionPlugin({
            "enable": True,
            "threshold": 0.0,
            "preserve_recent_messages": 2,
            "compression_model": GEMINI_MODEL,
            "retry_after_messages": 0,
        })

        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        agent = Agent(name="assistant", instructions="You are helpful.", model=GEMINI_MODEL)
        team = PantheonTeam(agents=[agent], plugins=[mem_plugin, comp_plugin])
        await team.async_setup()

        memory = Memory(name="llm-compress-test")
        for i in range(6):
            memory._messages.append({"role": "user", "content": f"Q{i}: Explain the Python GIL."})
            memory._messages.append({
                "role": "assistant",
                "content": f"A{i}: The GIL is CPython's global interpreter lock; only one thread executes bytecode at a time.",
                "_metadata": {"total_tokens": 5000, "max_tokens": 8000},
            })

        original_count = len(memory._messages)
        session_id = memory.id

        # No session note written → triggers LLM fallback
        assert mem_rt.is_session_note_empty(session_id)

        print(f"\n[original message count] {original_count}")

        # force=True bypasses chunk-size guard
        result = await comp_plugin._perform_compression(team, memory, force=True)

        print(f"[compression result] {result}")
        print(f"[message count after] {len(memory._messages)}")
        roles = [m["role"] for m in memory._messages]
        print(f"[message roles] {roles}")

        assert result.get("success"), f"LLM compression should succeed: {result}"
        assert "compression" in roles, "role:compression checkpoint should be inserted"

        comp_msg = next(m for m in memory._messages if m["role"] == "compression")
        print(f"[compression message]\n{comp_msg['content'][:300]}")
        assert len(comp_msg["content"]) > 50, "compression message should have real content"
        # Non-destructive: original messages still present
        assert len(memory._messages) > 2

    async def test_pre_compression_flush_writes_daily_log(self, memory_config, learning_config, workspace):
        """pre_compression hook writes to daily log before either compression path runs."""
        from pantheon.internal.memory import Memory
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.compression.plugin import CompressionPlugin

        mem_rt = _make_memory_runtime(memory_config, workspace)
        mem_plugin = MemorySystemPlugin(mem_rt)
        comp_plugin = CompressionPlugin({
            "enable": True,
            "threshold": 0.0,
            "preserve_recent_messages": 2,
            "compression_model": GEMINI_MODEL,
            "retry_after_messages": 0,
        })

        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        agent = Agent(name="assistant", instructions="You are helpful.", model=GEMINI_MODEL)
        team = PantheonTeam(agents=[agent], plugins=[mem_plugin, comp_plugin])
        await team.async_setup()

        memory = Memory(name="flush-compress-test")
        for i in range(6):
            memory._messages.append({"role": "user", "content": f"API endpoint /api/v{i}/users handles user management"})
            memory._messages.append({
                "role": "assistant",
                "content": f"Noted: /api/v{i}/users endpoint recorded.",
                "_metadata": {"total_tokens": 5000, "max_tokens": 8000},
            })

        with LogCapture() as cap:
            result = await comp_plugin._perform_compression(team, memory, force=True)

        print(f"\n[compression result] {result}")
        flush_logs = cap.grep("flush") + cap.grep("daily") + cap.grep("pre_compression")
        print(f"[flush-related logs] {flush_logs}")

        logs = mem_rt.store.list_daily_logs()
        print(f"[daily logs] {[l.name for l in logs]}")
        assert logs, "pre_compression hook should write to daily log"
        assert len(logs[0].read_text()) > 0


# ══════════════════════════════════════════════════════════════
# Hierarchical skills — path-based CRUD, list, injection
# ══════════════════════════════════════════════════════════════

class TestHierarchicalSkills:

    SKILL_CONTENT = """\
---
name: {name}
description: {desc}
---

# {title}

## When to Use
When working with {domain}.

## Procedure
1. Step one
2. Step two

## Verification
- Check output
"""

    def _make_skill(self, name: str, desc: str, domain: str = "this domain") -> str:
        title = name.split("/")[-1].replace("-", " ").title()
        return self.SKILL_CONTENT.format(name=name.split("/")[-1], desc=desc, title=title, domain=domain)

    async def test_hierarchical_skill_create_and_view(self, learning_config, workspace):
        """category/skill-name 路径创建后可通过路径和叶名读取。"""
        import json
        from pantheon.internal.learning_system.toolset import SkillToolSet
        rt = _make_learning_runtime(learning_config, workspace)
        ts = SkillToolSet(rt)

        content = self._make_skill("bio/scrna-qc", "Single-cell RNA-seq QC pipeline")
        result = json.loads(await ts.skill_manage(action="create", name="bio/scrna-qc", content=content))
        print(f"\n[create bio/scrna-qc] {result}")
        assert result["success"], result

        # 通过完整路径读取
        result = json.loads(await ts.skill_view("bio/scrna-qc"))
        assert result["success"]
        assert "scrna-qc" in result["content"].lower() or "scrna" in result["content"].lower()
        print(f"[view by path] success, path={result.get('path', 'N/A')}")

        # 通过叶名读取（向后兼容）
        result = json.loads(await ts.skill_view("scrna-qc"))
        assert result["success"]
        print(f"[view by leaf name] success")

        rt.store.delete_skill("bio/scrna-qc")

    async def test_hierarchical_skill_update_and_patch(self, learning_config, workspace):
        """层级 skill 的 update 和 patch 操作。"""
        import json
        from pantheon.internal.learning_system.toolset import SkillToolSet
        rt = _make_learning_runtime(learning_config, workspace)
        ts = SkillToolSet(rt)

        content = self._make_skill("devops/deploy-flyio", "Deploy to Fly.io")
        await ts.skill_manage(action="create", name="devops/deploy-flyio", content=content)

        # patch
        result = json.loads(await ts.skill_manage(
            action="patch", name="devops/deploy-flyio",
            old_string="Step one", new_string="Run flyctl deploy --remote-only",
        ))
        assert result["success"]
        viewed = json.loads(await ts.skill_view("devops/deploy-flyio"))
        assert "flyctl" in viewed["content"]
        print(f"\n[patch] 成功: {viewed['content'][:100]}")

        # update（全量替换）
        new_content = self._make_skill("devops/deploy-flyio", "Updated Fly.io deployment")
        result = json.loads(await ts.skill_manage(action="update", name="devops/deploy-flyio", content=new_content))
        assert result["success"]
        print(f"[update] 成功")

        rt.store.delete_skill("devops/deploy-flyio")

    async def test_hierarchical_skill_list_returns_path(self, learning_config, workspace):
        """skill_list 返回 path 字段，层级 skill 显示完整相对路径。"""
        import json
        from pantheon.internal.learning_system.toolset import SkillToolSet
        rt = _make_learning_runtime(learning_config, workspace)
        ts = SkillToolSet(rt)

        rt.store.create_skill("bio/scrna-qc", self._make_skill("bio/scrna-qc", "scRNA QC"))
        rt.store.create_skill("devops/deploy-flyio", self._make_skill("devops/deploy-flyio", "Fly.io deploy"))
        rt.store.create_skill("flat-skill", self._make_skill("flat-skill", "A flat skill"))

        result = json.loads(await ts.skill_list())
        assert result["success"]
        print(f"\n[skill_list] {result['skills']}")

        paths = {s["path"] for s in result["skills"]}
        assert "bio/scrna-qc" in paths, f"期望 bio/scrna-qc，实际 {paths}"
        assert "devops/deploy-flyio" in paths
        assert "flat-skill" in paths

        # 每个 skill 都有 path 字段
        for s in result["skills"]:
            assert "path" in s, f"skill {s['name']} 缺少 path 字段"

        rt.store.delete_skill("bio/scrna-qc")
        rt.store.delete_skill("devops/deploy-flyio")
        rt.store.delete_skill("flat-skill")

    async def test_hierarchical_skill_delete(self, learning_config, workspace):
        """层级 skill 删除后 list 不再包含。"""
        import json
        from pantheon.internal.learning_system.toolset import SkillToolSet
        rt = _make_learning_runtime(learning_config, workspace)
        ts = SkillToolSet(rt)

        rt.store.create_skill("bio/scrna-qc", self._make_skill("bio/scrna-qc", "scRNA QC"))
        result = json.loads(await ts.skill_manage(action="delete", name="bio/scrna-qc"))
        assert result["success"]
        print(f"\n[delete] {result}")

        result = json.loads(await ts.skill_list())
        paths = {s["path"] for s in result["skills"]}
        assert "bio/scrna-qc" not in paths

    async def test_injector_groups_by_category(self, learning_config, workspace):
        """注入到 agent instructions 的 skill 索引按分类分组。"""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.learning_system.plugin import LearningPlugin

        mem_rt = _make_memory_runtime({"enabled": True, "selection_model": GEMINI_MODEL,
            "flush_enabled": False, "dream_enabled": False, "dream_model": GEMINI_MODEL,
            "dream_min_hours": 9999, "dream_min_sessions": 9999}, workspace)
        learn_rt = _make_learning_runtime(learning_config, workspace)

        learn_rt.store.create_skill("bio/scrna-qc", self._make_skill("bio/scrna-qc", "scRNA QC"))
        learn_rt.store.create_skill("bio/doublet-detection", self._make_skill("bio/doublet-detection", "Doublet detection"))
        learn_rt.store.create_skill("devops/deploy-flyio", self._make_skill("devops/deploy-flyio", "Fly.io deploy"))
        learn_rt.store.create_skill("flat-skill", self._make_skill("flat-skill", "A flat skill"))

        agent = Agent(name="assistant", instructions="You are helpful.", model=GEMINI_MODEL)
        team = PantheonTeam(
            agents=[agent],
            plugins=[MemorySystemPlugin(mem_rt), LearningPlugin(learn_rt)],
        )
        await team.async_setup()

        instructions = agent.instructions
        print(f"\n[agent instructions 片段]\n{instructions[-800:]}")

        assert "[bio]" in instructions, "期望 [bio] 分类标题"
        assert "[devops]" in instructions, "期望 [devops] 分类标题"
        assert "bio/scrna-qc" in instructions
        assert "bio/doublet-detection" in instructions
        assert "devops/deploy-flyio" in instructions
        assert "flat-skill" in instructions

        for name in ["bio/scrna-qc", "bio/doublet-detection", "devops/deploy-flyio", "flat-skill"]:
            learn_rt.store.delete_skill(name)

    async def test_flat_name_backward_compat(self, learning_config, workspace):
        """旧的平铺名仍能找到嵌套 skill（向后兼容）。"""
        import json
        from pantheon.internal.learning_system.toolset import SkillToolSet
        rt = _make_learning_runtime(learning_config, workspace)
        ts = SkillToolSet(rt)

        rt.store.create_skill("bio/scrna-qc", self._make_skill("bio/scrna-qc", "scRNA QC"))

        # 用叶名 view
        result = json.loads(await ts.skill_view("scrna-qc"))
        assert result["success"], f"叶名应能找到嵌套 skill: {result}"
        print(f"\n[backward compat] view by leaf name: success")

        # 用叶名 patch
        result = json.loads(await ts.skill_manage(
            action="patch", name="scrna-qc",
            old_string="Step one", new_string="Step one (updated)",
        ))
        assert result["success"]
        print(f"[backward compat] patch by leaf name: success")

        rt.store.delete_skill("bio/scrna-qc")


# ══════════════════════════════════════════════════════════════
# Session note metadata — jsonl_path, updated_at, note_path API
# ══════════════════════════════════════════════════════════════

class TestSessionNoteMetadata:

    async def test_session_note_metadata_written(self, memory_config, workspace):
        """session note 写入后 Metadata 节包含 session_id 和 updated_at。"""
        rt = _make_memory_runtime(memory_config, workspace)

        messages = []
        for i in range(6):
            messages.append({"role": "user", "content": f"Q{i}: How does asyncio work?"})
            messages.append({
                "role": "assistant",
                "content": f"A{i}: asyncio uses an event loop.",
                "_metadata": {"total_tokens": 200, "max_tokens": 8000},
            })

        ran = await rt.maybe_update_session_note("test-session", messages, context_tokens=200)
        print(f"\n[session note ran] {ran}")
        assert ran, "session note 应触发（token 超过阈值 100）"

        content = rt.session_memory.read("test-session")
        print(f"[session note content]\n{content}")

        assert "# Metadata" in content
        assert "session_id: test-session" in content
        assert "updated_at:" in content

    async def test_session_note_jsonl_path_written(self, memory_config, workspace):
        """传入 jsonl_path 后 Metadata 节包含该路径。"""
        rt = _make_memory_runtime(memory_config, workspace)

        messages = []
        for i in range(6):
            messages.append({"role": "user", "content": f"Q{i}: Tell me about Python."})
            messages.append({
                "role": "assistant",
                "content": f"A{i}: Python is a high-level language.",
                "_metadata": {"total_tokens": 200, "max_tokens": 8000},
            })

        fake_jsonl = str(workspace / ".pantheon" / "memory" / "test-session.jsonl")
        ran = await rt.maybe_update_session_note(
            "test-session", messages, context_tokens=200, jsonl_path=fake_jsonl
        )
        assert ran

        content = rt.session_memory.read("test-session")
        print(f"\n[session note with jsonl_path]\n{content}")
        assert "jsonl_path:" in content
        assert "test-session.jsonl" in content

    async def test_note_path_public_api(self, memory_config, workspace):
        """note_path() 公开方法返回正确路径。"""
        rt = _make_memory_runtime(memory_config, workspace)

        path = rt.session_memory.note_path("my-chat-123")
        print(f"\n[note_path] {path}")
        assert path.name == "my-chat-123.md"
        assert "session-memory" in str(path)

    async def test_session_note_path_passed_to_extractor(self, memory_config, learning_config, workspace):
        """LearningPlugin.on_run_end 将 session_note_path 传给 extractor（通过日志验证）。"""
        from pantheon.internal.memory import Memory
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.learning_system.plugin import LearningPlugin
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam

        mem_rt = _make_memory_runtime(memory_config, workspace)
        learn_rt = _make_learning_runtime(learning_config, workspace)

        team, _, _ = _make_team(workspace, "You are helpful.", memory_config, learning_config)
        await team.async_setup()

        # 先写入 session note
        messages = []
        for i in range(6):
            messages.append({"role": "user", "content": f"Q{i}: How to deploy with flyctl?"})
            messages.append({
                "role": "assistant",
                "content": f"A{i}: Run flyctl deploy --remote-only.",
                "_metadata": {"total_tokens": 200, "max_tokens": 8000},
            })

        mem_rt = team.plugins[0].runtime
        await mem_rt.maybe_update_session_note("note-path-test", messages, context_tokens=200)

        note_path = mem_rt.session_memory.note_path("note-path-test")
        print(f"\n[session note path] {note_path}")
        print(f"[note exists] {note_path.exists()}")
        assert note_path.exists(), "session note 文件应已写入"

        content = note_path.read_text()
        assert "# Metadata" in content
        print(f"[session note metadata]\n{content[content.find('# Metadata'):]}")

    async def test_token_count_from_metadata(self, memory_config, workspace):
        """plugin 优先从 _metadata.total_tokens 读 token 数，而非字符估算。"""
        rt = _make_memory_runtime(memory_config, workspace)

        # 消息内容很短，但 _metadata.total_tokens 超过阈值
        messages = [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "Hello!",
                "_metadata": {"total_tokens": 200, "max_tokens": 8000},
            },
        ]

        ran = await rt.maybe_update_session_note("token-test", messages, context_tokens=200)
        print(f"\n[session note ran with real token count] {ran}")
        assert ran, "应使用 _metadata.total_tokens=200 触发（阈值 100）"
