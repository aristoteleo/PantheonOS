# Dynamic Workflow Phase 1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 设计依据：`docs/specs/2026-06-12-dynamic-workflow-design.md`（17 项决策已冻结）
> 按用户要求，本计划不含代码细节；实现者执行每个任务时参照 spec 对应决策编号。

**Goal:** 落地 Phase 1 核心闭环——Workflow Script Engine（沙箱 + 编排 API + journal/resume + InProcessRunner + 文件上下文）、Leader 5 工具（TeamPlugin 注入）、`workflow.*` NATS 事件协议、节点 memory 落盘。

**Architecture:** 独立 `pantheon/workflow/` 模块（决策 13），ChatRoom 持有 engine 单例，Leader 经 TeamPlugin 获得工具；节点执行复用 `Agent.run()`（每节点新建实例 + 独立 Memory，决策 11）；状态全部落文件（决策 4/15）；UI 事件走现有 NATS chat stream（决策 12）。

**Tech Stack:** Python 3.10+ / asyncio / 现有 Agent·ToolSet·TeamPlugin·NATSStreamAdapter·Memory 基础设施 / pytest（asyncio_mode=auto）

**验证方式:** 每任务 TDD（先写失败测试 → 实现 → 通过 → commit）。测试放 `tests/workflow/`。运行：`uv run pytest tests/workflow/ -v`。

---

## 文件结构总览

```
pantheon/workflow/
├─ __init__.py        # 导出 WorkflowEngine、WorkflowToolSet、WorkflowTeamPlugin
├─ models.py          # 数据模型：WorkflowMeta/State/NodeCall/NodeResult/JournalEntry
├─ storage.py         # workflow 目录布局与读写（meta/state/journal/context/nodes）
├─ journal.py         # Journal：append/前缀匹配/失效/skip 注入（决策 10/17）
├─ sandbox.py         # 受限 exec 沙箱：受限 globals、确定性约束（决策 1/17）
├─ api.py             # 编排 API：node/parallel/pipeline/phase/log（决策 10）
├─ templates.py       # 节点模板注册表 + prompt 三层拼装（决策 9）
├─ runner.py          # NodeRunner 抽象 + InProcessRunner（决策 3/11）
├─ events.py          # workflow.* 事件构造与发布（决策 12）
├─ engine.py          # WorkflowEngine：session 管理、执行/resume/control（决策 2/14）
├─ toolset.py         # WorkflowToolSet：Leader 5 工具（决策 8）
└─ plugin.py          # WorkflowTeamPlugin：向 leader 注入工具（决策 13）

tests/workflow/       # 每模块一个测试文件，命名 test_<module>.py
```

依赖方向：`engine → {sandbox, api, journal, runner, events, storage}`；`api → {journal, runner, events}`；`toolset/plugin → engine`。models/storage 无内部依赖。

---

### Task 1: 数据模型与目录存储（models.py + storage.py）

**Files:** Create `pantheon/workflow/models.py`、`pantheon/workflow/storage.py`、`tests/workflow/test_storage.py`

要点（决策 4/15）：
- models：`WorkflowMeta`（chat_id/goal/created_at/status）、`WorkflowState`（运行状态、progress）、`NodeCall`（node() 的全参数 + seq + key 哈希）、`NodeResult`、`JournalEntry`。哈希规则按决策 10（instruction+template+schema+model+inputs 内容哈希；label/phase 不参与）。
- storage：给定 base_dir（isolated workspace 或 project 目录）解析/创建 `{base}/.pantheon/workflows/{workflow_id}/` 布局（workflow.py、meta.json、state.json、journal.jsonl、context/、nodes/）；提供扫描目录列出 workflow 的函数。

- [ ] Step 1: 写失败测试：目录创建与布局、meta/state 读写往返、NodeCall key 哈希稳定性（同参同哈希、inputs 文件内容变化则哈希变化）、扫描列表
- [ ] Step 2: 运行确认失败（模块不存在）
- [ ] Step 3: 实现 models + storage 使测试通过
- [ ] Step 4: `uv run pytest tests/workflow/test_storage.py -v` 全绿
- [ ] Step 5: commit `feat(workflow): add models and directory storage`

### Task 2: Journal 层（journal.py）

**Files:** Create `pantheon/workflow/journal.py`、`tests/workflow/test_journal.py`

要点（决策 10/17）：
- append-only JSONL；加载后支持：按 (seq位置, key) 前缀匹配查缓存、第一个 miss 后整体失效、按 label 失效单条（retry）、按 label 改写为 skipped/null（skip）。
- 并发 seq：发起时分配、完成时回填结果（同 spec"并发 seq"条目）。

- [ ] Step 1: 失败测试：前缀命中/参数变化即 miss 且后续全失效/retry 失效语义/skip 注入后读出 None/崩溃后从文件恢复
- [ ] Step 2: 确认失败
- [ ] Step 3: 实现
- [ ] Step 4: 测试通过
- [ ] Step 5: commit `feat(workflow): add journal with prefix-cache resume semantics`

### Task 3: 沙箱执行层（sandbox.py）

**Files:** Create `pantheon/workflow/sandbox.py`、`tests/workflow/test_sandbox.py`

要点（决策 1/17，参照 `pantheon/toolsets/python/python_interpreter.py` 的 exec 模式）：
- async 执行入口：接收脚本文本 + 注入的 API dict + args → 在受限 globals 下编译执行（脚本顶层为 async 函数体或 module 模式，实现时选定一种并在脚本编写指南中固定）。
- 受限 globals：安全 builtins 白名单；不提供 `open`/`__import__`/`time`/`random`/`datetime`（确定性约束，违者 NameError）。
- 语法/确定性校验函数（AST 检查禁用名），供 toolset 在 create/edit 时调用；错误返回行号信息。
- 整体包裹 asyncio.Task：支持取消、超时、异常捕获并结构化返回。

- [ ] Step 1: 失败测试：正常脚本执行并返回值/禁用名触发 NameError/语法错误返回行号/取消传播/await 注入的 API 可用
- [ ] Step 2: 确认失败
- [ ] Step 3: 实现
- [ ] Step 4: 测试通过
- [ ] Step 5: commit `feat(workflow): add restricted script sandbox`

### Task 4: 模板与 prompt 拼装（templates.py）

**Files:** Create `pantheon/workflow/templates.py`、`tests/workflow/test_templates.py`

要点（决策 9）：
- `WorkflowTemplate`（name/base_prompt/toolsets/model）+ 注册表；内置 `generic` 模板（仅基本工具使用规范，Phase 1 初始模板集只有 generic，Phase 2 扩展见 spec §6 补充决策 4）。
- 三层拼装函数：template base + Engine 协议层（工作目录、输入文件位置、输出契约、"回复是数据"约束——文案集中常量化）+ Leader instruction 作为 user message 单独返回（不进 system prompt）。

- [ ] Step 1: 失败测试：generic 注册存在/拼装结果包含协议层关键句/未知模板报错/instruction 不混入 system prompt
- [ ] Step 2: 确认失败
- [ ] Step 3: 实现
- [ ] Step 4: 测试通过
- [ ] Step 5: commit `feat(workflow): add node templates and prompt composition`

### Task 5: NodeRunner 与 InProcessRunner（runner.py）

**Files:** Create `pantheon/workflow/runner.py`、`tests/workflow/test_runner.py`

要点（决策 3/11）：
- `NodeRunner` ABC（`run(node_call, ctx) -> NodeResult`）。
- `InProcessRunner`：每节点新建 Agent（参照 `team/pantheon.py` call_agent 内核：新 Memory + `use_memory=False` + await run）；节点 Memory 带 file_path 落 `nodes/{seq}_{label}.jsonl`（决策 15b）；context_variables 注入 workdir 与 execution_context_id；**不挂父级 step/chunk hook**（节点消息不回流 Leader，决策 11）；schema 输出验证失败时带错误反馈重试节点（重试次数常量，默认 1 次，语义见 spec §6 补充决策 1）。
- 测试用 fake/stub Agent（不真调 LLM；参照 tests/ 现有 mock 模式，先读 `tests/conftest.py`）。

- [ ] Step 1: 失败测试：runner 构造 Agent 参数正确（instructions 含协议层、model/toolsets 来自模板）/memory 落盘到 nodes/ 目录/schema 验证失败触发重试后成功/超时返回失败 NodeResult
- [ ] Step 2: 确认失败
- [ ] Step 3: 实现
- [ ] Step 4: 测试通过
- [ ] Step 5: commit `feat(workflow): add NodeRunner abstraction and InProcessRunner`

### Task 6: 事件层（events.py）

**Files:** Create `pantheon/workflow/events.py`、`tests/workflow/test_events.py`

要点（决策 12）：
- 事件构造器：`workflow.created/node_started/node_finished/phase_changed/log/status/resumed`（字段按 spec 决策 12 清单）。
- 发布器：包装 `NATSStreamAdapter.publish(chat_id, type, data)`（参照 `chatroom/stream.py` 与 live_view toolset 的 `_publish` 惰性初始化模式）；NATS 不可用时降级为 no-op + 日志（测试环境无 NATS）。

- [ ] Step 1: 失败测试：事件字段完备性/发布器调用 adapter（mock）/无 NATS 时不抛异常
- [ ] Step 2: 确认失败
- [ ] Step 3: 实现
- [ ] Step 4: 测试通过
- [ ] Step 5: commit `feat(workflow): add workflow.* event protocol`

### Task 7: 编排 API（api.py）

**Files:** Create `pantheon/workflow/api.py`、`tests/workflow/test_api.py`

要点（决策 10）：
- `node()`：journal 查缓存 → miss 时 runner 执行 → 写 journal → 全程发事件；skip 条目返回 None。签名按决策 10 完整版。
- `parallel(thunks)`：gather + return_exceptions；`pipeline(items, *stages)`：每 item 独立串行链（无 barrier）；`phase(title)`/`log(msg)`：状态记录 + 事件。
- 并发上限信号量（常量，默认 8）。
- API 工厂：按 workflow 上下文（journal/runner/emitter/sem）构造绑定好的 API dict，供沙箱注入。

- [ ] Step 1: 失败测试（runner 用 stub）：node 缓存命中不调 runner/miss 调用并记 journal/parallel 并发与异常隔离/pipeline 无 barrier 语义（慢 item 不阻塞快 item 的后续 stage）/事件序列正确/信号量限并发
- [ ] Step 2: 确认失败
- [ ] Step 3: 实现
- [ ] Step 4: 测试通过
- [ ] Step 5: commit `feat(workflow): add orchestration API (node/parallel/pipeline/phase/log)`

### Task 8: WorkflowEngine（engine.py）

**Files:** Create `pantheon/workflow/engine.py`、`tests/workflow/test_engine.py`

要点（决策 2/14/16）：
- session 管理：`sessions[chat_id] -> list[WorkflowSession]`；每 session 持 asyncio.Task + 状态。
- `create(chat_id, goal, script, args, base_dir, auto_start)`：校验（调 sandbox 校验函数）→ 建目录写 meta/script → 可选启动；返回 workflow_id + phases（AST 提取脚本开头的 meta 字面量；非字面量则校验失败——spec §6 补充决策 2）。
- 执行：组装 API → 沙箱跑脚本 → 完成/失败更新 state + 发 status 事件 + 触发**关键事件回调**（由 room 接线到 SteerQueue，engine 只暴露 callback 注册，决策 14）。
- `resume(workflow_id, new_script=None)`：替换脚本（可选）→ 重跑（journal 前缀缓存生效）→ 返回 cached/will_rerun 统计。
- `control(workflow_id, action, node_label)`：pause（取消 Task，state 标记）/resume/cancel/skip_node/retry_node（journal 操作 + resume）。
- 重启恢复：扫描目录把未完成 workflow 标记 interrupted（不自动重跑）。

- [ ] Step 1: 失败测试（脚本用纯 stub node）：create 校验失败返回错误行号/正常执行落盘全套文件/resume 缓存统计正确/skip+retry 行为/cancel 后 state 正确/interrupted 恢复标记/关键事件回调被触发
- [ ] Step 2: 确认失败
- [ ] Step 3: 实现
- [ ] Step 4: 测试通过
- [ ] Step 5: commit `feat(workflow): add WorkflowEngine with session lifecycle`

### Task 9: Leader 工具集（toolset.py）

**Files:** Create `pantheon/workflow/toolset.py`、`tests/workflow/test_toolset.py`

要点（决策 8，ToolSet 模式参照 `pantheon/toolset.py` 与 live_view toolset）：
- 5 工具按决策 8 签名：`workflow_create/status/get_output/edit/control`；全部委托 engine；chat_id 从 ExecutionContext/context_variables 取（参照 live_view 的 chat_id 解析）。
- 返回值守则：摘要不返回全量；get_output 默认 summary_only（路径 + 首 N 行）。

- [ ] Step 1: 失败测试：5 工具注册可见/create 透传并返回 phases/status 双模式（带/不带 id）/get_output 摘要截断/control 各 action 路由正确
- [ ] Step 2: 确认失败
- [ ] Step 3: 实现
- [ ] Step 4: 测试通过
- [ ] Step 5: commit `feat(workflow): add leader WorkflowToolSet`

### Task 10: TeamPlugin 与 ChatRoom 接线（plugin.py + room.py）

**Files:** Create `pantheon/workflow/plugin.py`；Modify `pantheon/chatroom/room.py`（三处轻量接线，决策 13/14）；Create `tests/workflow/test_plugin_integration.py`

要点：
- `WorkflowTeamPlugin(TeamPlugin)`：`get_toolsets(team)` 仅向 leader/entry agent 返回 WorkflowToolSet（参照 `team/plugin.py` 接口与现有 plugin 注册方式——先 grep 现有 plugin 如 task_system 的注入路径）。
- room.py：① 构造 engine 单例（base_dir 解析复用现有 workdir 逻辑：isolated 用 workspace_path，否则 project 根）；② 把 WorkflowTeamPlugin 加入 team 创建的 plugins；③ 注册关键事件回调 → 注入对应 chat 的 SteerQueue/input_queue（参照 `_on_bg_task_complete_for_loop` 模式）。
- chat 删除清理：isolated workspace rmtree 已覆盖（决策 15d），验证即可，不加新逻辑。

- [ ] Step 1: 失败测试：team setup 后 leader 拥有 5 个 workflow 工具且 sub agent 没有/engine 关键事件能注入消息到运行中 chat（集成测试，参照 tests/test_chatroom_* 的 room fixture 模式）
- [ ] Step 2: 确认失败
- [ ] Step 3: 实现（room.py 改动保持最小，新逻辑都在 plugin/engine 侧）
- [ ] Step 4: 测试通过 + 跑一遍现有 chatroom 测试确认无回归：`uv run pytest tests/test_chatroom_start.py tests/test_chatroom_memory_e2e.py -v`
- [ ] Step 5: commit `feat(workflow): wire engine into chatroom via TeamPlugin`

### Task 11: 端到端冒烟 + 脚本编写指南

**Files:** Create `tests/workflow/test_e2e.py`、`pantheon/workflow/SCRIPT_GUIDE.md`（注入给 Leader 的脚本编写说明素材，含 meta.phases 约定、确定性规则、`filter(None, results)` 习惯用法）

- [ ] Step 1: 写端到端测试：stub runner 下，完整脚本（含 parallel fan-out + phase）经 engine.create 执行 → 验证 journal/context/nodes/state 文件齐全、事件序列完整、resume 改脚本尾部后前缀全命中
- [ ] Step 2: 确认失败 → 修复发现的集成问题 → 通过
- [ ] Step 3: 写 SCRIPT_GUIDE.md（Leader system prompt 注入素材，由 plugin 在 get_toolsets 时附带或写入工具描述——实现时选其一）
- [ ] Step 4: 全量测试：`uv run pytest tests/workflow/ -v` 全绿
- [ ] Step 5: commit `feat(workflow): e2e smoke test and script guide`

---

## 任务依赖

```
T1 storage ─┬─ T2 journal ─┐
            ├─ T3 sandbox ─┤
            ├─ T4 templates ─ T5 runner ─┤
            └─ T6 events ──┴─ T7 api ─ T8 engine ─ T9 toolset ─ T10 plugin/room ─ T11 e2e
```

T2/T3/T4/T6 可并行；T5 依赖 T4；T7 依赖 T2/T5/T6；之后串行。

## 范围外（Phase 2+，勿实现）

前端 trace 画布与 workflowStore（pantheon-ui）、Task-as-Chat 视图、RemoteRunner/CLIRunner、`workflow()` 子流程、临时模板注册、budget API、跨任务可见性。前端事件协议已由 Task 6 固定，UI 可后续独立开发。
