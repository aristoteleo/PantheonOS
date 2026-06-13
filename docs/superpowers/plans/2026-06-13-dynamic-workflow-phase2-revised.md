# Dynamic Workflow Phase 2 实施计划（修订版 v2，契约补丁 + UI）

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 或 executing-plans。
> **契约权威**：`pantheon-ui/.worktrees/dynamic-workflow-ui/20260613-dynamic-workflow-ui/ui-interaction-design.md` 的 **§A 权威契约**（v9，七轮 adversarial review 收敛）。本计划所有契约细节**以 §A 为准**，这里只给实现任务 + 测试清单。
> 替代：本文件修订 `2026-06-12-dynamic-workflow-phase2.md`（原计划的"静态 parser"路线已否决，发现/并发/状态契约已收紧）。

**Goal:** 用户能在 UI 看到 workflow trace 实时生长、创建即见声明骨架、失败可干预、断线可恢复，且全部满足 §A 的身份/作用域/级联/drift 契约。

**Architecture:** 先做 **Phase 1.5 后端契约补丁块**（触及 Phase 1 engine/events，是 UI 正确性的前提），再做 **Phase 2 前端**（workflowTrace store + trace 画布 + 节点详情 + 干预 + 蓝图预览）。

**Tech Stack:** Python（pantheon-agents：engine/events/api/runner/storage/templates）+ Vue 3/Pinia（pantheon-ui）。

---

## 阶段总览

```
Phase 1.5 后端契约补丁（pantheon-agents，可独立单测，必须先于 UI）
  C0  spike：端点注册通道
  C1  workflow_blueprint 声明解析 + create 校验      §A.8
  C2  事件 schema 增量 + slots_invalidated 持久      §A.2/§A.6
  C3  awaiting_intervention 状态 + revision           §A.4
  C4  control 串行化 + CAS + 统一 API + 作用域贯穿    §A.3/§A.9
  C5  workflow_list(chat_id) 鉴权 + 作用域            §A.3
  C6  no-preview 后端边界清洗                          §A.7
  C7  四查询/控制端点装配 + 归属校验                  §A.3
Phase 2 前端（pantheon-ui worktree feature/dynamic-workflow-ui）
  U1  workflowTrace store + 类型 + StreamingManager 接线
  U2  trace 画布（声明骨架 + 流式 reconcile）
  U3  节点详情 + 失败干预 bar
  U4  蓝图预览模式分流 + drift 显式态
  U5  多 workflow 侧边栏 + 模板扩充
  E2E 真实 LLM 端到端验收
```

> **依赖**：C1–C6 各自可独立单测；C7 依赖 C1/C5；U1 依赖 C2/C7；U2–U4 依赖 U1。C2 的 `slots_invalidated` 持久化与 C3 的 `awaiting_intervention` 有交互（hydrate replay 顺序，§A.6 步骤5），建议 C2、C3 同一人连续做。

---

# Phase 1.5 — 后端契约补丁块

文件根：`pantheon/workflow/`（engine.py / events.py / api.py / runner.py / storage.py / models.py / templates.py）；测试根：`tests/workflow/`。

### Task C0：spike — 端点注册通道（先做，解锁估时）
**Files:** 调研，不改码（产出结论写回本计划 C7）
- [ ] grep `proxy_toolset`、live_view 的前端调用路径、chatroom service/magique RPC 注册点
- [ ] 确认四个查询/控制端点挂哪条通道（magique RPC / chatroom HTTP service），UI 侧调用范本（参照 `pantheon-ui/src/network/http/chatroom.ts`）
- [ ] **产出**：端点注册方式 + 鉴权上下文如何拿到 session→owner/chat（C3/C4/C5/C7 依赖）

### Task C1：workflow_blueprint 声明解析 + create 校验（§A.8）
**Files:**
- Modify `pantheon/workflow/engine.py`（`extract_phases` 旁加 `extract_blueprint`；`create()` 加校验）
- Modify `pantheon/workflow/models.py`（`WorkflowMeta` 加 `preview: "full"|"none"`、`blueprint` 持久字段）
- Test `tests/workflow/test_blueprint.py`（新建）

**测试清单（§A.8 不变式 → 用例）：**
- [ ] `extract_blueprint` 对含 `meta.blueprint=[{slot_id,phase,label,kind,schema?}]` 的脚本返回结构化槽位列表；无 blueprint → `[]` + `preview=none`
- [ ] 纯字面量解析：`blueprint` 含非字面量（变量/调用）→ 安全返回 `[]`（不执行脚本体）
- [ ] create 校验：`slot_id` 重复 → 拒绝创建 + 结构化错误（指出重复 slot）
- [ ] create 校验：同 slot_id `kind` 混用（node/fanout）→ 拒绝
- [ ] create 校验：脚本 `node(slot="sX")` 引用未声明的 sX → 拒绝（结构化错误带脚本位置）
- [ ] create 校验：脚本含 `slot=` 但 `meta.blueprint` 缺失 → 拒绝（堵 orphan，§A.7）
- [ ] rejection 后：**无可被 `workflow_list` 发现的半成品**（journal 未落或已清）
- [ ] 校验通过 → `preview="full"` 写入 meta；正常创建

### Task C2：事件 schema 增量 + slots_invalidated 持久（§A.2/§A.6）
**Files:**
- Modify `pantheon/workflow/events.py`（`make_node_started/finished` 加 `slot_id/attempt/cascade_epoch`；`node_finished` 补 `phase`；新增 `make_slots_invalidated`）
- Modify `pantheon/workflow/api.py`（`node()` 接受 `slot=`，事件带 slot_id/attempt/cascade_epoch）
- Modify `pantheon/workflow/engine.py`（级联 `_first_miss` 后递增 `cascade_epoch` + 发 `slots_invalidated` + **落 journal**；`workflow_state` 重建按 §A.6 replay）
- Modify `pantheon/workflow/storage.py`（journal 持久 `slots_invalidated` 记录）
- Test `tests/workflow/test_events.py`、`test_engine_cascade.py`（新建）

**测试清单（§A.1/§A.2/§A.6 → 用例）：**
- [ ] `node_started/finished` 含 `slot_id`（可空）、`attempt`、`cascade_epoch`；`node_finished` 含 `phase`
- [ ] `node(slot="s4")` → 事件 `slot_id="s4"`；无 `slot=` → `slot_id=null`
- [ ] retry 同 slot → `attempt` 自增，旧 attempt 标 superseded；当前态取最高 attempt（§A.1）
- [ ] 级联重跑：`_first_miss` 后 `cascade_epoch` 递增；发出 `slots_invalidated{cascade_epoch, slot_ids=[下游全部]}`
- [ ] `slots_invalidated` **落 journal**（持久，非仅 NATS）
- [ ] **hydrate replay 顺序（§A.6 步骤5）**：`workflow_state` 重放 invalidation 先把下游置 superseded，再按 `(cascade_epoch,attempt)` 重建；**绝不跨 epoch 拼旧代**
- [ ] **复合场景**：invalidation 后、替代节点前到达 `awaiting_intervention` → 下游停 pending，不回填旧代（§A.5 优先级1）
- [ ] 零宽 fan-out：声明 fanout slot 但运行时无节点带该 slot_id → 槽位空（hydrate 后仍空，不隐身）

### Task C3：awaiting_intervention 状态 + revision（§A.4）
**Files:**
- Modify `pantheon/workflow/engine.py`（`_run` 失败落态：可干预→`awaiting_intervention`，不可干预→`failed`；"未结算"集合定义）
- Modify `pantheon/workflow/models.py`（`WorkflowMeta` 加 `revision`，状态迁移 +1）
- Test `tests/workflow/test_engine_state.py`（新建/扩展）

**测试清单（§A.4/§A.5 → 用例）：**
- [ ] 节点失败且可干预 → workflow 落 `awaiting_intervention`（持久，非 `failed`）
- [ ] 脚本错/无可重试节点 → 落 `failed`
- [ ] retry/skip→resume → 回 `running`；cancel → `cancelled`
- [ ] 未结算集合 = `{running,paused,interrupted,awaiting_intervention}`；`workflow_list` 返回此集合
- [ ] `revision` 每次 control/状态迁移单调 +1
- [ ] **drift 优先级（§A.5）**：awaiting_intervention 下游声明槽位 = blocked，**非 stale drift**
- [ ] **cancelled 后**下游未执行槽位 = unreachable（带 terminal_reason），**非 drift**（堵 Codex⑤）
- [ ] stale drift **仅**在自然 settle 到 completed（或 failed 且本应执行）后判定

### Task C4：control 串行化 + CAS + 统一 API + 作用域贯穿（§A.3）
**Files:**
- Modify `pantheon/workflow/engine.py`（`control` 包 per-workflow `asyncio.Lock`；入参 `expected_revision` + `expected_chat_id`；返回 `{accepted, status, revision}`）
- Modify `pantheon/workflow/toolset.py`（Leader 的 `workflow_control` 与 UI 同一 API，revision+chat 必填）
- Test `tests/workflow/test_control_concurrency.py`（新建）

**测试清单（§A.3 → 用例）：**
- [ ] `control` per-workflow 串行化：并发两条 control 不交错
- [ ] CAS：`expected_revision` 匹配 → 执行返回 `{accepted:true,status,revision+1}`
- [ ] CAS：过期 → `{accepted:false,status,revision}`（回传权威态，不报错）
- [ ] 混合竞态：tab-A retry n4(rev=5) + tab-B cancel(rev=5) → 先到执行、后到 accepted:false 回传权威态
- [ ] UI vs Leader 混合：两通道同一 API、同队列、同 CAS（堵 Codex②自然语言绕过）
- [ ] **作用域贯穿**：`control` 带 `expected_chat_id`，`meta.chat_id` 不符 → **服务端拒绝 403**（不靠前端）
- [ ] 跨 chat stale wf_id：B chat session 持 A chat wf_id 调 control → 403

### Task C5：workflow_list(chat_id) 鉴权 + 作用域（§A.3）
**Files:**
- Modify `pantheon/workflow/engine.py`（新增 `list_workflows(chat_id, session)`：扫 journal）
- Test `tests/workflow/test_discovery.py`（新建）

**测试清单（§A.3 → 用例）：**
- [ ] 返回该 chat 所有未结算 workflow（含 awaiting_intervention）；每项带 `chat_id`
- [ ] **鉴权**：owner 由 session 派生；`chat_id` 仅作作用域选择器，非凭证
- [ ] **作用域**：只返回 `meta.chat_id==入参 chat_id` 者；**不返回该 owner 其它 chat 的 workflow**（堵 Codex⑤跨 chat）
- [ ] 越权：session 不属于入参 chat_id → 403
- [ ] 进程重启后仍能从 journal 发现（不依赖内存 `_by_chat`）
- [ ] `workflow.created` 丢失场景：刷新后仍能发现在飞 workflow（持久 journal 为权威）

### Task C6：no-preview 后端边界清洗（§A.7）
**Files:**
- Modify `pantheon/workflow/events.py` 或发布边界（`preview=none` 时 `slot_id` 强制 null）
- Test `tests/workflow/test_no_preview.py`（新建）

**测试清单（§A.7 不变式 → 用例）：**
- [ ] `preview=none` 的 workflow：事件流 `slot_id` 一律 `null`（即便脚本写了 `node(slot=)`）
- [ ] `preview=none`：journal 落盘的 `slot_id` 一律 null
- [ ] `preview=none`：node_trace 不暴露 orphan slot_id
- [ ] 不变式由**后端边界**强制（不依赖前端读 `preview` flag）
- [ ] 回退路径（create 校验失败→重生成耗尽→no-preview）端到端：最终无外部可见 orphan slot_id

### Task C7：四查询/控制端点装配 + 归属校验（§A.3，依赖 C0/C1/C5）
**Files:**
- Create `pantheon/workflow/http.py`（或按 C0 结论挂注册点）
- Test `tests/workflow/test_endpoints.py`（新建）

**端点（全部先 session→owner 鉴权 + `expected_chat_id` 作用域校验）：**
- `workflow_blueprint(workflow_id, expected_chat_id)` → §A.8 声明骨架
- `workflow_state(workflow_id, expected_chat_id)` → §A.6 重放重建
- `workflow_node_trace(workflow_id, node_id, expected_chat_id)` → 读 nodes/n{id}.jsonl
- `control(workflow_id, action, node_id, expected_revision, expected_chat_id)` → §A.3
- `workflow_list(chat_id)` → C5

**测试清单：**
- [ ] 四端点跨 chat 拒绝：B chat session 访问 A chat 的 workflow_id → state/node_trace/control/blueprint 全 403
- [ ] `workflow_state` 返回值含重放后的当前态（cascade replay 已应用）
- [ ] `workflow_node_trace` 按 node_id 寻址（非 label/seq），404 区分越权(403)与无产物

---

# Phase 2 — 前端（pantheon-ui，worktree `feature/dynamic-workflow-ui`）

文件根：`src/`。命名空间 `trace/`（避开既有 `components/workflow/WorkflowCanvas.vue` 团队编辑器与 `TaskWorkflowCard`）。

### Task U1：workflowTrace store + 类型 + StreamingManager 接线
**Files:**
- Create `src/types/workflowTrace.ts`（镜像 §A.2 事件 + 重建态；按 slot_id/node_id/attempt/cascade_epoch 索引）
- Create `src/stores/workflowTrace.ts`（`applyEvent` / `hydrate`；范本 `src/stores/liveView.ts`）
- Modify `src/services/streaming/StreamingManager.ts`（`live_view.` 分支旁加 `workflow.` 分支，lazy-import；**不动 liveViewStore**）
- Create `src/network/http/workflow.ts`（四端点封装；范本 `network/http/chatroom.ts`）
- Test `src/stores/workflowTrace.test.ts`

**测试清单（§A.1/§A.5/§A.6 前端侧）：**
- [ ] `applyEvent`：按 `slot_id` reconcile 进声明槽位、`node_id` 定个体（非 (phase,label)）
- [ ] retry：同 slot 新 attempt → 当前态取最高 attempt，旧代可回看
- [ ] `slots_invalidated`：下游槽位置 superseded/pending，不显示旧代
- [ ] `hydrate(workflow_state)`：重放后状态与增量事件一致（断线恢复不拼旧代）
- [ ] 多 workflow 按 chat 索引；进入 chat 调 `workflow_list(chat_id)` 发现后逐个 hydrate
- [ ] drift 优先级：blocked/unreachable 不显示为 drift（§A.5）

### Task U2：trace 画布（声明骨架 + 流式 reconcile）
**Files:** Create `src/components/chat/trace/{WorkflowTraceCard,TraceGraph,TraceNode}.vue`
- 创建即按 `workflow_blueprint` 画声明骨架（phases + slot，fanout 画 `×N`）
- 流式：真实节点按 slot_id 填槽、fanout 就地展开；状态色（运行/完成/失败/跳过/blocked/unreachable）
- 折叠/展开（决策6）、大 fan-out >10 折叠计数组（§2.3，参考 Prefect Radar 布局）
- [ ] 组件测试（mock store）：骨架→实况、fanout 展开、drift ⚠ 标记

### Task U3：节点详情 + 失败干预 bar
**Files:** Create `src/components/chat/trace/{NodeDetailPanel,WorkflowInterventionBar}.vue`
- NodeDetailPanel：点节点 → `workflow_node_trace(wf_id, node_id, expected_chat_id)` → 右栏渲染（只读）
- InterventionBar：`awaiting_intervention` 时显示；按钮调 `control(..., expected_revision, expected_chat_id)`；以返回权威 `{status,revision}` reconcile
- [ ] 测试：control in-flight disabled（仅降抖）；control rejected 静默对齐权威态；403 丢弃 stale wf_id

### Task U4：蓝图预览模式分流 + drift 显式态
**Files:** Modify trace 组件 + store
- `preview=full` → 声明骨架预览；`preview=none` → 显式"无结构预览"+ 纯流式（无 slot/drift 逻辑）
- blueprint drift（stale slot / undeclared node）显式：卡片头"偏差 N 处"可展开
- [ ] 测试：两种 preview 模式分流；drift 三态显示；blocked/unreachable 不计 drift

### Task U5：多 workflow 侧边栏 + 模板扩充
**Files:** Create `src/components/chat/trace/WorkflowSidebar.vue`；后端 `templates.py` 增 analyzer/coder/researcher
- [ ] 测试：多 workflow 列表（进度/状态）；点击滚动到卡片

### E2E 验收（真实 LLM）
- [ ] 真实 LLM 跑含 parallel + fanout 的 workflow：创建即见骨架 → 流式生长 → 制造失败 → UI 干预 → 断线刷新恢复（不拼旧代）→ 跨 chat 隔离
- [ ] 复用 `20260613-workflow-e2e/` 风格 harness，扩展覆盖 §A 不变式

---

## 范围外（保持 Phase 2 边界）
- 节点编辑入口、依赖感知增量重算（D1/D2 → Phase 3）
- Task-as-Chat 任务分组视图、`ChatInfo` schema（D3 → Phase 2.5；`workflow_list` 已抢救最小子集）
- 静态 AST parser（已否决，§11.6 存档）

## 自检
- 每个 §A 小节都有对应 Task + 测试清单：A.1→C2/U1，A.2→C2，A.3→C4/C5/C7，A.4→C3，A.5→C3/U4，A.6→C2/U1，A.7→C6，A.8→C1 ✓
- 契约补丁（C1–C6）先于 UI；C7 装配后 UI 才有数据接口 ✓
- 跨 chat 越权在 C4/C5/C7 都有拒绝测试 ✓
