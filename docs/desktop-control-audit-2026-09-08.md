# Desktop Agent 控制验收（2026-09-07—08）

本轮实际测试了仓库内 20 个提供应用内容控制能力的应用，以及 `agent-view` 自定义应用机制。测试使用独立工作区、独立浏览器和独立 Linux/Modal 沙箱；没有操作用户正在使用的窗口或挂载用户 Volume。这里的“通过”指表中列出的操作已实际执行并核对结果，不代表穷举所有格式、参数或第三方扩展。

## 验证方法

- DOM/科学应用：真实 AppInstanceResolver → Go fleet → NATS → DesktopToolSet → Atrium bridge → 实际浏览器组件。只替换测试页面的网络接入适配器，没有模拟应用或工具成功结果。
- Browser/QuPath：独立 Linux Xpra 运行时，使用公开工具、真实 Chrome/QuPath/JVM、X11 输入、原生窗口和截图。
- ImageJ：真实 ImJoy/ImageJA.JS；另经完整 NATS 链路导入本地 PNG，运行宏并解码截图核对像素。
- 真实 Pantheon Agent：模型自主调用 8 次 Desktop 工具，在指定的 Terminal/Text/PDF 窗口完成操作；独立读取终端缓冲区、已保存文件、PDF 页码和另一个未被修改的编辑器核验。

## 应用覆盖

| 应用 | 实际操作及核验 | 结果 |
| --- | --- | --- |
| Browser | 指定窗口 DOM 控制、输入；15 次中英文/希腊字母/emoji 原生输入；右键菜单寻址/截图/Reload；按 page/window ID 关闭及最后 tab 的 Xpra lost-window；其他窗口不变 | 通过 |
| QuPath | 同 GUI 的 worker/fx 脚本、图像身份 guard、ROI/幂等、长任务查询、原生缩放/菜单、Save/Cancel、GeoJSON/TSV/qpdata 导出、保存后正常关闭 | 通过，字符限制见下文 |
| ImageJ | 打开/选图、宏创建和修改图像、错图 guard、失败/abort、截图；导入 640×360 PNG，修改像素后截图读到准确的 `[73,73,73,255]` | 通过 |
| Files | 初始目录加载、读取/选择/导航、打开 PDF 等待实际可用；删除取消/失败；确认期间切目录仍只删除原目录目标 | 通过 |
| Terminal | 同一 PTY 执行、回读真实输出、Ctrl-C 后继续执行、截图 | 通过 |
| Image Viewer | 实际图像加载/尺寸、缩放、坏 URL 拒绝、错误及空状态后恢复原图、截图 | 通过 |
| Text Viewer | 读取编辑器、替换/expectedText guard、实际保存字节、加载错误后原窗口恢复、截图 | 通过 |
| PDF Viewer | 真实 PDF.js 渲染、翻页/缩放、提取页文本、非法页码拒绝、截图 | 通过 |
| Jupyter/Notebook | 同一 Notebook 添加和执行单元格，输出 42 在原窗口可见，read_cells 回读、截图；旧 poll 不得提前确认新编辑 | 通过 |
| Cytoscape | circle 布局 | 通过 |
| PhyloTree | radial 布局 | 通过 |
| MSA | 隐藏 ruler | 通过 |
| RDKit | 显示原子索引 | 通过 |
| Mol* | protein → DNA 实际结构加载 | 通过 |
| Gosling | genomic track 颜色改变 | 通过 |
| IGV | 自定义 FASTA reference 的 locus 导航 | 通过 |
| Spatial3D | 3D cluster → 2D gene coloring | 通过 |
| Volume3D | ISO → MIP、brightness | 通过 |
| Vitessce | heatmap 面板切换，截图含真实 WebGL 散点 | 通过 |
| Viv | channel 颜色/显隐；关闭后重新显示 Channels 面板 | 通过 |

11 个科学应用均额外检查了：同应用第二窗口不变、实际画面变化、工具截图、非法输入返回失败，以及原窗口恢复。使用小型真实 H5AD/TIFF/FASTA/PDB 等数据，经真正后端转换与 HTTP 数据服务加载。

`agent-view` 使用公开 `desktop_open(module=...)` 实际创建两个独立应用窗口；修改第一个计数、读取/截图、非法 action 参数拒绝、第二窗口不变均通过。其元数据已修正为支持通用 state bridge，不虚构动态 action 名称。

Agent、Settings、Store、Interfaces 目前没有应用内容 bridge：实际打开后，读取/调用明确返回不支持，通用 `$close` 正常。这些不计入“应用内容控制通过”。原生 `xwindow` 是子窗口容器，其菜单/对话框目标在 Browser/QuPath 用例中覆盖。

截图中的 Counter、NEON DESCENT 是用户工作区自定义应用。本轮验证了它们使用的通用机制，但未取得这两个实际包的 workspace，不能将通用 fixture 的通过算作这两个应用通过。

## 修复的实际问题

1. 通用 bridge 未等待异步状态/渲染，错误被丢弃；action 完成后立即 read 会读到旧状态。现在以真实完成 ACK 返回，并序列化状态与 action。初始加载失败保留窗口 ID，可修复同一窗口。
2. Files/Terminal 截图入口不可用；现代 CSS 颜色和 opaque 自定义 iframe 导致截图失败；Vitessce WebGL 散点在截图中丢失。修复通用捕获，必要渲染依赖同源固定版本提供。
3. 浏览器关闭窗口 ID 实际无效却返回成功、右键菜单无法寻址、Unicode 临时映射恢复过早丢字；修复精确目标、菜单所有权及事件处理同步。
4. QuPath 菜单误抢焦点；只读/保存脚本自动触发 hierarchy event 又变 dirty。新增 `update_hierarchy=False` 用于读取/保存，保留真实未保存修改。
5. ImageJ 上游将非空宏返回值作为 rejection，旧逻辑将错误吞成 0。增加带标记的真实宏结果协议，身份与尺寸同次读取，失败明确传播。
6. 科学组件异步加载/渲染提前报成功，Mol*/IGV/Vitessce 吞掉内部失败；Zarr/AnnData 新版本格式和 API 变化导致数据准备失败。现以实际完成和错误为准，并保持浏览器需要的 Zarr v2 数据格式。
7. 文档应用补齐可验证的通用控制；Notebook 等待写入后启动的新 poll，阻止旧响应覆盖新状态。
8. Files 删除确认期间读取变化后的目录，以及取消/失败仍报已删除。现固定原目标路径，失败如实返回；目录列表也拒绝把普通文件当成空目录。

## 已知边界

- JavaFX 原生键盘注入不能无损表示非 BMP 字符，例如 🧬。现在在整批操作发送前明确拒绝，不再错写字符；同窗口、同焦点文本框的 `desktop_call(run_script, thread='fx')` 替代输入已真实验证。
- Terminal `run` 表示命令已送入当前 PTY，不等同于命令已经执行结束；需要 read 输出验证。
- ImageJ 首次 Java VM 启动可能约 70 秒；此次文件验收在缓存已热情况下约 9 秒。等待期限已覆盖冷启动，未宣称消除了下载和 VM 启动成本。
- Notebook 的既有 packaged viewer 依赖轮询更新，操作等待真实刷新；本轮没有新增实时输出流协议。
- PDF 的验收覆盖基础渲染、翻页、缩放和文本读取，不涵盖浏览器 PDF 插件的全部功能或复杂 PDF 格式。
- 真实模型验收使用可用的 staging OpenRouter 路由。现有部分 OpenAI/Google 测试凭据分别返回额度不足/无效密钥，这属于模型环境问题；没有购买额度或更改密钥。

## 回归与复跑

- 核心真实全链：66/66。
- 科学真实全链：11/11，另含 Viv 面板和 IGV 自定义 reference 专项。
- Browser：7 组及 Unicode/menu/last-tab 专项；QuPath：12 组。
- Python：282 项通过；UI Desktop：171 项通过；Notebook 竞态：2 项通过。
- UI type-check、修改源文件的 ESLint/Oxlint、生产 build 通过。
- 全量 `pnpm lint` 仍因历史错误失败：基线 529 项，本次 527 项；vendored 源码单独排除，不改写第三方发行文件。未将全量 lint 记为通过。

复跑说明见 [scripts/desktop_audit/README.md](../scripts/desktop_audit/README.md)。UI 仓库提供通用 bridge、ImageJ macro 和完整文件导入的独立验收脚本。测试产物包括完整工具结果、窗口 ID、实际截图和文件字节，不以 UI 打开或工具 success 字段单独判定通过。

当前本机详细证据在 `/tmp/pantheon-all-apps`、`/tmp/pantheon-scientific-audit`、`/tmp/pantheon-native-audit`、`/tmp/pantheon-app-control`。所有测试原生沙箱均已终止；本地独立 NATS/fleet 在本轮结束时关闭。
