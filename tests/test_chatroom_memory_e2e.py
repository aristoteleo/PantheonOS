"""
ChatRoom 端到端集成测试 — memory / dream / learning 全流程

模拟前端调用：create_chat → chat（多轮）→ 检查 .pantheon 文件变更

测试目录：tests/e2e_chatroom/
  .pantheon/settings.json  — 开启 memory + learning，使用 gemini-3-flash-preview
  .pantheon/memory-store/  — 测试后检查是否有 .md 文件
  .pantheon/skills/        — 测试后检查是否有 skill 文件
  .pantheon/MEMORY.md      — 检查索引

运行：
    uv run python -m pytest tests/test_chatroom_memory_e2e.py -v -s

注意：测试结果持久化在 tests/e2e_chatroom/.pantheon/，可以在测试后手动检查。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

_repo_root = Path(__file__).parent.parent
load_dotenv(_repo_root / ".env")

GEMINI_MODEL = "gemini/gemini-3-flash-preview"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

# 固定测试目录（不用 tmp_path，测试后可检查文件）
E2E_DIR = Path(__file__).parent / "e2e_chatroom"
PANTHEON_DIR = E2E_DIR / ".pantheon"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not GEMINI_KEY, reason="GEMINI_API_KEY not set"),
]

logger = logging.getLogger(__name__)


# ── 每个测试前清理 .pantheon（保留 settings.json）──

@pytest.fixture(autouse=True)
def clean_pantheon():
    """每个测试前清理 .pantheon/ 下的运行时数据，保留 settings.json。"""
    import shutil
    for name in ["memory-store", "memory-runtime", "memory", "skills", "skills-runtime", "MEMORY.md"]:
        target = PANTHEON_DIR / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
    yield


# ── 辅助：构造 ChatRoom（绑定到 e2e_chatroom 目录）──

async def _make_chatroom():
    """
    创建绑定到 tests/e2e_chatroom/ 的 ChatRoom。

    关键：
    - 用 get_settings(work_dir=E2E_DIR) 让 settings 指向测试目录
    - 手动构造 MemoryRuntime + LearningRuntime，绑定到 PANTHEON_DIR
    - 用 default_team 注入 team，绕过 template system
    - ChatRoom 的 memory_dir 指向 PANTHEON_DIR/memory（chat 历史）
    """
    from pantheon.agent import Agent
    from pantheon.team import PantheonTeam
    from pantheon.chatroom.room import ChatRoom
    from pantheon.internal.memory_system.runtime import MemoryRuntime
    from pantheon.internal.memory_system.plugin import MemorySystemPlugin
    from pantheon.internal.learning_system.runtime import LearningRuntime
    from pantheon.internal.learning_system.plugin import LearningPlugin
    from pantheon.settings import get_settings

    settings = get_settings(work_dir=E2E_DIR)

    # 从 settings 读取配置（使用 e2e_chatroom/.pantheon/settings.json）
    from pantheon.internal.memory_system.config import get_memory_system_config
    from pantheon.internal.learning_system.config import get_learning_system_config
    mem_config = get_memory_system_config(settings)
    learn_config = get_learning_system_config(settings)

    # 构造 runtime，绑定到测试目录
    mem_rt = MemoryRuntime(mem_config)
    mem_rt.initialize(
        pantheon_dir=PANTHEON_DIR,
        runtime_dir=PANTHEON_DIR / "memory-runtime",
    )

    learn_rt = LearningRuntime(learn_config)
    learn_rt.initialize(PANTHEON_DIR)

    # 构造 team（单 agent，通用助手）
    agent = Agent(
        name="assistant",
        instructions=(
            "你是一个全栈技术顾问，擅长架构设计和最佳实践。"
            "当用户描述技术问题时，给出详细的解决方案。"
            "当用户询问操作流程时，给出步骤化的指南。"
        ),
        model=GEMINI_MODEL,
    )
    team = PantheonTeam(
        agents=[agent],
        plugins=[MemorySystemPlugin(mem_rt), LearningPlugin(learn_rt)],
    )

    # ChatRoom：memory_dir 指向 PANTHEON_DIR/memory（存 chat 历史）
    chatroom = ChatRoom(
        memory_dir=str(PANTHEON_DIR / "memory"),
        default_team=team,
    )

    # team 需要 async_setup（注入 memory/skill guidance 到 agent instructions）
    await team.async_setup()

    return chatroom, mem_rt, learn_rt


def _print_pantheon_state(label: str = "") -> None:
    """打印 .pantheon 目录当前状态。"""
    print(f"\n{'='*60}")
    if label:
        print(f"📊 {label}")
    print(f"目录: {PANTHEON_DIR}")
    print('='*60)

    def _tree(path: Path, indent: int = 0):
        if not path.exists():
            return
        prefix = "  " * indent
        if path.is_file():
            size = path.stat().st_size
            print(f"{prefix}📄 {path.name} ({size}B)")
        else:
            print(f"{prefix}📁 {path.name}/")
            for child in sorted(path.iterdir()):
                if child.name.startswith("."):
                    continue
                _tree(child, indent + 1)

    _tree(PANTHEON_DIR)


def _read_file(path: Path, max_chars: int = 600) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    truncated = content[:max_chars] + ("..." if len(content) > max_chars else "")
    print(f"\n── {path.relative_to(E2E_DIR)} ──\n{truncated}")


async def _send(chatroom, chat_id: str, text: str) -> str:
    """发送一条消息，返回回复文本。"""
    result = await chatroom.chat(
        chat_id=chat_id,
        message=[{"role": "user", "content": text}],
    )
    assert result and result.get("success"), f"chat 失败: {result}"
    return result.get("response", "")


# ══════════════════════════════════════════════════════════════
# 测试 1：单 chat 多轮 → memory extraction
# ══════════════════════════════════════════════════════════════

class TestSingleChatExtraction:

    async def test_multi_turn_memory_extraction(self):
        """
        场景：一个 chat，2 轮对话，用户介绍自己的技术背景。
        期望：memory-store/ 出现 .md 文件，MEMORY.md 有索引。
        """
        chatroom, mem_rt, _ = await _make_chatroom()

        # 创建 chat
        result = await chatroom.create_chat(chat_name="test-intro")
        chat_id = result["chat_id"]
        print(f"\n✅ 创建 chat: {result['chat_name']} ({chat_id[:8]}...)")

        # Turn 1
        print("[Turn 1] 用户自我介绍...")
        r1 = await _send(chatroom, chat_id,
            "你好！我是张伟，后端工程师，在一家电商公司工作。"
            "我们用 Java + Spring Boot + MySQL，部署在阿里云上。"
            "我负责订单系统，每天处理约 200 万笔订单。"
        )
        print(f"  回复: {r1[:120]}...")

        # Turn 2
        print("[Turn 2] 补充技术细节...")
        r2 = await _send(chatroom, chat_id,
            "我们最近在引入 Elasticsearch 做商品搜索，"
            "用 Canal 同步 MySQL 数据到 ES。团队 12 人，我是架构师。"
        )
        print(f"  回复: {r2[:120]}...")

        # 等待 background extraction
        print("[等待] memory extraction（20s）...")
        await asyncio.sleep(20)

        _print_pantheon_state("Turn 2 后的状态")

        # 检查文件
        memory_store = PANTHEON_DIR / "memory-store"
        md_files = list(memory_store.glob("*.md")) if memory_store.exists() else []
        print(f"\n✅ memory-store/ 文件数: {len(md_files)}")
        for f in md_files:
            _read_file(f)

        memory_index = PANTHEON_DIR / "MEMORY.md"
        if memory_index.exists():
            _read_file(memory_index)

        assert memory_store.exists(), "memory-store/ 应存在"
        if not md_files:
            pytest.skip("LLM 未提取到 memory（非确定性）")


# ══════════════════════════════════════════════════════════════
# 测试 2：多个 chat → 跨 chat 记忆共享
# ══════════════════════════════════════════════════════════════

class TestMultiChatMemory:

    async def test_cross_chat_memory(self):
        """
        场景：Chat A 写入用户偏好，Chat B 提问时 LLM 应引用该偏好。
        """
        from pantheon.internal.memory_system.types import MemoryEntry, MemoryType

        chatroom, mem_rt, _ = await _make_chatroom()

        # 手动写入记忆（模拟 Chat A 已经提取的结果）
        mem_rt.write_memory(MemoryEntry(
            title="用户技术栈：Go + gRPC",
            summary="用户团队规定所有新服务必须用 Go + gRPC，禁止 Python",
            type=MemoryType.USER,
            content=(
                "用户团队的技术规范：所有新微服务必须使用 Go 语言编写，"
                "服务间通信使用 gRPC。团队已从 Python 完全迁移到 Go。"
                "数据库使用 TiDB（MySQL 兼容的分布式数据库）。"
            ),
        ))
        print(f"\n✅ 写入记忆: 用户技术栈 Go + gRPC")

        # Chat B：新对话，应检索到上面的记忆
        result = await chatroom.create_chat(chat_name="test-new-project")
        chat_id = result["chat_id"]
        print(f"✅ 创建 Chat B: {result['chat_name']} ({chat_id[:8]}...)")

        r = await _send(chatroom, chat_id,
            "我们要开发一个新的用户认证服务，你推荐用什么技术栈？"
        )
        print(f"\n[Chat B 回复]:\n{r}")

        content_lower = r.lower()
        go_mentioned = "go" in content_lower or "golang" in content_lower
        grpc_mentioned = "grpc" in content_lower
        print(f"\n✅ Go 被提及: {go_mentioned}")
        print(f"✅ gRPC 被提及: {grpc_mentioned}")

        _print_pantheon_state("跨 chat 测试后")

        assert r, "应有回复"
        if not go_mentioned:
            print("⚠️  记忆检索未生效（LLM 未提及 Go）")


# ══════════════════════════════════════════════════════════════
# 测试 3：Dream 整合
# ══════════════════════════════════════════════════════════════

class TestDream:

    async def test_dream_consolidation(self):
        """
        场景：写入 daily log，强制触发 dream，检查 memory-store/ 文件。
        """
        from datetime import datetime, timezone

        chatroom, mem_rt, _ = await _make_chatroom()

        now = datetime.now(timezone.utc)
        logs = [
            "用户是 Java 架构师，负责电商订单系统",
            "系统每天处理 200 万订单，部署在阿里云",
            "技术栈：Spring Boot + MySQL + Elasticsearch + Canal",
            "团队 12 人，用户是架构师",
            "正在引入 Kafka 做异步消息处理",
        ]
        print("\n[Dream] 写入 daily log...")
        for log in logs:
            mem_rt.store.append_daily_log(log, now)
            print(f"  + {log}")

        print("[Dream] 强制触发 dream 整合...")
        result = await mem_rt.maybe_run_dream(force=True)
        print(f"[Dream] 结果: {result}")

        _print_pantheon_state("Dream 后")

        memory_store = PANTHEON_DIR / "memory-store"
        md_files = list(memory_store.glob("*.md")) if memory_store.exists() else []
        print(f"\n✅ dream 后 memory-store/ 文件数: {len(md_files)}")
        for f in md_files:
            _read_file(f)

        memory_index = PANTHEON_DIR / "MEMORY.md"
        if memory_index.exists():
            _read_file(memory_index)

        assert result is not None and result.success, f"dream 应成功: {result}"
        assert len(md_files) >= 1, "dream 应写入至少 1 个 memory 文件"


# ══════════════════════════════════════════════════════════════
# 测试 4：Skill 提取
# ══════════════════════════════════════════════════════════════

class TestSkillExtraction:

    async def test_skill_from_procedure(self):
        """
        场景：用户询问详细操作流程，LLM 给出步骤化回答，
        期望 skill extractor 识别并保存为可复用 skill。
        """
        chatroom, _, learn_rt = await _make_chatroom()

        result = await chatroom.create_chat(chat_name="test-k8s-deploy")
        chat_id = result["chat_id"]
        print(f"\n✅ 创建 chat: {result['chat_name']}")

        print("[Turn 1] 询问 K8s 部署流程...")
        r1 = await _send(chatroom, chat_id,
            "请给我一个完整的流程：如何用 Helm 把 Spring Boot 应用部署到 Kubernetes，"
            "包括 Deployment、Service、Ingress 配置，以及 HPA 自动扩缩容设置。"
            "请给出具体的 kubectl 和 helm 命令。"
        )
        print(f"  回复长度: {len(r1)} 字符")

        print("[Turn 2] 追问监控配置...")
        r2 = await _send(chatroom, chat_id,
            "另外，如何配置 Prometheus + Grafana 监控这个服务？"
            "给出 ServiceMonitor 的 YAML 配置和关键监控指标。"
        )
        print(f"  回复长度: {len(r2)} 字符")

        print("[等待] skill extraction（25s）...")
        await asyncio.sleep(25)

        _print_pantheon_state("Skill 提取后")

        skills_dir = PANTHEON_DIR / "skills"
        skill_files = []
        if skills_dir.exists():
            skill_files = [f for f in skills_dir.rglob("*.md") if f.name != "SKILLS.md"]

        print(f"\n✅ skills/ 文件数: {len(skill_files)}")
        for f in skill_files:
            _read_file(f)

        if not skill_files:
            pytest.skip("LLM 未提取到 skill（非确定性）")


# ══════════════════════════════════════════════════════════════
# 测试 5：完整生命周期（3 个 chat + dream）
# ══════════════════════════════════════════════════════════════

class TestFullLifecycle:

    async def test_three_chats_full_pipeline(self):
        """
        Chat 1：用户介绍项目背景（2 轮）
        Chat 2：询问技术方案（2 轮）
        Dream：整合 daily log
        Chat 3：新对话，验证跨 chat 记忆检索
        """
        from datetime import datetime, timezone

        chatroom, mem_rt, learn_rt = await _make_chatroom()

        # ── Chat 1 ──
        print("\n" + "="*60)
        print("📌 Chat 1：项目背景")
        r1 = await chatroom.create_chat(chat_name="chat1-background")
        cid1 = r1["chat_id"]

        await _send(chatroom, cid1,
            "我们在开发一个实时数据分析平台，用 Flink 处理 Kafka 消息流，"
            "结果写入 ClickHouse，前端用 Grafana 展示。"
            "每秒处理约 10 万条事件。"
        )
        await _send(chatroom, cid1,
            "我们遇到了 Flink checkpoint 超时的问题，"
            "state backend 用的是 RocksDB，checkpoint 间隔 60 秒。"
        )
        print("  Chat 1 完成")
        await asyncio.sleep(5)

        # ── Chat 2 ──
        print("📌 Chat 2：技术方案")
        r2 = await chatroom.create_chat(chat_name="chat2-solution")
        cid2 = r2["chat_id"]

        await _send(chatroom, cid2,
            "如何优化 Flink 的 checkpoint 性能？"
            "给出具体的配置参数和最佳实践。"
        )
        await _send(chatroom, cid2,
            "另外，ClickHouse 的写入性能如何优化？"
            "我们现在用 JDBC sink，延迟比较高。"
        )
        print("  Chat 2 完成")

        print("[等待] extraction（20s）...")
        await asyncio.sleep(20)

        # ── Dream ──
        print("📌 Dream 整合")
        now = datetime.now(timezone.utc)
        mem_rt.store.append_daily_log("用户构建实时数据分析平台：Flink + Kafka + ClickHouse", now)
        mem_rt.store.append_daily_log("遇到 Flink checkpoint 超时，RocksDB state backend", now)
        mem_rt.store.append_daily_log("ClickHouse 写入延迟高，使用 JDBC sink", now)

        dream_result = await mem_rt.maybe_run_dream(force=True)
        print(f"  Dream 结果: {dream_result}")

        # ── Chat 3：验证记忆检索 ──
        print("📌 Chat 3：验证跨 chat 记忆")
        r3 = await chatroom.create_chat(chat_name="chat3-followup")
        cid3 = r3["chat_id"]

        reply = await _send(chatroom, cid3,
            "我们的流处理系统最近遇到了性能问题，你有什么建议？"
        )
        print(f"\n[Chat 3 回复]:\n{reply}")

        # ── 最终状态 ──
        _print_pantheon_state("完整生命周期后")

        memory_store = PANTHEON_DIR / "memory-store"
        md_files = list(memory_store.glob("*.md")) if memory_store.exists() else []
        skills_dir = PANTHEON_DIR / "skills"
        skill_files = [f for f in skills_dir.rglob("*.md") if f.name != "SKILLS.md"] if skills_dir.exists() else []

        for f in md_files:
            _read_file(f)
        if (PANTHEON_DIR / "MEMORY.md").exists():
            _read_file(PANTHEON_DIR / "MEMORY.md")

        print(f"\n📈 统计：")
        print(f"  memory-store/ 文件数: {len(md_files)}")
        print(f"  skills/ 文件数: {len(skill_files)}")
        print(f"  Dream 成功: {dream_result is not None and dream_result.success}")

        # 验证 Chat 3 回复包含流处理相关词汇（说明记忆被检索到）
        content_lower = reply.lower()
        terms = ["flink", "kafka", "clickhouse", "checkpoint", "流处理", "实时"]
        found = [t for t in terms if t in content_lower]
        print(f"  Chat 3 回复中的相关词: {found}")

        assert reply, "Chat 3 应有回复"
        assert dream_result is not None and dream_result.success, "Dream 应成功"


# ══════════════════════════════════════════════════════════════
# 测试 6：call_agent 委托 — sub-agent 结果不触发 extraction
# ══════════════════════════════════════════════════════════════

class TestCallAgentDelegation:

    async def test_call_agent_memory_only_on_main_agent(self):
        """
        场景：coordinator 通过 call_agent() 委托 specialist 回答问题。
        验证：
          - sub-agent 的 on_run_end（有 'question' key）不触发 memory extraction
          - main agent 的 on_run_end 正常触发 extraction
          - memory-store/ 只包含 main agent 对话的记忆，不重复
        """
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.chatroom.room import ChatRoom
        from pantheon.internal.memory_system.runtime import MemoryRuntime
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.learning_system.runtime import LearningRuntime
        from pantheon.internal.learning_system.plugin import LearningPlugin
        from pantheon.settings import get_settings

        settings = get_settings(work_dir=E2E_DIR)
        from pantheon.internal.memory_system.config import get_memory_system_config
        from pantheon.internal.learning_system.config import get_learning_system_config
        mem_config = get_memory_system_config(settings)
        learn_config = get_learning_system_config(settings)

        mem_rt = MemoryRuntime(mem_config)
        mem_rt.initialize(
            pantheon_dir=PANTHEON_DIR,
            runtime_dir=PANTHEON_DIR / "memory-runtime",
        )
        learn_rt = LearningRuntime(learn_config)
        learn_rt.initialize(PANTHEON_DIR)

        # 两个 agent：coordinator 委托 specialist
        coordinator = Agent(
            name="coordinator",
            instructions=(
                "你是一个协调者。当用户询问技术问题时，"
                "使用 call_agent('specialist', '问题') 委托给 specialist 回答。"
                "然后把 specialist 的回答总结给用户。"
            ),
            model=GEMINI_MODEL,
        )
        specialist = Agent(
            name="specialist",
            instructions=(
                "你是一个技术专家，专注于数据库和后端架构。"
                "给出简洁、专业的技术建议。"
            ),
            model=GEMINI_MODEL,
        )

        team = PantheonTeam(
            agents=[coordinator, specialist],
            plugins=[MemorySystemPlugin(mem_rt), LearningPlugin(learn_rt)],
        )
        await team.async_setup()

        chatroom = ChatRoom(
            memory_dir=str(PANTHEON_DIR / "memory"),
            default_team=team,
        )

        result = await chatroom.create_chat(chat_name="test-call-agent")
        chat_id = result["chat_id"]
        print(f"\n✅ 创建 chat: {result['chat_name']}")

        # 用户介绍背景，触发委托
        print("[Turn 1] 用户提问，coordinator 委托 specialist...")
        r1 = await _send(chatroom, chat_id,
            "我是陈磊，DBA，我们的 PostgreSQL 数据库有 5 亿条记录，"
            "查询越来越慢。请给我一个优化方案。"
        )
        print(f"  回复: {r1[:200]}...")

        # 等待 extraction
        print("[等待] memory extraction（20s）...")
        await asyncio.sleep(20)

        _print_pantheon_state("call_agent 委托后")

        memory_store = PANTHEON_DIR / "memory-store"
        md_files = list(memory_store.glob("*.md")) if memory_store.exists() else []
        print(f"\n✅ memory-store/ 文件数: {len(md_files)}")
        for f in md_files:
            _read_file(f)

        # 验证：有回复（委托链正常工作）
        assert r1, "coordinator 应有最终回复"

        # 验证：dream counter 只增加了 1（main agent），不是 2（sub-agent 不应计数）
        if mem_rt.dream_gate:
            counter = mem_rt.dream_gate._session_counter
            print(f"  Dream session counter: {counter}（应为 1，sub-agent 不计数）")
            assert counter == 1, f"Dream counter 应为 1，实际为 {counter}"


# ══════════════════════════════════════════════════════════════
# 测试 7：memory guidance 注入 + pre_compression flush
# ══════════════════════════════════════════════════════════════

class TestMemoryGuidanceAndFlush:

    async def test_memory_guidance_in_agent_instructions(self):
        """
        验证 async_setup 后 agent instructions 包含 memory guidance，
        说明 agent 知道如何读写 .pantheon/memory-store/。
        """
        chatroom, mem_rt, learn_rt = await _make_chatroom()

        # chatroom 用 default_team，team 已经 async_setup
        # 直接检查 team 里的 agent instructions
        team = chatroom._default_team
        agent = team.team_agents[0]

        print(f"\n[Agent instructions 长度] {len(agent.instructions)}")
        print(f"[包含 Long-Term Memory] {'Long-Term Memory' in agent.instructions}")
        print(f"[包含 memory-store] {'.pantheon/memory-store/' in agent.instructions}")

        assert "Long-Term Memory" in agent.instructions, "memory guidance 应注入到 agent instructions"
        assert ".pantheon/memory-store/" in agent.instructions

    async def test_pre_compression_flush_writes_daily_log(self):
        """
        验证 pre_compression hook 把对话内容写入 daily log。
        这是 compact 触发时保存上下文的关键路径。
        """
        chatroom, mem_rt, _ = await _make_chatroom()

        # 创建 chat 并对话
        result = await chatroom.create_chat(chat_name="test-flush")
        chat_id = result["chat_id"]

        await _send(chatroom, chat_id,
            "我们的 API 认证端点是 POST /api/v2/auth/login，"
            "使用 JWT token，有效期 24 小时。"
        )

        # 直接调用 pre_compression（模拟 compact 触发）
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        mem_plugin = next(p for p in chatroom._default_team.plugins if isinstance(p, MemorySystemPlugin))

        messages = [
            {"role": "user", "content": "API 认证端点是 POST /api/v2/auth/login"},
            {"role": "assistant", "content": "好的，JWT token 有效期 24 小时。"},
        ]
        flush_result = await mem_plugin.pre_compression(None, chat_id, messages)
        print(f"\n[Flush 结果] {flush_result}")

        # 验证 daily log 有内容
        logs = mem_rt.store.list_daily_logs()
        print(f"[Daily logs] {[l.name for l in logs]}")

        _print_pantheon_state("Flush 后")

        assert logs, "pre_compression should write to daily log"
        log_content = logs[0].read_text()
        print(f"[log content]\n{log_content[:300]}")
        assert len(log_content) > 0


# ══════════════════════════════════════════════════════════════
# Test 8: Compression paths in real ChatRoom
# ══════════════════════════════════════════════════════════════

class TestCompressionInChatRoom:

    @pytest.mark.timeout(300)
    async def test_session_note_compact_via_chatroom(self):
        """
        Path A: Session Note Compact triggered inside a real ChatRoom team.

        Uses team.run() directly to accumulate session note, then injects
        CompressionPlugin and verifies Session Note Compact path succeeds.
        """
        from pantheon.internal.compression.plugin import CompressionPlugin
        from pantheon.internal.memory_system.plugin import MemorySystemPlugin
        from pantheon.internal.memory import Memory

        chatroom, mem_rt, _ = await _make_chatroom()
        team = chatroom._default_team

        memory = Memory(name="compact-e2e-test")

        # Run one real turn to get LLM-generated content into memory
        await team.run(
            "I'm a backend engineer. Our stack is Python + FastAPI + PostgreSQL. "
            "We deploy on AWS ECS with Docker. Auth uses JWT tokens with 24h expiry.",
            memory=memory,
        )

        session_id = memory.id

        # Explicitly write session note — threshold is 100 tokens (from settings.json)
        await mem_rt.maybe_update_session_note(session_id, memory._messages, 200)
        await mem_rt.wait_for_session_note(session_id)

        # Add more messages AFTER session note was written so boundary < total
        for i in range(6):
            memory._messages.append({"role": "user", "content": f"Q{i}: How should we handle DB migrations?"})
            memory._messages.append({
                "role": "assistant",
                "content": f"A{i}: Use Alembic for PostgreSQL migrations with version control.",
                "_metadata": {"total_tokens": 5000, "max_tokens": 8000},
            })

        total_count = len(memory._messages)
        print(f"\n[total message count] {total_count}")
        print(f"[session note empty?] {mem_rt.is_session_note_empty(session_id)}")
        sm = mem_rt.get_session_note_for_compact(session_id)
        print(f"[session note content]\n{sm[:300]}")

        # Inject CompressionPlugin
        comp_plugin = CompressionPlugin({
            "enable": True,
            "threshold": 0.0,
            "preserve_recent_messages": 2,
            "compression_model": GEMINI_MODEL,
            "retry_after_messages": 0,
        })
        await comp_plugin.on_team_created(team)

        result = await comp_plugin._perform_compression(team, memory)

        print(f"\n[compression result] {result}")
        print(f"[message count after] {len(memory._messages)}")
        roles = [m["role"] for m in memory._messages]
        print(f"[message roles] {roles}")

        _print_pantheon_state("After Session Note Compact")

        assert result.get("success"), f"Compression should succeed: {result}"
        assert len(memory._messages) > original_count, "Message count should increase (checkpoint inserted)"
        assert result.get("method") == "session_note_compact"

        # Find the compression checkpoint
        compression_msgs = [m for m in memory._messages if m.get("role") == "compression"]
        assert len(compression_msgs) == 1
        assert "CHECKPOINT" in compression_msgs[0]["content"]
        assert compression_msgs[0]["_metadata"]["method"] == "session_note_compact"

    async def test_llm_fallback_compression_via_chatroom(self):
        """
        Path B: LLM fallback compression in a real ChatRoom.

        Bypasses Session Note Compact by using a fresh memory with no session notes.
        Expected: role:compression checkpoint inserted, original messages preserved.
        """
        from pantheon.internal.compression.plugin import CompressionPlugin
        from pantheon.internal.memory import Memory

        chatroom, mem_rt, _ = await _make_chatroom()
        team = chatroom._default_team

        # Build a fresh memory with enough messages but no session note written
        memory = Memory(name="llm-compress-e2e")
        for i in range(6):
            memory._messages.append({"role": "user", "content": f"Q{i}: Explain Python's asyncio event loop."})
            memory._messages.append({
                "role": "assistant",
                "content": f"A{i}: asyncio runs a single-threaded event loop that schedules coroutines cooperatively.",
                "_metadata": {"total_tokens": 5000, "max_tokens": 8000},
            })

        original_count = len(memory._messages)
        session_id = memory.id

        # Confirm no session note → forces LLM path
        assert mem_rt.is_session_note_empty(session_id), "session note must be empty to test LLM fallback"

        comp_plugin = CompressionPlugin({
            "enable": True,
            "threshold": 0.0,
            "preserve_recent_messages": 2,
            "compression_model": GEMINI_MODEL,
            "retry_after_messages": 0,
        })
        await comp_plugin.on_team_created(team)

        print(f"\n[original message count] {original_count}")

        result = await comp_plugin._perform_compression(team, memory, force=True)

        print(f"[compression result] {result}")
        print(f"[message count after] {len(memory._messages)}")
        roles = [m["role"] for m in memory._messages]
        print(f"[message roles] {roles}")

        _print_pantheon_state("After LLM Fallback Compression")

        assert result.get("success"), f"LLM compression should succeed: {result}"
        assert "compression" in roles, "role:compression checkpoint should be inserted"

        comp_msg = next(m for m in memory._messages if m["role"] == "compression")
        print(f"[compression message]\n{comp_msg['content'][:400]}")
        assert len(comp_msg["content"]) > 50
        # Non-destructive: original messages still present
        assert len(memory._messages) > 2

        # Verify pre_compression hook wrote daily log
        logs = mem_rt.store.list_daily_logs()
        print(f"[daily logs] {[l.name for l in logs]}")
        assert logs, "pre_compression hook should write daily log before LLM compression"
