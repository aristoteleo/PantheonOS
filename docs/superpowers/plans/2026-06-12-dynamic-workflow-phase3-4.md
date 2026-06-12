# Dynamic Workflow Phase 3/4 实施计划（架构级）

> 设计依据：`docs/specs/2026-06-12-dynamic-workflow-design.md`（决策 3/9/13、§6 补充决策 5、§6.1 耦合契约）
> 前置：Phase 1/2 完成。本文档为架构级计划——Phase 3/4 距今较远，任务列表在启动时由执行 agent 按本文档展开为 bite-sized 步骤（参照 phase1 计划的格式），避免对远期细节过度规划而失效。
> **不变式（所有任务必须遵守）**：耦合契约 §6.1 封闭清单不扩大；NodeRunner 接口不破坏（新 runner 只增不改）；journal/事件协议向后兼容（只加字段不删改）。

## Phase 3：分布式与复用

### 3.1 RemoteRunner（RemoteAgent over NATS）
- 新文件 `pantheon/workflow/runner_remote.py`：实现 `NodeRunner.run()`，把节点路由为 `RemoteAgent.run()` 调用（骨架已存在：`agent.py` AgentService/RemoteAgent，NATS worker per-request 并发已验证）。
- `node()` 增 `runner: str = "inprocess"` 参数（参与 journal 哈希）；模板可声明默认 runner。
- 需解决（实现时设计）：远端节点的文件上下文访问（方案候选：NATS object store 传文件 / 共享文件系统约定，按部署形态选）；execution_context_id 跨服务透传；远端失败的超时与重试。
- 验收：同一脚本混跑 inprocess + remote 节点，journal/resume 语义不变。

### 3.2 `workflow()` 子流程
- api.py 增 `workflow(script_or_name, args)`：子 workflow 在父目录下建嵌套目录（`{parent}/subflows/{id}/`），共享父的并发信号量与取消传播；嵌套限一层（Claude Code 同款约束）。
- journal：子流程在父 journal 中占一条（key = 子脚本哈希 + args），子内部有自己的 journal——父 resume 时子整体命中或整体重跑。
- UI：trace 画布子流程节点可展开（▸ 分组，事件带 parent_workflow_id 字段——协议加字段，向后兼容）。

### 3.3 模板库与复用
- 临时模板注册工具（Leader 对话中注册，存 `{project}/.pantheon/workflows/templates/`）；`templates.from_agent(team_agent)` 快照 team 成员配置（复制不共享实例，决策 13）。
- `workflow_status(scope="project")`：扫 project 目录聚合（决策 15d 已预留，零架构变更）。
- workflow 脚本另存为命名模板 + `workflow_create(from_template=...)`。

### 3.4 Budget API
- api 工厂注入 `budget` 对象（total/spent()/remaining()，Claude Code 同款）；total 从 `workflow_create(budget=...)` 传入；spent 聚合 journal token_cost。超限时 `node()` 抛 BudgetExceeded。

## Phase 4：外部运行时与强隔离

### 4.1 CLIRunner（claude/codex CLI 子进程）
- 新文件 `pantheon/workflow/runner_cli.py` + `pantheon/workflow/cli_runtimes/`（运行时定义注册表）。
- 借鉴 open-design 三件套（spec §2 调研）：
  - `RuntimeDef`（id/bin/version_args/build_args/stream_format）静态注册 claude、codex 两个起步；
  - 检测：PATH 扫描 + version 探测（启动时一次，缓存）；
  - 执行：`asyncio.create_subprocess_exec`（参照 shell toolset 子进程模式），cwd = 节点 workdir，prompt 经 stdin 或 argv，stream-json 输出解析为统一 NodeResult + 进度事件。
- 文件上下文天然兼容（任何 CLI 都能读写 context/ 目录——决策 4 的预期收益兑现点）。
- prompt 三层拼装的适配：外部 CLI 无 system prompt 注入口时，协议层并入 user prompt 文本。
- 验收：脚本中 `node(..., runner="cli:claude")` 真实调用 claude CLI 完成一个节点。

### 4.2 节点强隔离
- worktree 隔离：节点声明 `isolation="worktree"` 时，runner 在 git worktree 中执行（借鉴 Claude Code isolation 参数语义）；非 git 目录降级为 tmp 目录拷贝。
- 进程级资源限制（CLIRunner 天然进程隔离；InProcessRunner 不做强隔离——需要隔离的节点用 CLI/Remote runner，决策 3 既定取舍）。

## 决策点提示（执行 agent 启动 Phase 3/4 时需先确认的事项）
1. RemoteRunner 文件传递方案（取决于届时部署形态：单机多进程 vs 跨机）。
2. CLIRunner 的权限模式默认值（bypassPermissions 等价物——安全取舍需用户拍板）。
3. 子流程 UI 展开交互细节（Phase 2 画布实现后再定）。
