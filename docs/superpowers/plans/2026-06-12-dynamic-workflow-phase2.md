# Dynamic Workflow Phase 2 实施计划（前端可视化 + Task-as-Chat）

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 或 executing-plans。
> 设计依据：`docs/specs/2026-06-12-dynamic-workflow-design.md`（决策 5/6/12、§6 补充决策 3/4、§6.1 耦合契约第 7 条）
> 前置条件：Phase 1 完成（`workflow.*` 事件协议已固定，见 events.py）
> 仓库：主体在 pantheon-ui；后端少量补充在 pantheon-agents

**Goal:** 用户能在 UI 看到 workflow trace 画布实时生长、按任务视图管理 chat、在节点失败时通过 UI 或对话干预。

**Architecture:** 前端新增独立 workflowStore（applyEvent 模式，与 liveViewStore 同构但不共享代码）+ trace 画布组件嵌入 chat 时间线；chat 列表替换为按 task_status 分组的任务视图。后端补 3 个轻量 HTTP/RPC 端点（状态重建、节点轨迹、归档）与 3 个模板。

**Tech Stack:** Vue 3 + Pinia + 现有 NATS streaming 订阅管道；后端复用 Phase 1 模块。

---

## 文件结构

```
pantheon-ui/src/
├─ stores/workflow.ts                 # workflowStore：事件应用、状态重建、多 workflow 索引
├─ types/workflow.ts                  # 事件与状态类型（镜像后端 events.py 字段）
├─ components/chat/workflow/
│  ├─ WorkflowTraceCard.vue           # 嵌入时间线的 trace 画布（默认形态，可折叠）
│  ├─ TraceGraph.vue                  # phases 骨架 + 节点图渲染（含大 fan-out 折叠聚合）
│  ├─ TraceNode.vue                   # 单节点：状态色/label/耗时/产物链接
│  ├─ NodeDetailPanel.vue             # 点击节点：按需拉取执行轨迹（nodes/ JSONL）
│  └─ WorkflowInterventionBar.vue     # 失败时：重试/跳过/取消按钮（调后端 control）
├─ components/layout/left_sidebar/
│  └─ TasksScrollArea.vue             # 任务分组视图（替换 ChatsScrollArea 的使用处）
└─ components/chat/workflow/WorkflowSidebar.vue  # 多 workflow 时右侧列表

pantheon-agents/pantheon/workflow/
├─ http.py（或挂 chatroom 现有 RPC 注册处）  # 状态重建/节点轨迹/控制 三端点
└─ templates.py                        # 增 analyzer/coder/researcher（§6 补充决策 4）
```

## 任务列表

### Task 1: 后端查询/控制端点
- Create `pantheon/workflow/http.py`，按 chatroom 现有 service/RPC 注册模式（先 grep `proxy_toolset` 与 live_view 的前端调用路径，沿用同一通道）暴露：
  - **每个端点先经当前 session 解析 chat_id，再校验目标 workflow 的 `meta.chat_id` 归属，不匹配即 403**（Codex Finding 2：workflow 存共享 project 目录，仅凭 workflow_id 会跨 chat 越权）
  - `workflow_state(workflow_id)` — 从 journal+state.json 重建完整 trace（决策 12 断线恢复）
  - `workflow_node_trace(workflow_id, node_id)` — 读 nodes/n{node_id}.jsonl 返回执行轨迹（按 node_id，非 label/seq）
  - `workflow_control_ui(workflow_id, action, node_id)` — UI 干预按钮直达 engine.control（与 Leader 工具同一入口，同样校验归属，不绕过）
- [ ] 失败测试（含跨 chat 拒绝：用 B chat 的 session 访问 A chat 的 workflow_id，state/node_trace/control 三端点都应 403）→ 实现 → 通过 → commit `feat(workflow): add UI query/control endpoints with ownership checks`

### Task 2: 前端类型与 workflowStore
- Create `types/workflow.ts`（字段与后端 events.py 一一对应）与 `stores/workflow.ts`：
  - `applyEvent(event)` 处理事件（created/node_started/node_finished/phase_changed/log/status/resumed）；节点以 `node_id` 索引，label 仅显示
  - `hydrate(workflowId)`：重连/刷新时调 workflow_state 重建后续订增量
  - 按 chat_id 索引多 workflow
- 订阅接线：在现有 streaming 订阅处（参照 liveView 事件分发位置）把 `workflow.*` 路由进 store——**不改 liveViewStore**（耦合契约 7）
- [ ] store 单测（事件序列→状态断言、hydrate 合并）→ commit `feat(ui): add workflow store and event types`

### Task 3: Trace 画布组件
- TraceGraph/TraceNode/WorkflowTraceCard：phases 列骨架 + 节点按事件实时出现；状态色（运行/完成/失败/跳过）；完成后折叠为概览卡片（决策 6）
- 大 fan-out 聚合：同 phase 同前缀 label 节点 >10 时折叠为计数组（spec §7 风险项）
- 嵌入 chat 时间线：参照 SubAgentCard/TaskWorkflowCard 的时间线插入模式
- [ ] 组件测试（mock store）→ commit `feat(ui): add workflow trace canvas`

### Task 4: 节点详情与干预
- NodeDetailPanel（点击节点拉 node_trace，渲染消息/工具调用列表）
- WorkflowInterventionBar（workflow failed 时显示在卡片顶部：重试该节点/跳过/取消，调 Task 1 端点）
- [ ] 测试 → commit `feat(ui): node detail and intervention UI`

### Task 5: Task-as-Chat 任务视图
- 后端：`new_chat`/chat 列表响应补充 `task` 元数据（goal 由 Leader 首次 workflow_create 时写入 extra_data；status 按 §6 补充决策 3 聚合推导，在列表接口实时计算）
- 前端：TasksScrollArea 按 状态分组（运行中/待决策/已完成/自由讨论[无 task 元数据的 chat]/已归档）；归档操作
- [ ] 测试（分组逻辑、归档）→ commit `feat: task-as-chat grouped view`

### Task 6: 多 workflow 侧边栏 + 模板扩充
- WorkflowSidebar：当前 chat 的 workflow 列表（进度/状态，点击滚动到对应卡片）
- 后端 templates.py 增 analyzer/coder/researcher
- [ ] 测试 → commit → 端到端手动验收：真实 LLM 跑一个含 parallel 的 workflow，验证画布生长、失败干预、任务分组

## 范围外
跨任务可见（`workflow_status(scope="project")`）、临时模板注册工具——列入 Phase 3 候选；全屏画布编辑器（永不做，决策 1 已放弃双向编辑）。
