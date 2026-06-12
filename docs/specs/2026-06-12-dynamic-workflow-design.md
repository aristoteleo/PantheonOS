# Pantheon Dynamic Workflow 设计方案

> 日期：2026-06-12
> 状态：设计冻结（17 项核心决策已对齐），待用户最终审查
> 范围：pantheon-agents（后端 Workflow Engine + Leader 工具集）+ pantheon-ui（Workflow 可视化）

## 1. 背景与目标

### 1.1 问题

当前 LLM 工具的两种主流交互模式都有缺陷：

- **传统 chat 模式**：对话即黑箱，任务执行过程不可见、不可结构化干预，长任务挤爆 leader 上下文。
- **看板模式**（Multica/Hermes）：任务粒度太细且分散；单个任务卡片内部仍是黑箱（点进去又是一个 chat）；不便于追踪整体目标。

### 1.2 目标

把"基于 chat 的黑箱对话"换成"基于目标的、结构可视化的协作"：

1. 用户在**同一个 chat 里持续对话**，由 Leader agent 持续塑造目标 / plan / workflow（可能多个）。
2. Workflow 的**内部结构可视化**（节点图），可被增量调整。
3. **Leader 上下文充分释放**：只负责对话沟通和 workflow 定义/干预，不微观管理节点。
4. 每个 workflow 节点是一个 agent，初期复用 Pantheon agent，**预留多运行时**（claude/codex CLI 等）扩展。
5. 用户**永远不需要理解 workflow/节点等概念**——只描述需求，Leader 翻译成 workflow 操作。

### 1.3 与现有模式的对比（价值论证）

#### 用户视角 —— "我如何与 AI 协作"

| | 传统 Chat 对话 | Multica 看板 | Pantheon Dynamic Workflow |
|---|---|---|---|
| **心智模型** | "我在和一个人聊天" | "我在给一组员工派工单" | "我在和一个**项目负责人**对话，他带着一个团队干活" |
| **表达方式** | 描述需求，然后等 | 创建 issue、填字段、分配 agent、切运行时 | 只描述需求/目标，**永远不需要懂 workflow/节点** |
| **任务进行中** | 盯流式输出或干等；插话会打断 | 看卡片状态变化；点进卡片又是黑箱 chat | 持续对话：随时问"到哪了"、随时说"跳过那步""再加个检查"，Leader 当场调整 |
| **出错时** | 翻长对话找错误，从头解释重来 | 卡片变红，进去看 log，重开任务 | 失败节点自动高亮 + Leader 主动告知原因，一句话决定重试/跳过/改方案 |
| **关注成本** | 全程陪跑（离开就断） | 碎片化（N 张卡片分别看） | **目标级**：一个 goal 一个画布，离开再回来状态完整（文件持久化） |

本质区别：chat 是"全程口头指挥一个人"；看板是"管理一堆工单"；本设计是"**与负责人对齐目标，执行托管给确定性引擎**"。

#### UI 视角 —— "我能看见什么"

| | Chat | Multica 看板 | Dynamic Workflow |
|---|---|---|---|
| **结构可见性** | ✗ 纯文本流，任务结构埋在滚动历史里 | △ 任务间结构可见（列/泳道），**任务内黑箱** | ✓ 任务内结构可视：trace 图实时生长，节点状态/进度/产物可见 |
| **粒度** | 无粒度（一锅粥） | 太细（一张卡 = 一个任务，痛点来源） | **目标级**：一个画布 = 一个 goal，节点是内部结构而非独立管理单元 |
| **实时性** | token 流 | 卡片状态轮换 | phases 骨架 → 节点实时长出（动态 fan-out 如实呈现） |
| **黑箱程度** | 全黑箱 | 卡片内黑箱 | **白箱**：结构、数据流（文件产物）、失败点全部外显 |
| **信息架构** | 平铺的对话列表 | Workspace → 看板 → 卡片 | Project → Task → Workflow（嵌入 chat 的画布 + 侧边栏） |

#### 能力上限 —— "能做多复杂的事"

之前的上限：**线性任务 + 最多一层 subagent 调用**（`call_agent` 同步等待，leader 上下文随子任务输出膨胀）。

| 能力 | Chat（含 subagent） | Multica 看板 | Dynamic Workflow |
|---|---|---|---|
| 线性多步任务 | ✓（上下文持续膨胀） | ✓（手动拆卡） | ✓ |
| 并行执行 | △ 同轮多 call_agent，靠 LLM 即兴 | △ 多卡并行，**卡片间无数据流** | ✓ `parallel()` 确定性并发，结果自动汇聚 |
| **动态 fan-out**（数量运行时才知道） | ✗ leader 手动逐个调，上下文爆炸 | ✗ 人肉建 N 张卡 | ✓ `modules.map(...)` 一行 |
| 多阶段流水线（发现→验证→汇总） | ✗ 靠 leader 上下文串接 | ✗ 卡片无依赖编排 | ✓ `pipeline()`，阶段间文件传递 |
| 循环到收敛（"找 bug 直到没有新的"） | ✗ | ✗ | ✓ `while new_findings:` |
| 交叉验证/对抗审查（N 怀疑者投票） | ✗ | ✗ | ✓ adversarial verify、judge panel 等编排模式 |
| 长任务断点恢复 | ✗ 断了重来 | △ 任务级重跑 | ✓ journal/resume：只重跑变化之后的部分 |
| 中途修改计划 | ✗ 推翻重说 | △ 增删卡片 | ✓ 改脚本 + resume，已完成部分零成本保留 |
| Leader 上下文承载力 | **瓶颈本身**（中间结果全流经它） | 不适用（无 leader） | **解耦**：产物落文件，Leader 只持有 goal + 概览 + 关键事件 |
| 跨运行时节点 | ✗ | ✓ 卡片级切运行时 | ✓ NodeRunner 预留（节点级混用） |

> **总结**：Chat 模式的能力上限 = Leader 一个脑子的上下文窗口；Multica 把任务拆给多个脑子但失去任务内结构和编排能力；Dynamic Workflow 让用户对话只对齐目标、确定性引擎执行任意复杂的编排——**复杂度上限从"一次对话能装下的"变成"一段代码能表达的"，而用户交互成本反而下降**。

## 2. 调研结论摘要


| 调研对象 | 关键结论 |
|---|---|
| **Claude Code Workflow 工具** | 确定性脚本编排：LLM 写 JS 脚本（`agent()`/`parallel()`/`pipeline()`），引擎执行。核心智慧：**leader 只写定义、拿结果、出错才介入**，不关心内部细节，只需总体 overview |
| **Multica** | 看板模式 + 多运行时（22+ CLI 守护进程注册/心跳/路由）。验证了痛点：任务粒度太细、任务内黑箱 |
| **Open Design** | ① 统一适配器的多运行时切换（`RuntimeAgentDef` + 三阶段检测 + `buildArgs`）② **文件系统作为单一事实来源**的上下文管理（agent cwd = 项目目录，输出即持久化，SQLite 只存元数据） |
| **Pantheon 现状** | 单进程全 asyncio；`call_agent` 子 agent 机制成熟（同步 await + 流式冒泡 + 防环 + fork_context）；`BackgroundTaskManager`、NATS streaming、LiveView 全部可复用。**缺失：DAG 编排层、外部 CLI 运行时**（现有"多运行时"只是多 LLM HTTP adapter） |
| **LangGraph** | 不集成。哲学不匹配（状态机+函数节点 vs 文件上下文+agent节点），依赖重；自研编排核心 ~1000 行（见 §4.1）。借鉴其 checkpointing 思路（对应 journal/resume） |

## 3. 核心设计决策

### 决策 1：Workflow 表示 — Leader 编写受限 Python 编排脚本（修订，原为结构化 schema）

**最终选择：代码表示**。Leader 编写受限 Python 编排脚本，Script Engine 沙箱执行。原因是经过深入权衡后，schema 方案的两大卖点被证伪：

**(a) 动态性是常态不是例外**。"审查所有模块""对每个发现做验证"等场景的节点数量运行时才知道。Schema 要支持需发明 map/condition/loop 节点——滑向"在 JSON 里写编程语言"的反模式；而这些控制流 Python 原生免费：

```python
# 动态 fan-out —— schema 模式难以表达，代码模式一行
modules = await node('列出 repo 所有模块', schema=LIST)
reviews = await parallel([lambda m=m: node(f'审查 {m}') for m in modules])
```

**(b) 可视化不依赖 schema —— trace 即图**。Claude Code 已验证：`meta.phases` 提供执行前骨架，每个 `node()` 调用运行时实时出现在画布上（执行 trace 树）。动态 fan-out 出 10 个节点，画布实时长出 10 个节点，比静态图更诚实。

**两种方案的"起步设计成本"不对称**：

> DSL 的设计工作量 ∝ 控制流种类（开放集合，每次撞墙都要演进 schema + 引擎 + 前端 + 兼容，订阅制）；
> 编排 API 的设计工作量 ∝ 原语数量（4-7 个，一次付清，买断制），控制流由 Python 免费提供。

Claude Code 的 API 表面至今只有 `agent/parallel/pipeline/phase/log/budget/workflow` 七个函数，却支撑了 adversarial verify、judge panel、loop-until-dry 等复杂模式，从未为控制流扩展过 API。

**逐项能力对照（修正后的最终评估）**：

| 能力 | Schema | 代码 |
|---|---|---|
| UI 展示（执行中/后） | 静态图+着色 | ✓ trace 图实时生长（所见即真实执行） |
| UI 展示（执行前） | ✓ 完整定义图 | △ phases 骨架 + AST 提取的预估图 + Leader 文字 plan |
| 编辑（均经 Leader） | patch ops | ✓ 改脚本 + resume（改代码是 Claude 最强能力） |
| skip/retry 节点 | 状态标记 | journal 层实现（注入空结果/失效条目 + resume） |
| 画布拖拽双向编辑 | ✓ 理论可行 | ✗ trace 是执行投影非源 |
| 动态 fan-out/条件/循环 | ✗ DSL 长尾债务 | ✓ 原生 |

**接受的代价（已确认）**：
- 放弃"画布拖拽双向编辑"——与产品定位（用户不懂节点概念，改 workflow 走对话→Leader）无冲突；未来若需要可补受限 schema 子集。
- 执行前用户看到的是 phases 骨架 + Leader 自然语言 plan（用户确认的本质是这段话，不是图）。
- 干预粒度从"patch 节点"变为"改脚本 + resume"，依赖 journal 前缀缓存让 resume 廉价。
- 需要受限 Python 执行环境（详见 §4.1 Script Engine）。

### 决策 2：职责边界 — Leader 轻量、Engine 自治（设计灵魂）

来自 Claude Code Workflow 的核心启发：**Leader 不是微观管理者**。

```
Leader（少量工具）               Workflow Engine（自治运行）
├─ 创建 workflow 定义            ├─ 编排脚本沙箱执行与自动调度
├─ 按需查询概况                  ├─ 节点并发执行（asyncio.gather）
└─ 干预（用户要求/出错时）        ├─ 基于文件的上下文传递（节点输入输出）
                                ├─ 错误检测/重试，失败才通知 Leader
Leader 不知道节点实时细节          └─ 直接推 UI 更新（NATS，不经 Leader）
```

Leader 仅在四种时机介入：
1. **创建时**：理解用户需求 → 生成 workflow 定义
2. **用户询问时**：调 status 工具 → 自然语言概述
3. **出错时**：Engine 推送错误事件 → Leader 告知用户、根据决策干预
4. **用户调整时**："跳过性能测试" → Leader 调干预工具

节点调度权在 Engine（自动档）：单节点手动启停破坏依赖语义；skip/retry 通过 journal 层实现（见决策 17）。Workflow 编辑 = Leader 修改脚本 + resume（前缀缓存使已执行部分零成本跳过）。

### 决策 3：节点执行 — 模式 A 起步 + NodeRunner 抽象

基于 pantheon-agents 执行模型调研（单进程全 asyncio，`Agent.run()` 为轻量协程）：

```python
class NodeRunner(ABC):
    """节点执行后端抽象。共用 execution_context_id（事件归属）、
    chunk/step hook（流式）、BackgroundTaskManager 风格（生命周期）。"""
    @abstractmethod
    async def run(self, node: WorkflowNode, inputs: dict, ctx: ExecutionContext) -> NodeResult: ...

class InProcessRunner(NodeRunner):   # Phase 1：复用 Agent.run()/call_agent 机制
class RemoteRunner(NodeRunner):      # 预留：RemoteAgent over NATS（骨架已存在）
class CLIRunner(NodeRunner):         # 预留：spawn claude/codex CLI 子进程（最后做）
```

三种模式可行性（调研结论）：
- **A 进程内 async**：可行性最高，框架天生如此，改动小。限制：单进程、无强隔离。
- **C RemoteAgent over NATS**：骨架已就绪（`AgentService.serve()` + `RemoteAgent.run()`），改动中。
- **B 外部 CLI 子进程**：不存在现成机制，改动大；是 open-design 式"真多运行时"的必经之路。

已确认：**Phase 1 只做 A，但第一天就定义 NodeRunner 接口**，B/C 作为可插拔后端按需实现。

注意：同一 Agent 实例并行跑多个 run 不安全（`_bg_manager` 等实例级状态）；Engine 为每个节点**new 独立 Agent 实例 + 独立 Memory**。

### 决策 4：上下文管理 — 基于文件（借鉴 open-design）

文件系统作为单一事实来源：

```
{chat workspace 或 project 目录}/.pantheon/workflows/<workflow_id>/   # 挂载规则见决策 15
├─ workflow.py         # Leader 生成的编排脚本（持久化，可版本化）
├─ journal.jsonl       # node() 调用记录（resume 前缀缓存依据）
├─ state.json          # 执行状态元数据
└─ context/            # 节点输出文件 = 下游节点输入
    ├─ inputs.json     # workflow_create 的 args 落盘
    └─ n{node_id}.json # 节点结果（journal result_ref 指向此处；文件名只用 node_id）
```

- 节点输出落文件 → Engine 把**文件路径**（而非内容）传给下游节点 → leader 与节点的 LLM 上下文都不膨胀。
- 天然支持：多运行时共享（任何 CLI 都能读文件）、Git 版本化、崩溃恢复、可审计。
- 与 chat/project 的 workspace 关系：见决策 15（isolated chat → workspace 下，project chat → project 目录下，含 meta.json 与 nodes/ 目录的完整结构）。

### 决策 5：信息架构 — Project → Task（Task-as-Chat）→ Workflow（修订，原为 Project+Thread）

**核心修订：不引入独立的 Thread 概念，而是把 chat 重新定义为"任务"（Task-as-Chat）。**

之前"Project + Thread"方案的问题：如果只是 project 下挂一堆 chat、chat 里多个 workflow 画布，结构上与旧 chat 列表无异——换汤不换药。旧模式的真正痛点不是"chat 多"，而是 **chat 是无状态、无目标、无终点的容器**（不知道哪个干完了、找东西靠回忆、永远不会"完成"只会被遗忘）。

**方案（方向 B）**：保留多 chat 结构，但每个 chat 获得任务语义：

```
Project "认证系统"
├─ 📋 任务视图（默认入口，按状态分组，替代 chat 时间列表）
│   ├─ 运行中:  "添加 2FA"（task chat，内含 workflow，2/5 节点）
│   ├─ 待决策:  "审查 PR #123"（节点失败，等用户）
│   └─ 已完成:  "实现 OAuth"
└─ 💬 自由讨论 chat（不带任务的普通对话，可选保留）
```

- **Task = chat + goal + status + 终态**：goal 由 Leader 在任务确立时提炼；status 聚合自其内 workflow 状态（运行中/待决策/已完成）；任务可完成、可归档。
- **每个任务一个 Leader**（独立上下文，天然隔离）——这正是最初愿景："和这个任务的 leader 进行对话且持续对话"。
- **UI 入口变化是关键**：从"按时间排的 chat 列表"变成"按状态分组的任务视图"——吃掉 Multica 看板的优点（任务可追踪），同时每个任务点进去不是黑箱而是 对话 + trace 画布。
- **改造成本低**：chat 级 project 元数据已存在（`memory.extra_data["project"]`），增加 `goal`/`task_status` 字段 + 前端将 ChatsScrollArea 替换为任务分组视图。

被否决的方向 A（一个 Project = 唯一主对话 + task thread 分叉）：多任务并行时所有事件挤回一个对话流，重新制造滚动查找问题；且需要改造 memory 模型（main + fork），成本高一个量级。其有价值的部分——项目级跨任务对话——以"自由讨论 chat + Phase 2 的 `workflow_status(scope='project')`"形式保留。

### 决策 6：UI — 嵌入式 Canvas + 侧边栏混合（复用 LiveView 架构）

```
┌──────────┬────────────────────────────────┬──────────────┐
│ Projects │ Chat Panel                     │ Workflows    │
│ /Tasks    │ User: 帮我构建登录系统          │ ● Workflow A │
│          │ AI: 好的，我会...               │   ⏸ (2/4)   │
│          │ [Workflow "OAuth" 执行中 ▼]    │ ○ Workflow B │
│          │ ┌────────────────────────────┐ │   (等待 A)   │
│          │ │ ✓Node1 → ⏸Node2 → □Node3  │ │              │
│          │ │ [暂停] [查看全屏]           │ │ Templates    │
│          │ └────────────────────────────┘ │              │
└──────────┴────────────────────────────────┴──────────────┘
```

- **默认嵌入式**：单 workflow 内嵌 chat 中（300-400px），**渲染执行 trace 图**（phases 骨架 + 实时生长的节点，含动态 fan-out），自动展开时机：创建时、状态变化、失败；完成后折叠为概览卡片。
- **侧边栏辅助**：多 workflow 时右侧列表 + 进度概览。
- **全屏按需**：复杂 workflow 点击展开独立视图。
- **Agent-to-UI 直接复用 Pantheon LiveView 模式**：Engine 发 `workflow.*` NATS 事件（协议清单见决策 12）→ 前端 workflowStore.applyEvent → 响应式渲染。**不引入新的 A2UI 框架**——LiveView 就是 A2UI 的成熟实现。
- 干预交互双通道：节点失败自动展开高亮，UI 提供 重试/跳过/修改/停止 按钮；用户也可纯对话表达（"跳过性能测试"→ Leader 调干预工具）。

### 决策 7：不集成 LangGraph

- 状态管理模型不匹配：LangGraph 状态在内存/DB、节点是函数；本设计是文件上下文、节点是 agent（LLM+工具+流式）。
- 依赖负担：强制引入 LangChain 生态。
- 自研编排核心很薄（~1000 行，见 §4.1 工程量评估），80% 基础设施已存在。
- 借鉴其 checkpointing 思路（对应本设计的 journal/resume）。

### 决策 8：Leader 工具集（5 个工具）

```python
workflow_create(goal, script, args=None, auto_start=True) -> dict
    # 校验（语法/AST/确定性）→ 创建（可选立即启动）
    # 返回 {workflow_id, phases, validation}；失败返回 {error, line} 供 Leader 重写
    # auto_start 默认 True；Leader 自判"复杂/高风险"时传 False，先把 phases 念给用户确认

workflow_status(workflow_id=None) -> dict
    # 不传 id：当前任务内所有 workflow 一行概览
    # 传 id：trace 摘要（phase 分组节点+状态）+ 错误摘要 + 产物路径
    # 原则：返回摘要不返回全量，节点输出内容永不直接进 Leader 上下文

workflow_get_output(workflow_id, node_id=None, summary_only=True) -> dict
    # 缺省取 workflow 最终返回值；summary_only 返回路径+首 N 行摘要

workflow_edit(workflow_id, script) -> dict
    # 接收完整修改后脚本（非 diff；脚本通常 <100 行，全量提交简单可靠）
    # 校验 → 暂停 → resume（journal 前缀缓存）
    # 返回 {resumed_from, cached_nodes, will_rerun} —— Leader 据此告知用户"保留了多少工作"

workflow_control(workflow_id, action, node_id=None) -> dict
    # action: pause | resume | cancel | skip_node | retry_node（node_id 寻址，非 label）
```

刻意不提供：单节点启停（破坏调度语义）、节点日志读取（属 UI 按需查看，不属 Leader 上下文）。

**归属校验（修 Codex Finding 2）**：5 个工具都从 ExecutionContext 解析出当前 chat_id，并在 engine 侧校验目标 workflow 的 `meta.chat_id` 与之匹配后才放行——workflow 存于共享 project 目录、仅靠 workflow_id 寻址会导致跨 chat 越权读取/控制。同一约束适用于 Phase 2 的 UI 端点（见决策 12 与耦合契约第 8 项）。

### 决策 9：节点 agent 的 system prompt 三层拼装

```
最终 prompt = ① Template base（预注册：角色 + 工具集 + 模型；generic 兜底）
            + ② Engine 协议层（统一注入，不可省略/覆盖）
            + ③ Leader instruction（node(instruction=...)，作为 user message）
```

②协议层内容：工作目录与输入文件位置、输出契约（写指定路径；有 schema 时最终回复必须是合法 JSON）、行为约束（"你的回复是数据不是给人看的消息"——Claude Code subagent 同款设计）。

约束（Phase 1）：
- 脚本**不可**配置 system_prompt（破坏②契约；"临时角色"在 instruction 里写"你扮演…"等价）和 toolsets（工具集=权限边界，由模板这个受控注册点管理）。
- 节点 agent 模板不含 call_agent——节点内禁止再派子 agent（避免不可见 agent 树重新制造黑箱）；嵌套编排走未来的 `workflow()` 子流程（Phase 3+）。
- 临时模板注册工具（Leader 对话中注册）留 Phase 2+。

### 决策 10：`node()` 签名与 Journal 失效语义

```python
async def node(
    instruction: str,           # 任务指令（参与哈希）
    *,
    template: str = "generic",  # 模板名（参与哈希）
    schema: dict | None = None, # 输出 JSON Schema，验证失败让节点重试（参与哈希）
    inputs: list[str] = (),     # 显式声明依赖的 context/ 文件（内容哈希参与失效判断）
    label: str = "",            # 纯显示文本（不参与哈希、不寻址、不进文件名）
    phase: str = "",            # 进度分组（不参与哈希）
    model: str | None = None,   # 模型覆盖（参与哈希）
    timeout: float | None = None,
) -> Any:                       # schema 时返回验证后 dict；否则返回文本
```

**节点寻址：稳定 node_id（不是 label）**。引擎在节点发起时分配 `node_id`（= 该 workflow 内单调递增的 seq，确定且唯一——脚本确定性保证调用顺序确定）。`node_id` 是 journal 条目、事件、`skip_node`/`retry_node`/UI 控制的**唯一寻址键**；`label` 仅作 UI 显示，可重复、可为空，绝不用于寻址或文件名（修 Codex Finding 3/4 同根问题：label 此前同时承担显示/文件名/寻址三职，拆分后只保留显示）。

Journal 规则：
- 条目 = `{node_id, key: sha256(instruction+template+schema+model+inputs内容哈希), label, status, result_ref, ...}`（node_id 寻址，key 判缓存命中）
- 命中 = **node_id 位置一致 + key 一致**（最长未变化前缀语义，Claude Code 同款）；第一个 miss 之后全部失效真跑。
- **`inputs` 内容哈希的意义**：用户/上游改了中间产物文件 → 声明依赖它的下游节点自动失效——文件上下文模式独有的正确性保障。
- `retry_node(node_id)` = 失效该条 + resume；`skip_node(node_id)` = 改写为 `{status: skipped, result: null}` + resume，脚本侧返回 None（Engine 注入的脚本编写指南写明 `filter(None, results)` 习惯用法）。
- **node_id 分配**：在发起时分配（脚本确定性 ⇒ asyncio task 创建顺序确定），完成时回填结果。

### 决策 11：节点执行接口（node → Agent 实例化）

每节点**新建** Agent 实例 + 独立 Memory（调研确认：同一 Agent 实例并行 run 不安全；Agent 构造轻量）。内核即 `call_agent` 的逻辑（new child memory + await run + execution_context_id 归属），区别仅是"从模板现场构造"替代"从 team 找已注册 agent"：

```python
agent = Agent(name=f"wf-{wf_id}-n{node_id}",          # node_id 唯一，label 不入名
              instructions=compose(template, ctx, node_call),  # 三层拼装
              model=..., toolsets=template.toolsets)
memory = Memory(name=..., file_path=ctx.workflow_dir/"nodes"/f"n{node_id}.jsonl")
response = await agent.run(..., context_variables={"workdir": ctx.chat_workdir,
                                                   "execution_context_id": ecid})
```

**文件名只用引擎生成的 `node_id`**（修 Codex Finding 3）：脚本可控的 `label` 在动态 fan-out 时可能含 `/`、`..` 等路径字符，绝不拼入文件名或路径组件；label 仅作 journal/事件里的显示字段。所有 workflow 目录内的写入路径，实现时必须断言解析后仍在 workflow 目录下（path confinement）。

**与 call_agent 的关键差异：节点消息不回流 Leader memory**（call_agent 回流是因为子 agent 输出属于对话；节点输出是数据，落 context/ 文件。回流会重新制造 Leader 上下文膨胀）。只有 NATS 事件去 UI、关键事件摘要去 Leader。

### 决策 12：UI 机制 — 只用 LiveView 管道，不用 LiveView 模式

把 LiveView 拆成两个东西：

| | LiveView 模式（agent 控制 UI） | LiveView 管道（NATS→store→组件） |
|---|---|---|
| 适用前提 | UI 状态 opaque，只有 agent 知道显示什么 | 任何"后端状态→前端实时反映" |
| Workflow | **不需要** | **直接复用** |

理由：**代码即 UI 的 source of truth**——`UI = f(脚本结构, journal 状态)` 是纯推导，没有任何人需要"决定"UI。事件由引擎在 `node()` 生命周期**确定性发出**：无 LLM 参与、零 token 成本、永不遗漏（agent 控 UI 模式中 agent 可能"忘记"更新）。

事件协议（挂现有 chat NATS stream，与 `live_view.*` 平级）：

```
workflow.created   {workflow_id, goal, phases}   # phases 来自脚本 meta 字面量（§6 补充决策 2）
workflow.node_started / node_finished                # 带 node_id（寻址）+ label（显示）
workflow.phase_changed / log / status / resumed
```

- 前端 workflowStore 与 liveViewStore 同构（applyEvent 模式）。
- **断线/刷新恢复**：重连拉 `workflow/<id>/state`（journal+state.json 重建完整 trace）再续订增量——继承 LiveView"状态在服务端"原则。该端点同样需校验 chat 归属（见耦合契约第 8 项）。
- **节点内部过程 Phase 1 不透传**（大 fan-out 的全量 chunk 流会冲垮前端）：画布节点只显示运行状态+完成摘要，点击节点按需拉取执行轨迹（nodes/ 下的 JSONL）；按需流式订阅（复用 execution_context_id 冒泡）列为可选增强。
- 节点 agent 自身的可视化需求（如生信图表）走 LiveView **工具**，与 workflow UI 正交共存。

### 决策 13：模块挂载 — 独立模块 + room 持有 + TeamPlugin 注入（与 Team 正交）

三个候选挂载点的裁决：
- ✗ **新 Team 类型**：Team 契约是"对话轮驱动"（run 一轮即返回），workflow 是跨轮次、跨重启的后台长任务；节点是用完即弃的临时 Agent，不是 team 成员。
- ✗ **写死 room.py**：room.py 已 2500+ 行，引擎（沙箱/journal/调度）与 chatroom 的耦合点很窄。
- ✓ **独立 `pantheon/workflow/` 模块**：

```
pantheon/workflow/        # 核心组成（完整文件布局见 phase1 实施计划）
├─ engine.py      # WorkflowEngine：session 管理、执行/resume/control
├─ sandbox.py     # 受限 exec 沙箱
├─ api.py         # node/parallel/pipeline/phase/log
├─ journal.py     # 前缀缓存
├─ runner.py      # NodeRunner / InProcessRunner
├─ templates.py   # 模板注册表 + prompt 三层拼装
├─ events.py      # workflow.* 事件定义
├─ toolset.py     # Leader 5 工具
└─ plugin.py      # WorkflowTeamPlugin

room.py 三处轻量接线：
├─ self.workflow_engine = WorkflowEngine(...)   # room 级单例
├─ sessions 归 engine 管（engine.sessions[chat_id]）
└─ 关键事件回调 → SteerQueue/input_queue 唤醒 Leader

Leader 工具注入：WorkflowTeamPlugin.get_toolsets(leader) → [WorkflowToolSet]
（现有 TeamPlugin 扩展点，零新机制；只注入 leader，sub agent 不给）
```

**与 Team 的关系一句话：正交，经 plugin 接壤。** Team 管对话层（轮次/steer/transfer），Engine 管执行层（脚本/节点/journal）。Team 资产复用放模板层：`templates.from_agent(...)` 快照配置（**复制配置，不共享实例**），Phase 2。

额外收益：engine 不依赖 chatroom → SDK/CLI/测试可脱离 chatroom 直接驱动 workflow。

### 决策 14：Chatroom 生命周期集成

约束（调研确认）：一个 chat 同时只有一个主 run（`room.py:2505`），新消息进 SteerQueue。Workflow 必须不占用主 run。

1. **创建**：Leader 主 run 中调 `workflow_create` → 引擎 `asyncio.create_task(...)` → 工具立即返回。主 run 结束，Leader 空闲，**用户可继续对话**。
2. **事件分流**：节点级细粒度事件只发 NATS → UI（不进 Leader）；关键事件（完成/失败/需决策）走 **SteerQueue/input_queue 注入**唤醒 Leader 一轮 run（复用 `BackgroundTaskManager` 完成通知的既有路径 `agent.py:2777`）。
3. **进程重启恢复**：状态全在文件（journal + state.json），重启后扫描 workflow 目录 → 恢复未完成 session 或标记 interrupted 待用户决定 resume。
4. **Leader 说话时 workflow 完成**：通知进队列，当前轮结束后下一轮处理——SteerQueue 语义天然支持。

### 决策 15：Memory 与 Project/Workspace 集成

基于现有机制的调研事实（project 两层结构、Memory opt-in 持久化、call_agent child memory 用完即弃、brain 目录先例）：

**(a) Leader memory — 不动，零新机制。** 走普通 chat memory（JSONL 持久化）。Leader 的"workflow 记忆"= 工具调用历史（create/status 的调用与返回摘要）+ 关键事件唤醒消息，已足够；细节永远从文件按需查。

**(b) 节点 memory — child memory 模式 + 落盘。** 与 call_agent 唯一差异是给 `file_path`（指向 `{workflow_dir}/nodes/n{node_id}.jsonl`，文件名只用 node_id），执行轨迹落盘：支撑节点详情按需查看、retry 携带上次失败轨迹、全程审计。

**(c) Workflow 目录挂载点**：

```
isolated chat → {workspace_path}/.pantheon/workflows/{workflow_id}/
project chat  → {project_dir}/.pantheon/workflows/{workflow_id}/

{workflow_id}/
├─ workflow.py / journal.jsonl / state.json
├─ meta.json          # chat_id, goal, created_at, status
├─ context/           # 节点产物（输入输出文件）
└─ nodes/             # 节点 memory JSONL（执行轨迹，文件名 n{node_id}.jsonl）
```

文件系统是 source of truth（meta.json 存 chat_id，扫目录即得 project 全部 workflow）；chat 侧仅在 `memory.extra_data` 加 `workflow_ids` 轻量缓存。

**(d) 关联语义**：Workflow 属于 task chat，task chat 属于 project；isolated chat 删除时 workflow 目录随 workspace `rmtree`（现有清理逻辑覆盖）。节点 workdir = 该 chat 的 workdir（与 Leader 同一视角），产物/journal 在 `.pantheon/workflows/` 不污染用户目录。跨 chat 可见性 Phase 1 不做（状态在文件系统 ⇒ Phase 2 加 `workflow_status(scope="project")` 零架构变更解锁）。`switch_project` 不影响运行中 workflow（持绝对路径）。

### 决策 16：编辑入口 — 语义化工具收口写路径（非直接编辑文本、非 LiveView patch）

三种 Leader 编辑方式的权衡：
- **直接编辑文本**（file edit 改脚本文件）：✗ 无验证边界、无事件同步、无法表达"已执行部分不可改"。
- **LiveView 式 deep-merge patch**：✗ 适合 opaque state；workflow 定义对 Engine 非 opaque（每处变更需语义验证与级联处理），patch 把意图压扁成状态差异，是反模式。
- **语义化工具（选定）**：操作经工具层验证（语法/AST 检查），非法即拒绝并返回原因供 Leader 重试；操作即事件，UI 同步自然。

> 类比：不让应用直接 patch 数据库文件，而是发 SQL——引擎需要在写入路径上做约束检查。

代码表示下的具体形态：Leader 提交**编辑后的脚本**（而非 patch ops），引擎做语法/确定性校验后 `resume`。脚本文件（workflow.py）仍持久化在 workflow 目录中，但它是 Engine 的输出和执行依据，不是 Leader 的直接编辑入口。

### 决策 17：干预机制 — Journal/Resume（前缀缓存）

借鉴 Claude Code `resumeFromRunId` 语义：
- 每次 `node()` 调用按**调用序 + 参数哈希**记录到 journal（`journal.jsonl`）。
- **resume**：重跑脚本，`node()` 命中"未变化前缀"直接返回缓存结果（瞬时），第一个 miss 之后真实执行。
- **skip 节点**（按 node_id）= 向 journal 注入空结果再 resume；**retry 节点**（按 node_id）= 失效该条 journal 再 resume。寻址用引擎生成的稳定 node_id，绝不用可重复的 label（修 Codex Finding 4：label 重复/为空会误伤其他节点，而 skip 注入 null + resume 是数据损坏路径）。
- **确定性约束**（resume 的前提）：脚本禁用 `time.time()`/`random`/裸 `datetime.now()`（受限 globals 不提供，违者 NameError）；时间戳等从 `args` 传入。

## 4. 架构总览

### 4.1 Workflow Script Engine（核心新组件）

```
┌─────────────────────────────────────────────────┐
│ Workflow Script Engine                          │
│ 1. 沙箱执行层   受限 exec 跑 Leader 写的脚本      │
│ 2. 编排 API    node()/parallel()/pipeline()/    │
│                phase()/log()                    │
│ 3. Journal 层  调用序+参数哈希 → resume 前缀缓存  │
│ 4. 事件层      API 调用 → NATS workflow.* 事件   │
│ 5. 调度层      并发信号量、budget、取消传播        │
└─────────────────────────────────────────────────┘
```

**沙箱选型**：Python 进程内受限 exec（复用 python toolset 的 `exec` 模式）+ `asyncio.Task` 包裹（取消/超时/异常）。

**威胁模型与边界（回应 Codex Finding 1）**：脚本作者是 Leader（自有对齐 LLM），不是用户直接写代码——与 Claude Code 同款（LLM 写脚本在本进程执行）。因此 Phase 1 维持进程内执行（推翻它会否定整个 Phase 1 的轻量性与基础设施复用）。但"作者可信"不等于"无需边界"——prompt injection 可能诱导 Leader 生成越界脚本，故进程内执行**必须**配齐以下硬边界，否则不得上线：
- **受限 globals**：只注入编排 API（node/parallel/pipeline/phase/log）与安全 builtins 白名单；不提供 `open`/`__import__`/`eval`/`exec`/`time`/`random`/`datetime`/`os`/`sys`。
- **无直接 IO**：脚本不能直接读写文件/网络；所有文件访问只经引擎的 context API，且路径被约束在 workflow 目录内（path confinement，配合决策 11 的 node_id 文件名）。
- **资源上限**：单 workflow 的最大节点数、总超时、并发上限（信号量）、可选 token 上限；超限即终止并标记 failed。
- **auto_start 的边界**：默认 True 仅适用于无副作用风险的脚本；spec §6 补充决策 1 的失败唤醒 + 决策 8 的 Leader 自判"复杂/高风险传 False" 共同构成确认机制。
- **测试**：sandbox 任务必须含越界/恶意脚本用例（路径穿越、禁用名、资源耗尽）。

**明确不在 Phase 1 防御范围**（残余风险，已知接受）：精心构造的 CPython 沙箱逃逸（`().__class__...` 一类）。需要抵御此类的节点 → 用 Phase 3 RemoteRunner（进程隔离）或 Phase 4 CLIRunner（子进程）承载。Phase 1 的边界目标是"防事故 + 防越界 IO + 防资源耗尽"，不是"防 CPython 逃逸"。

**编排 API（最小集）**（`node()` 完整签名与哈希规则见决策 10）：

```python
async def node(instruction, *, template="generic", schema=None, inputs=(),
               label="", phase="", model=None, timeout=None) -> Any:
    # 1. journal 查缓存（命中即返回）
    # 2. miss → NodeRunner.run()（Phase 1 = InProcessRunner → Agent.run()）
    # 3. schema 验证输出，失败让节点 agent 重试
    # 4. 写 journal + 发 NATS 事件

async def parallel(thunks) -> list   # asyncio.gather(return_exceptions=True)
async def pipeline(items, *stages)   # 每 item 独立串行链再 gather，无 barrier
def phase(title: str)                # 进度分组
def log(message: str)                # 用户可见进度行
```

**工程量评估**：~1000 行核心（沙箱 ~200 + API/NodeRunner 对接 ~400 + journal ~300 + 调度 ~150）+ 前端 trace 画布。80% 站在已有基础设施上（Agent.run、asyncio、NATS、LiveView 管道）；唯一新颖部分 journal/resume 以 Claude Code 为规格参考。

### 4.2 整体信息流

```
用户对话层          Leader Agent          Workflow Engine（自治）        UI
─────────          ────────────          ──────────────────          ─────────
User: "帮我审查 PR"
    │
    ▼
Leader 理解意图 ──▶ workflow_create({goal, script, args})
                        │
                        ▼
                   Script Engine 独立运行
                   ├─ 沙箱执行编排脚本
                   ├─ node() → NodeRunner ──────┐
                   ├─ 文件上下文传递              │ NATS workflow.* 事件
                   ├─ journal 记录/错误处理       ├──────────▶ Trace 画布
                   └─ 状态持久化                  │            （实时生长）
                        │                        │
              ┌─────────┼─────────┐
              │         │         │
          成功完成    节点失败   用户干预请求
              │         │         │
              ▼         ▼         ▼
         事件通知 Leader（仅关键事件）
              │
              ▼
        Leader 继续对话："审查完成，发现 3 个安全问题…"
```

事件通知 Leader 采用**混合模式**：关键事件（完成/失败/需决策）推送进 Leader 消息流；详情由 Leader 按需调 status 工具查询。

## 5. 实施路线

| 阶段 | 内容 |
|---|---|
| **Phase 1**（后端核心闭环，纯 pantheon-agents） | Workflow Script Engine（沙箱 + 编排 API + journal/resume + InProcessRunner + 文件上下文）；Leader 5 工具（TeamPlugin 注入）；`workflow.*` NATS 事件协议；节点 memory 落盘 |
| **Phase 2**（前端 + Task-as-Chat） | 前端嵌入式 trace 画布 + workflowStore、Task-as-Chat 任务分组视图、节点详情与错误干预 UI、多 workflow 侧边栏、模板扩充（analyzer/coder/researcher） |
| **Phase 3** | RemoteRunner（NATS 分布式节点）、`workflow()` 子流程嵌套、模板库/复用（含临时模板注册、`templates.from_agent`、`workflow_status(scope="project")`）、budget API |
| **Phase 4** | CLIRunner（claude/codex 外部运行时，含 open-design 式运行时检测/适配）、节点强隔离（worktree/容器） |

## 6. 补充决策（原待讨论项，已冻结）

1. **错误处理语义**：节点内部失败（LLM/工具异常、schema 验证失败）→ runner 内重试 1 次（schema 失败时附错误反馈），仍失败则 `node()` 抛 `NodeError`。脚本层自由处理：`parallel(return_exceptions=True)` 收集异常、或 try/except、或不处理。未捕获异常 → workflow 标记 failed + 关键事件唤醒 Leader（带失败节点 label 与原因摘要）。Leader 决定 retry_node / 改脚本 / cancel。
2. **执行前预览**：脚本必须以 `meta = {"goal": ..., "phases": [...]}` 字面量开头（Claude Code 同款约定），engine 用 AST 提取（非字面量则校验失败）。Phase 1 预览 = phases 骨架，不做调用点预估图。
3. **任务终态**：`task_status` 自动聚合（任一 workflow 运行中→运行中；任一待决策→待决策；全部完成→已完成）；归档（archived）由用户在 UI 显式操作，不自动。字段存 `memory.extra_data["task"] = {goal, archived}`，status 实时推导不落盘。
4. **初始模板集**：Phase 1 仅 `generic`；Phase 2 增 `analyzer`（file+shell 只读倾向）、`coder`（file+shell）、`researcher`（web+file）三个。
5. **Budget**：Phase 1 不提供 budget API；journal 已记 token_cost，`workflow_status` 返回累计值。Claude Code 式 `budget` 对象留 Phase 3。

## 6.1 耦合契约（与现有系统的全部接触面，封闭清单）

任何实现不得在此清单之外新增对现有代码的修改或依赖：

| # | 接触面 | 方向 | 内容 |
|---|---|---|---|
| 1 | `room.py` 接线（仅三处） | room→workflow | 构造 engine 单例；team plugins 加入 WorkflowTeamPlugin；关键事件回调注入 SteerQueue |
| 2 | `TeamPlugin` 接口 | workflow→team | `get_toolsets()` 向 leader 注入 WorkflowToolSet（只读使用现有接口） |
| 3 | `Agent` / `Memory` 公共 API | workflow→core | 节点执行只用 `Agent(...)` 构造 + `run()`；`Memory(file_path=...)` |
| 4 | NATS 事件命名空间 | workflow→stream | 仅发布 `workflow.*`（复用 `NATSStreamAdapter.publish`，不改 stream 代码） |
| 5 | `memory.extra_data` 键 | workflow→memory | 仅新增 `workflow_ids`、`task` 两个键 |
| 6 | 文件系统 | workflow 自有 | 仅写 `{base}/.pantheon/workflows/`；base 解析复用现有 workdir 规则 |
| 7 | 前端（Phase 2） | ui→stream | 订阅 `workflow.*`；新增独立 workflowStore + 组件，不改 liveViewStore |
| 8 | UI 查询/控制端点（Phase 2） | ui→workflow | 在 chatroom 现有 RPC/service 注册通道上**一处注册** workflow 查询与控制端点（state/node_trace/control），实现在 workflow 模块内；**每个端点必须经当前 session 解析 chat_id 并校验目标 workflow 的 `meta.chat_id` 归属后才放行**（Codex Finding 2） |

`pantheon/workflow/` 模块内部不 import chatroom（plugin.py 仅 import TeamPlugin 接口类型）；可脱离 chatroom 由 SDK/测试直接驱动。

## 7. 关键风险

- **Journal/resume 正确性**：确定性约束（禁时间/随机）靠受限 globals 强制；失效语义需仔细设计 → 以 Claude Code `resumeFromRunId` 为规格参考。
- **Leader 生成合法脚本的可靠性**：工具调用层做语法/AST/确定性校验，不合法返回原因让模型重试；写代码是 Claude 最强能力，风险低于自研 DSL。
- **单进程稳定性**（Phase 1）：节点崩溃可能影响全局 → NodeRunner 内 try/except 全包 + 后续用 RemoteRunner 隔离关键节点。
- **沙箱逃逸**：进程内 exec 不防 CPython 逃逸（明确接受，见 §4.1）；防线是受限 globals + 无 IO 直访 + 资源上限 + path confinement。需强隔离的节点走 Phase 3/4 的进程/子进程 runner。实现必须含越界/恶意脚本测试。
- **跨 chat 越权**（Codex Finding 2）：workflow 存共享 project 目录、仅 workflow_id 寻址会越权。所有工具与 UI 端点强制经 session 解析 chat_id 并校验 `meta.chat_id`；Phase 2 加跨 chat 拒绝测试。
- **label 路径注入 / 误寻址**（Codex Finding 3/4）：label 退为纯显示，文件名与 skip/retry/控制一律用引擎生成的 node_id；写入路径断言在 workflow 目录内。
- **trace 图信息密度**：大 fan-out（如 50 个并行节点）的画布渲染需要折叠/聚合策略 → UI 阶段设计。
