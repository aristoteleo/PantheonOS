# Fleet App 生命周期与钩子契约

日期：2026-09-16

状态：生命周期 v1 已实现，尚未部署；补充 [统一运行模型](fleet-app-runtime-plan.md)。下文先说明当前可用契约，再保留目标设计。服务路由及 Office 窗口绑定已实现，参见 [服务入口](fleet-app-gateway.md)；后台跨节点文档任务和全部旧 App 迁移仍在后续范围内。

## 当前 v1 契约

- 制品：最多 32 MiB 的 SHA-256 校验 tar，包含和 `app.json` 身份/版本一致的 `fleet.json`。不允许链接、路径逃逸或特殊文件。大依赖可使用 digest 固定的容器镜像，或由安装钩子下载、校验固定 SHA256 的原生发行包并解压到 INSTALL。
- Node 能力：`runtimes.app-lifecycle = "1"`；旧 Runner 不接收新协议。Windows 已实现节点锁、原子账本替换、原生进程服务和独立隐藏控制台停止信号；已通过交叉编译，Windows 真机验收仍待完成。macOS 已通过真实 Office 生命周期测试。
- 命令：`type=app_lifecycle, protocol=1`，`method=stage|submit|status|service`；submit 的 action 为 `install|uninstall|start|stop|reconcile`。
- Agent 入口：`desktop_app_install_on_node(app_id, node_id, revision, operation_id)` 准备已安装的不可变 App 版本；`fleet_app_lifecycle(node_id, action, digest, scope, generation, operation_id)` 查询或执行。
- 安装、启动不阻塞 RPC：先返回操作记录，再查询阶段。操作 ID 相同但参数不同会拒绝；实例动作必须提交当前 generation。
- 钩子：`before_install/after_install/before_start/after_start/before_stop/after_stop/before_uninstall/after_uninstall`，argv 而非 shell 字符串，超时 1–600 秒；stdout 是结构化 receipt。`after_uninstall` 在安装目录移除后执行，钩子代码应引用 `${PACKAGE}`。
- 进程钩子通过 stdin 接收用户 Fleet、node_id、instance_id、digest、阶段与目录。容器钩子可以指定 `component`，仅用于 after_start/before_stop，在拥有归属标签的运行中容器内执行。
- 组件身份通过 Runner 注入的 `PANTHEON_FLEET_ID/NODE_ID/INSTANCE_ID/APP_REVISION/INSTANCE_GENERATION` 环境变量传入，App manifest 不能伪造这些值。
- 原生进程服务声明 `ports: {"http": 0}`；Runner 分配 loopback 端口，通过 `PANTHEON_PORT_HTTP` 注入，并记录实例服务地址。进程必须绑定该端口并通过自身 readiness；不能复用碰巧占用该端口的其他服务。进程不声明固定端口或容器挂载。
- drain 期间保留正在运行的 generation 与已有连接；由 App 拒绝新会话，同时允许保存完成。Fleet 的新建窗口入口只开放 ready；已有连接可续期以完成保存，真正停止成功才推进 generation。
- before_stop 必须返回 `status=succeeded, safe_to_stop=true`；其 checkpoints 必须指向实例 DATA 内真实文件。失败保留资源并显示 stop_blocked，不自动强杀。
- Runner 重启后状态不冒充 ready，不重放结果未知的钩子；reconcile 查询真实进程/容器；仍存活时保留原 generation，允许重连完成保存后受控停止。卸载保留用户 DATA 和共享容器引擎。

容器组件必须声明以下依赖；不能在 App manifest 中提供任意引擎安装脚本或 Docker socket：

```json
{"dependencies":{"container_engine":{"provider":"docker","provision":"if-missing"}}}
```

`if-missing` 在安装目标节点上准备，`never` 仅使用已配置的可用引擎。首次选定的本机 Unix socket 被固定下来；离线时不偷偷切换 Docker context。自动准备目前支持 Linux amd64/root、有 mount/net namespace 的节点；其他节点需要可用的本地 Docker。安装后运行真实测试容器，只有 Docker CLI 或 daemon 能响应不算成功。默认引擎采用固定 checksum 的 Docker 27.5.1，后续版本升级须更新校验与兼容性测试。

上述嵌套容器模式在 Modal gVisor Workspace 中缺少所需 namespace；VM Workspace 实测通过。Hub 可用 `MODAL_WORKSPACE_VM=true` 创建 VM Workspace，`MODAL_WORKSPACE_VM_MEMORY_MB` 设置固定内存（默认 8192 MiB）。VM 内存按配置计费，不沿用 gVisor 的弹性上限；不能为安装依赖而悄悄删除已有 Workspace 或卷。

Office 默认采用原生进程包，已在 Debian 12 gVisor Workspace 镜像中验证，无需 Docker 或 VM。新建 gVisor 和 VM Workspace 都把 `PANTHEON_FLEET_STATE_DIR` 指到挂载卷中的 `/workspace/.pantheon/fleet-node`。现有 Workspace 不会被自动替换；生产卷上的 Office 数据库/缓存持久化仍需专项验收。

Office 原生制品另支持 `darwin-arm64`、`darwin-amd64` 和 `windows-amd64`，分别声明节点平台。Mac/Windows 的安装钩子准备私有 Node、Community 文档服务和官方原生转换内核，不使用 Linux 二进制或本地 Docker。所有平台需要 Python 3.11–3.13；Windows 另需 Visual C++ v14 x64 运行库。Apple Silicon 已验证安装、启动、停止、重启、卸载保留数据，以及 DOCX/XLSX/PPTX 编辑保存；Intel Mac 和 Windows 已实现但尚未真机验收。平台制品与新版 Runner 发布前，现有节点不会自动切换。

## 1. 基本决定

提供统一的 `install`、`uninstall`、`start`、`stop` 操作，App 可声明生命周期钩子。操作的状态机、顺序、权限、持久记录、资源登记和超时由 Fleet Runtime/Runner 负责；钩子只完成 App 特有的准备、初始化、持久化和清理。

钩子不是完整的运行管理器。普通 Python App 不应为了接入 Fleet 再写一套进程管理代码；容器 App 不应在钩子里自行运行一个无法登记的 `docker run -d`。标准安装、前台进程、容器、端口、挂载和健康检查由运行器按声明处理。

所有节点生命周期钩子在**目标 Node**执行，绑定同一份已校验的 App revision。Hub 负责协调；浏览器只提交请求、显示进度和连接窗口，不负责执行或等待长任务结束。

## 2. 操作范围

- 用户在 Store 添加 App：记录用户希望使用的 App/revision，不立即在整个 Fleet 安装一遍。
- `install`：在指定 Node 为一个 App revision 准备安装；初次打开可以自动触发。
- `start`：创建或复用一个明确 scope 的实例，引用已经安装好的 revision。
- `stop`：停止该实例，不卸载 App，不删除文档或持久工作副本。
- `uninstall`：移除指定 Node 的安装引用，先处理使用它的实例，默认保留用户数据。其他节点的安装不受影响。
- 从用户应用库移除：取消该用户的安装意图，并按节点执行卸载。离线节点显示待处理，不能冒充已经清理完成。

节点上的代码和镜像缓存可以按 digest 共享；用户状态、凭证和可写环境隔离。卸载只能释放自己的引用，共享缓存由 Runner 在引用归零后回收。清除用户数据是单独的 `purge_data` 操作，不属于普通 uninstall。

窗口的 `attach/detach` 与实例生命周期分开。关闭最后一个窗口可启动保温计时，但不能直接触发卸载或杀进程。

## 3. 标准流程与可选钩子

App 没有特殊需求时可以不声明钩子。下面的 post 钩子都位于操作提交成功之前：失败时该操作不得被报告为成功。

| 操作 | Runner 执行顺序 | App 可选钩子的用途 |
| --- | --- | --- |
| install | 校验权限/运行条件 → 拉取并校验制品 → 隔离暂存目录 → `before_install` → 标准依赖准备 → `after_install` → 安装验证 → 原子标记 installed | 生成节点相关资源、验证 App 自己的依赖 |
| start | 取得实例锁 → ensure installed → 准备状态目录/凭证/挂载/端口 → `before_start` → 运行器启动组件 → readiness → `after_start` → 再次 readiness → 发布服务并标记 ready | 初始化实例配置，恢复检查点，必要的启动后初始化 |
| stop | 标记 draining、暂停新会话与新修改 → `before_stop` → 验证可安全停止 → 运行器请求优雅退出 → 确认进程/容器退出 → `after_stop` → 清理临时资源与路由 → 标记 stopped | 等待任务、持久化会话；退出后做离线清理 |
| uninstall | 设置 removal 意图并拒绝新 start → 检查依赖和活跃引用 → 按策略完成 stop → `before_uninstall` → 释放本安装的资源引用 → `after_uninstall` → 标记 absent | 移除 App 专有注册、可重建索引及安装资源 |

安装钩子的代码来自校验后的包或受控工具镜像，不可能在下载代码之前执行。安装钩子不能假定该 App 的运行依赖已经准备好；需要声明自身可用的执行器。

`after_stop` 和 `after_uninstall` 不能依赖已经停止的 App 服务；Runner 保留执行它们所需的工具和钩子制品直到操作结束。它们不负责保存唯一的用户数据副本。停止后清理失败应显示“进程已退出，清理待重试”；卸载清理失败则显示“清理未完成”，资源不做无记录的遗留。

start 中的服务先拥有内部地址，供组件通信和探测使用；只有完成就绪检查才对普通调用方开放。进程运行两秒或容器状态为 Running，不代表 App ready。健康探测至少验证实际 RPC 或 HTTP 服务及所需组件。

目标模型中，多组件 App 声明启动依赖图；Runtime 按图启动并验证，停止时按相反顺序。当前原生 Office 对 Fleet 暴露一个前台进程组件，其专用 supervisor 管理内部 API、文档引擎和代理，确保引擎退出前保存回调 API 仍存活；它不通过安装钩子启动脱离管理的服务。

## 4. 停止之前的 drain 协议

`before_stop` 是可观测、可恢复的收尾步骤，不是立即发送 kill 信号。

1. 拒绝新会话和新编辑操作，允许现有保存回调、会话刷新及数据持久化请求继续。
2. App 返回仍在执行的任务及保存/检查点进度。请求停止立即返回 operation ID，UI 不同步卡住。
3. 对需要前端同步的实时编辑会话，请求已连接窗口刷新缓冲区，并记录其确认。未获确认不能宣称所有编辑已保存。
4. App 返回 `safe_to_stop`、持久检查点/任务引用和未完成源文件写回列表；Runner 按 manifest 的持久化契约验证这些引用。
5. 只有满足安全停止条件，才请求优雅退出。达到等待上限仍未满足时，状态为 stop blocked/failed，暴露原因、重试和单独的强制停止操作。

“工作副本已经持久化”和“已写回原文件”是不同结果。源文件节点离线时，如果 App 的检查点完整且支持恢复，可以安全停止并保留待写回任务；UI 必须仍显示待写回。若唯一编辑内容仍在易失内存里，则不能走这条路径。

停止组件之前，应把仍需完成的任务交给独立持久任务执行器，或等待它们完成。不能把任务保存在将要被杀掉的进程内存里然后报告为后台继续。

强制停止记录数据风险和审计结果，不能伪装成正常保存完成。Runner 只有确认实际进程/容器退出后才报告 stopped；节点失联期间状态是 unknown/unreachable。断电和系统强杀时不能保证任何 stop 钩子会执行，数据安全必须依靠正常运行期间的持续持久化与恢复，而不是仅靠关闭钩子。

## 5. 幂等、并发与恢复

每个操作有稳定的 `operation_id`。上下文包括用户、Node、安装/实例 ID、App digest、执行阶段、尝试次数、deadline、取消信号和工作/状态目录。敏感值通过范围受限的凭证句柄注入，不拼接进命令行或日志。

- 对同一安装或实例串行执行会冲突的生命周期操作。正在 uninstall 的安装不能接受新 start；stop 需要先取消/收尾正在进行的 start。
- 执行前、执行后都写入持久步骤记录，节点重启后恢复或核对。协调层使用 generation/租约避免旧请求改变新实例；无法联系旧节点时不能推断其进程已经停止。
- 钩子必须幂等，使用 Runner 提供的 ensure/release 资源接口；资源预先分配稳定 ID，并标注 owner 与 operation。重试先检查实际资源，避免重复容器、重复迁移或重复注册。
- 不承诺跨网络、进程和数据库的 exactly-once。执行成功但回执丢失时，结果是待核对；不是自动重复一次不可逆操作。
- 每个钩子声明 timeout；总操作有独立 deadline。重试有次数/退避限制，可恢复错误才自动重试。节点离线和权限错误不能靠循环重试伪装成进度。
- 制品目录、资源句柄、进度和错误结构化记录；日志脱敏，用户可从 Store/Fleet 查看具体失败步骤。

Runner 要维护资源账本。部分失败时，只释放当前操作新建并拥有的资源，保留既有安装、共享依赖、活跃实例和用户状态。钩子不能通过一个全局路径删除其他版本或其他用户的数据。

## 6. 执行边界

钩子属于已安装且明确授权执行的 App 代码，不来自文档内容、Agent 临时字符串或用户输入插值。manifest 声明包内入口、参数 schema、运行器、所需访问能力和超时；标准命令采用 argv，不隐式通过 shell 拼接输入。

install/uninstall 钩子的写权限只覆盖本次安装的暂存/资源区域，不获得持久文档目录的删除权。实例 start/stop 钩子按 App 声明访问自己的状态和经授权的文件引用。包或镜像本身保持只读。

机器级运行环境（容器引擎、系统服务等）是节点准备职责。App install 钩子不能静默使用 sudo、安装系统守护进程、读取全机凭证或修改共享机器配置。需要额外能力的节点明确显示不满足条件；按节点运维流程补齐后再运行 App。

安全检查、鉴权、配额和制品验证由 Runtime 强制执行，不能被 App 的 `before_*` 返回值绕过。

## 7. 升级不是 uninstall + install

升级先安装新 revision，旧实例继续绑定原 revision。新实例就绪后才切换新启动的默认版本。旧实例停止后，旧安装才能按引用数回收。

状态 schema 的变化使用单独的、版本化 `migrate` 流程：声明前后版本，取得状态写锁，创建可恢复检查点，执行并验证。代码可以回滚并不意味着数据 schema 可回滚；不兼容时恢复检查点或明确禁止降级，不能在 start 中反复运行未标记的数据库迁移。

恢复可以由 `before_start` 读取检查点完成；首次版本暂不新增多个同义恢复钩子。若以后引入专门的 recovery handler，也必须沿用同一操作记录和租约协议。

## 8. Office 的映射

| 阶段 | Office 的行为 |
| --- | --- |
| install | 在目标云端/所选 Node 拉取固定的修改版 ONLYOFFICE 和会话组件，准备依赖；验证渐进加载、File 菜单与内容 API 的构建标识 |
| start | 准备持久状态、实例凭证和内部连接；按依赖启动组件；恢复已有会话；验证编辑器、转换和回调路径；发布实例入口 |
| stop | 停止接收新文档，刷新活跃编辑会话，持久化工作副本和待保存任务；达到安全条件后停组件，保留完整资源及恢复信息 |
| uninstall | 移除本节点安装引用及专有临时资源；保留文档、未写回工作副本和恢复索引；其他 App 使用的镜像层不直接删除 |

Office 的 before_stop 不能只依赖原始文件写回成功：Mac 可能离线。完整且可恢复的工作副本可以满足安全停止，但不代表已经写回 Mac。

如果浏览器中还有尚未同步到 Document Server 的内容，必须报告这一限制；已知服务端检查点不能被描述为包含所有客户端编辑。普通关闭窗口应先完成客户端同步，再允许后台接管后续保存。

用户打开 Office 不需要运行本地 Docker。上述安装和生命周期全部在调度得到的合格节点上执行，与其他 App 使用相同协议。

## 9. 钩子描述与回执

实现前先冻结一个版本化 schema，至少包含：

- Hook 名称、包内入口、运行器、timeout、输入输出协议版本。
- 操作 ID、实例/安装身份、精确 digest、当前阶段和 deadline。
- 输入通过结构化 stdin/RPC 传递；返回结果采用结构化回执。
- 回执区分 succeeded / retryable failure / permanent failure / waiting / unknown outcome。
- 返回进度、明确错误码及 Runner 认可的资源/检查点/任务引用；不能仅靠退出码 0 证明服务已就绪或数据已持久化。

App 回报意图和专有状态，Runner 负责核验它可核验的事实，例如子进程退出、资源归属、服务健康和持久对象存在。对于 App 内部内容正确性，使用 App 自带验证和集成测试，不能承诺通用 Runner 自动理解每种文档格式。

## 10. 实施与验收

先交付操作状态机、持久账本、幂等和执行适配器，再接入钩子。不要先开放任意 install/start shell 字符串，再逐项补保护。

第一批验证必须包含：

1. install 中途失败，原有版本仍可使用，暂存资源可恢复/回收。
2. 两个客户端重复 start，只存在一个目标实例；ready 前不可接收普通调用。
3. Runner 在钩子执行后、回执前重启，不重复创建资源或反复迁移数据。
4. stop 期间仍允许必要保存回调；数据未持久化或进程未退出时不报告 stopped。
5. 卸载与启动并发、共享依赖和离线节点都具有明确结果。
6. uninstall 默认保留用户数据；失败钩子不会清除其他安装的资源。
7. 升级失败保留旧实例；不兼容 schema 不自动降级。
8. Office 源节点离线时保留可恢复工作副本，并准确显示待写回。
9. 正常关闭后恢复 Office 会话，以及模拟进程崩溃后从最近持久检查点恢复。

已完成代码与隔离测试，包括真实 Office 容器及原生 Office 的安装、启动、安全停止、重启和卸载。原生 gVisor 测试还验证了 DOCX/XLSX/PPTX 打开与内容接口，以及 Word 修改后的原生保存结果。测试保留工作副本，并清理测试自己创建的运行资源；未对用户线上实例执行这些动作。


## Portable Python dependency cache

The portable App adapter keeps environments under the node's Fleet state root,
in `apps/<fleet>/python-environments/<key>`, outside individual artifact installs.
The key includes App identity, requirements, Python executable/version/ABI and
platform. Changing only UI or backend code reuses the completed environment;
changing dependencies or the interpreter prepares an independent environment.
Apps must pin their requirements when reproducible dependency upgrades matter.

An OS file lock serializes creation of each environment across installers. Failed
or interrupted installs have no ready marker and are rebuilt on retry. A ready
environment is never updated in place. Local/editable/URL/nested requirements and
pip options are conservatively isolated per artifact. A node-local pip download
cache still avoids fetching identical wheels repeatedly. The artifact binds to
its environment atomically; the launcher retains the venv executable path on
Linux, macOS and Windows without requiring Windows symlink privileges.

Uninstall removes the artifact's binding, not a shared environment used by other
revisions. Automatic cache garbage collection is not yet implemented; do not
remove an environment referenced by an installed/running App. Environment caches
use local node storage. On Linux, if the Fleet ledger is on a network mount
(including a Modal 9p volume), dependencies and the pip cache use a private,
per-user/node directory on the local temporary disk. A local Python interpreter
is selected so its standard library is not imported over the network either.
App data and the ledger remain on their durable volume. `before_start` checks
the environment and rebuilds it when a replacement sandbox has lost that local
cache, before backend readiness begins. A genuinely new dependency set still
requires its first installation.

The Desktop serializes launches only for the same App on the same requested
node. Different nodes and unrelated Apps can prepare concurrently; an offline or
slow Mac install cannot stall a Workspace launch in the browser.

## Idle backend policy

Fleet usage protocol 1 tracks each mounted window with a generation-bound,
renewable lease (30 second renewals, 5 minute expiry after a lost client). Closing
the last window releases its lease. The node waits 60 seconds after the final
window and active RPC/HTTP tunnel finish, then queues the normal safe stop hooks.
It checks usage again when that operation runs. An app that refuses to checkpoint
remains `stop_blocked`, reachable for saving; it is never force-killed or uninstalled.

Fleet's App instances inspector exposes **Keep running in background**, persisted
per installed instance across stops and Runner restarts. Turn it on for servers
or long background work that should outlive UI connections. Reopening a stopped
app starts the same installation with retained data and a new generation.

Older clients do not silently enable idle stopping: a successful window lease or
an explicit background-policy change opts an instance in. Old nodes remain usable
and show an upgrade hint. Node-owned usage prevents one browser from stopping an
app still used by another. Minimized windows and other Spaces retain their leases.
A crashed/closed browser is reclaimed after lease expiry plus the idle grace.
