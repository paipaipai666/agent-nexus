# TUI 现代化与 desktop 功能对齐方案

状态：功能对齐与后续确认的终端界面均已实现。界面实施、回滚快照和验证单元见 [终端界面实施记录](tui-interface-plan.md)。Python 全量仍有已在未修改 HEAD 复现的 Windows 基线失败，详见验收记录。

下文保留功能阶段的原始计划与当时“界面延期”的边界。用户随后确认重新按终端环境设计，并批准实施字符 Logo、单列对话、按需详情、可选择命令、多行编辑与主题；该批准取代原有界面冻结约束。当前使用方式见 [终端命令与快捷键](tui-commands.md)。

调研日期：2026-10-17。功能基准为当前工作区的 desktop 源码。

## 实施前基线与差距

TUI 使用 Textual。`agentnexus/tui/screens/chat.py` 共 1,721 行，包含界面、命令、会话切换、技能管理和代理执行。当前只有一个业务 Screen。

desktop 在 `desktop/src/App.tsx` 中提供 15 个页面组件。功能对齐以页面实际暴露的操作为准，不能把后台所有接口都算作 desktop 已有功能。

现有 TUI 并非只有基础聊天。它已经支持流式回复、工具确认、上下文压缩、会话恢复与切换、检查点撤销与重做、技能和 MCP 管理。但这些功能主要通过斜杠命令和只读侧栏呈现。

具体问题如下。

- 侧栏固定为 38 列。在实际运行的 80×24 布局中，聊天区只剩 42 列。输入框高 1 行。
- 命令建议使用 `Static` 展示。现有实现没有选择候选和确认补全的交互。
- `/stats` 输出 HUD 文本，尚未提供 desktop 的统计、日志和 trace 浏览能力。
- `AppRuntime.build()` 已创建 `runtime.chat`，TUI 入口没有传递这个服务。`ChatScreen.__init__()` 另外创建 `ChatService`，`_run_agent()` 又直接调用 `agent.run()`。
- `/switch` 修改版本管理器的会话 ID，但没有同步替换 `_chat_session`。运行状态、记忆、Todo、子代理和检查点的会话归属需要统一验证。
- 历史恢复读取短期记忆。desktop 已支持持久化消息历史，TUI 也应在上下文压缩后展示完整历史。
- 当前 TUI 的侧栏时间线只保留最近 8 条摘要，不能代替 desktop 的持久化事件时间线和上下文快照。

以上定位来自 `agentnexus/cli/tui_cmd.py`、`agentnexus/app/runtime.py`、`agentnexus/services/chat.py`、`agentnexus/tui/screens/chat.py` 和 `agentnexus/tui/widgets/side_panel.py`。

## 接入方式的取舍

| 方案 | 优点 | 代价 |
| --- | --- | --- |
| 继续使用进程内 `AppRuntime` 和共享服务，推荐 | 保留 `nexus tui` 的本地启动方式；共用会话、执行和恢复语义；不增加后台进程 | 部分管理逻辑仍在 HTTP 路由中，需要下沉到可复用的服务或现有领域模块 |
| TUI 改为 HTTP 与 WebSocket 客户端 | 直接复用 desktop 的传输协议；后续可连接远端后端 | 增加服务启动、鉴权、端口和连接恢复依赖，改变当前 TUI 的部署方式 |

推荐第一种。TUI 使用运行时已有的 `ChatService`，通过它提交请求和消费事件。HTTP 路由与 TUI 调用同一套领域操作，避免从 TUI 导入 FastAPI 路由函数，也不复制路由业务逻辑。

用户已批准按共享运行时方案推进功能对齐。

## 产品需求

### 目标

用户可以通过现有聊天输入区完成 desktop 当前提供的业务操作。功能通过斜杠命令、文本输出与现有确认弹窗接入；界面现代化单独延期。

### 非目标

- 不迁移到新的 TUI 框架。
- 不新增独立 HTTP 服务依赖。
- 不改变代理推理策略、现有 HTTP 协议或数据库结构。
- 不把 Electron 窗口控制、拖拽或图形动效逐像素搬进终端。文件选择改用路径与文件列表，图表提供可读的数据表和终端图形。
- 不扩展 desktop 尚未提供的插件安装能力。当前插件页的基准是发现结果和加载报告。
- 不执行用户数据删除、真实付费评估或模型下载来完成开发验收。这些操作在产品中提供明确的用户入口，在隔离测试中验证。

### 功能对齐清单

“部分”表示已有能力需要补全入口或执行语义。“缺失”表示没有对应的 TUI 页面或完整操作流程，不代表底层模块不存在。

| desktop 页面或流程 | TUI 当前情况 | 本次验收范围 |
| --- | --- | --- |
| ChatPage，会话与工作区 | 部分 | 工作区筛选、新建、切换、恢复与删除会话；会话草稿；压缩后保留完整历史；检查点历史与撤销重做 |
| ChatPage，输入与执行 | 部分 | 多行输入、粘贴、本地附件添加与移除、附件校验、模型选择、计划模式、计划审阅、思考强度、停止任务、工具确认、Todo、上下文用量 |
| ChatPage，恢复流程 | 缺少完整入口 | 提交状态、任务状态、保存失败、重试保存、保留原请求、恢复记录导出和准备继续请求；重试不重复执行已接受的提交 |
| SubagentPage | 只有工具结果摘要 | 子代理列表、任务状态、完整详情、追加指令、回答澄清问题、取消任务和交付结果；区分发送回执、接收状态与结束状态 |
| TimelinePage | 只有侧栏摘要 | 主代理与子代理的持久化事件浏览、事件筛选、工具参数与结果详情、上下文快照 |
| SettingsPage | 缺失 | 提供商与模型配置、模型发现、活动模型、通用设置、persona；保留现有凭据掩码与更新语义 |
| SkillsPage | 已有命令 | 列表、搜索、详情、启停；保留现有技能选择、默认技能、校验和动态技能命令 |
| MCPPage | 已有命令 | 服务状态、工具列表、失败信息、启停、重试与重载；保留已有 resources 和 prompts 查询 |
| PluginsPage | 缺失 | 已发现插件、加载报告和运行时信息 |
| MemoryPage | 缺失 | 长期记忆列表与检索、短期上下文查看、删除、清空和反思；破坏性操作先确认目标和范围 |
| KnowledgePage | 缺失 | 文档列表、路径导入、进度、检索、删除；本地检索模型状态与准备；缺少可选依赖时显示可操作的说明 |
| WikiPage | 缺失 | 页面列表与详情、统计、查询、文本与文件导入、删除、lint、审阅处理和回填 |
| StatsPage | `/stats` 仅显示 HUD | 时间范围统计、运行日志与 trace 详情；图形信息有数据表形式 |
| HealthPage | 缺失 | 健康检查结果、组件状态与失败详情 |
| AlertsPage | 缺失 | 告警列表与规则展示 |
| AuditPage | 缺失 | 审计记录列表、筛选与详情 |
| EvalPage | 缺失 | 任务、详情、校验、评估套件、运行结果、基线保存与比较；长任务不阻塞界面 |

这张清单覆盖 15 个 desktop 页面。ChatPage 拆成三行，以便分别验收会话、执行与恢复。

### 界面方向（已延期，不属于本阶段）

本节及下方布局示意仅保留原始设计提案。本阶段不实施主题、分栏、导航、表单、命令补全或输入框改造。

典型场景是开发者在本地终端中长时间阅读回复、检查工具执行并管理多个会话。终端窗口经常与编辑器并排，因此布局优先保证正文宽度、焦点可见和键盘路径。

沿用 desktop 已提交的中性黑白主题与玫瑰色强调。终端使用单一主强调色，成功、告警与错误保留语义色，并配合文字状态。所有常用文本和占位文字达到可读对比度，提供明暗主题及终端兼容的降级配色。

建议布局如下，图中展示信息组织，不表示功能已实现。

```text
 AgentNexus   AgentNexus 工作区   当前会话               搜索命令
 ────────────────────────────────────────────────────────────
 会话与导航        对话内容                      当前任务
                  用户请求                      执行状态
 最近会话         助手回复                      Todo
 新建会话         工具摘要，可展开详情            子代理
                  需要处理的确认或问题            上下文
 知识与 Wiki
 能力与设置        模型  思考强度  计划模式
 运行与评估        多行输入与附件
 ────────────────────────────────────────────────────────────
 当前会话状态                         发送、停止与导航提示
```

- 132 列及以上支持导航、正文与任务详情三栏。两侧都可收起。
- 100 至 131 列默认显示导航与正文，任务详情通过抽屉打开。
- 80 至 99 列默认使用单栏，两侧内容通过导航和抽屉访问。
- 设置和管理页采用列表与详情，窄屏逐页进入，避免横向挤压字段。
- 工具默认展示名称、状态和耗时。参数、结果与 diff 按需展开。
- 命令面板支持搜索、上下选择和确认执行。保留现有斜杠命令及 `/exit`。
- 输入框随内容增高到上限，多行粘贴不误发送。发送与换行快捷键同时显示，并验证 Windows 终端的按键兼容性。
- 空状态给出下一步入口；加载、失败、保存中和完成状态都有准确文字。

### 验收标准

1. 功能清单中的每一行都有实际可用的 TUI 入口、成功路径、失败反馈和对应测试。只添加命令名字或只读占位页不算完成。
2. TUI 复用 `AppRuntime.chat` 和共享执行事件。切换会话后，记忆、Todo、子代理、运行事件、队列和检查点均归属于目标会话，迟到事件不进入其他会话。
3. 模型、计划模式和思考强度影响下一次实际提交。工具确认、取消、失败和保存恢复遵循共享服务的结果。
4. 子代理消息能展示服务返回的回执与状态。重复提交和重复追加指令不会造成重复执行。
5. 保持现有布局与所有样式；安装产物在 80×24 正常启动，聊天区和侧栏保持 42/38 列。响应式改造与其他布局验收延期。
6. 新增操作可用键盘命令完成；长计划使用现有确认弹窗滚动审阅全文。
7. 文件、检索、模型准备和评估任务通过后台任务运行。离开页面或退出应用后正确停止订阅、计时器和资源。
8. 使用临时数据验证删除与清空，使用本地替身验证模型、附件和评估流程。可选依赖不可用时应用仍能启动并显示原因。
9. 相关 pytest、后端契约测试、desktop vitest、lint 和注释门禁通过。新增或调整后端逻辑遵循先失败测试、再实现、再重构。

## 4+1 实施计划

### 1. 目标

交付共享执行状态、与当前 desktop 对齐的业务命令，以及可重复运行的验收证据。界面现代化延期。

### 2. 非目标

遵循上文产品非目标。保持 `nexus tui`、`nexus --continue` 和现有会话数据兼容，不实施数据迁移，不顺带重做 desktop。

### 3. 影响模块

| 模块 | 计划改动 |
| --- | --- |
| `agentnexus/tui/app.py` | 接收共享运行时，保持现有应用入口与生命周期 |
| `agentnexus/tui/screens/`、`commands/` 与 `execution.py` | 分离共享执行适配，新增业务命令；不增加页面 |
| `agentnexus/tui/widgets/` | 补全长计划审阅和 HUD 状态，保持样式；`styles/` 不改动 |
| `agentnexus/cli/tui_cmd.py` | 传递完整运行时及初始会话，保持入口兼容 |
| `agentnexus/services/` 与既有领域模块 | 复用聊天、协作、技能和评估服务；提取确需复用的管理操作 |
| `agentnexus/app/runtime.py` | 仅调整共享服务装配和会话接入所需部分 |
| `agentnexus/server/routes/` | 仅在提取共享管理逻辑时改为调用共享操作，保留 HTTP 契约 |
| `tests/unit/` 与 `tests/integration/` | 会话隔离、事件与恢复、管理操作、Textual Pilot 交互和布局回归 |
| 文档与打包配置 | 更新启动说明、快捷键和功能清单，验证安装产物包含 TUI 样式 |

实施前为每个功能记录 desktop 入口、共享操作、TUI 入口和验收用例。静态 API 引用盘点用于防漏，行为测试负责证明功能。

### 4. 回滚策略

每个阶段包含对应回归测试。先完成共享执行接入，再交付业务命令；不保留长期并行的两套聊天执行器。界面阶段延期。

默认不修改数据库结构。配置仍使用现有读写和凭据保留规则。发生回归时按依赖逆序回退提交，保留用户会话与配置数据。若发现某项功能必须改变数据结构或公共 API，暂停该项，说明原因与取舍后再申请批准。

### + 按提交拆分

以下是拟采用的提交边界，不代表现在创建提交或向远端发布。每一步完成后运行相关验证，再开始下一步。

| 顺序 | 拟用提交标题 | 完成条件 |
| --- | --- | --- |
| 1 | `refactor: share runtime execution with tui` | TUI 使用共享聊天服务；会话切换、队列、历史、事件和取消回归通过 |
| 延期 | `feat: modernize tui workspace navigation and themes` | 本阶段不执行；多行请求由草稿文件提供 |
| 3 | `feat: align tui chat controls and recovery` | 附件、模型、思考强度、计划审阅、工具确认和保存恢复可用 |
| 4 | `feat: add tui subagent workspace and timeline` | 子代理详情、追加指令、澄清答复、取消、持久化事件与上下文快照可用 |
| 5 | `feat: add tui settings and capability management` | 提供商、模型、persona、技能、MCP 和插件信息可管理 |
| 6 | `feat: add tui memory management` | 记忆列表、搜索、上下文查看、反思与删除确认可用 |
| 7 | `feat: add tui knowledge management` | 文档导入与进度、检索、删除和本地模型状态可用 |
| 8 | `feat: add tui wiki workspace` | Wiki 浏览、查询、导入、审阅、lint 与回填可用 |
| 9 | `feat: add tui runtime inspection` | 统计、日志、trace 与健康检查可用 |
| 10 | `feat: add tui alerts and audit views` | 告警、规则和审计记录可浏览与筛选 |
| 11 | `feat: add tui evaluation workspace` | 任务、套件、结果和基线比较可用，长任务不阻塞操作 |
| 12 | `test: verify tui desktop parity and packaged startup` | 完整清单逐项验收；布局、快捷键、打包启动与全量相关测试通过；文档更新 |

每个涉及后端行为的提交都先添加能复现缺失能力或错误的失败测试，再实现对应功能。第 12 步负责跨功能验收，不能替代前面各步的测试。

## 调研时验证证据

调研阶段执行了 12 个 TUI 相关测试文件，共 309 项测试通过，有 1 条 warning。这证明现有基线通过，不代表上述对齐目标已经完成。

```powershell
rtk proxy python -X utf8 -m pytest tests/unit/test_tui_pilot.py tests/unit/test_tui_chat_screen.py tests/unit/test_tui_lifecycle.py tests/unit/test_tui_session_commands.py tests/unit/test_tui_widgets.py tests/unit/test_tui_side_panel.py tests/unit/test_tui_message.py tests/unit/test_tui_input_bar.py tests/unit/test_tui_hud.py tests/unit/test_tui_confirm_dialog.py tests/unit/test_tui_chat_static.py tests/unit/test_cli_tui.py -q --disable-warnings
```

可重复运行的源码盘点工具、JSON 结果和三种尺寸的现状 SVG 位于：

`C:/Users/29218/.codex/visualizations/2026/10/17/01a14848-72bc-7953-b977-48e1d13211a3/`

运行下面的命令可重新生成盘点与布局证据。截图使用已有 Pilot 测试替身，不调用真实模型，也不写入用户会话。

```powershell
rtk proxy python -X utf8 C:/Users/29218/.codex/visualizations/2026/10/17/01a14848-72bc-7953-b977-48e1d13211a3/audit_tui.py --root D:/code/AgentNexus
```

调研前已有未提交改动：`desktop/dist-electron/main.js`、`desktop/dist-electron/preload.js` 和 `docs/eval-improvement-plan.md`。本方案不修改它们。

## 功能阶段验收

使用方式见 [TUI 功能命令](tui-commands.md)，逐项行为证据、复核发现及已知基线失败见 [功能验收记录](tui-parity-verification.md)。调研阶段的 309 项基线测试不作为功能完成证据。
