# AgentNexus TUI 调研与重构提案

日期：2026-10-19。基线：413453f。状态：用户已批准本方案，按下列三个提交实施。

## 调研依据

以下是官方项目资料。提案中的交互约束是结合 AgentNexus 输入、协作和思考展示需求作出的设计判断，不是照搬各项目的按键表。

| 资料 | 已核实的做法 | 对本项目的取舍 |
| --- | --- | --- |
| [LazyGit vision](https://github.com/jesseduffield/lazygit/blob/master/VISION.md) | 计算操作步数；保持肌肉记忆；在需要的位置显示快捷键；操作后给出明确反馈；禁用操作说明原因 | 用完整按键路径验收；常用操作有直达入口；禁用状态和提示由同一操作定义提供 |
| [Yazi quick start](https://yazi-rs.github.io/docs/quick-start/) / [keymap](https://yazi-rs.github.io/docs/configuration/keymap/) | 列表、输入、任务、选择、确认和帮助有各自按键上下文；方向键和 Vim 按键可并存；F1 打开帮助 | 主聊天优先文字编辑，浏览时再启用列表按键；不让普通字母快捷键截获输入；提供上下文帮助 |
| [Helix keymap](https://docs.helix-editor.com/keymap.html) | 明确区分编辑和导航上下文；picker 作为集中查找入口 | 统一查找、选择、执行的规则；聊天不强制切换到 Vim 的 Normal/Insert 模式 |
| [Textual input](https://textual.textualize.io/guide/input/) / [actions](https://textual.textualize.io/guide/actions/) | 绑定优先级会影响编辑器按键；Action 可同时接入点击和键盘；动态操作状态可驱动帮助栏 | 修正 Ctrl+C 抢占复制；绑定、可用性、底栏和点击共用操作定义 |
| [Terminal.Gui navigation](https://gui-cs.github.io/Terminal.Gui/docs/navigation.html) | 区域切换与区域内导航分开；焦点需要明确显示；鼠标操作有键盘替代入口 | Tab 不再遍历整段历史；一个区域一个活动项，返回后恢复原焦点 |

搜索经 agent-reach 的 Exa 后端完成。Jina 网页阅读连接失败，补充使用网页搜索与官方页面检索。未安装、更新或运行网上找到的代码。

## 当前问题与证据

使用真实 AgentNexusTUI.run_test 和 Textual Pilot，在独立临时配置、离线模拟执行器下验证。没有调用付费模型，没有改动项目源码或真实会话。

| 问题 | 实测或源码证据 | 影响 |
| --- | --- | --- |
| 焦点数量随记录增加 | 10 个 ReasoningBlock + 10 个 ToolCall 时，Tab 循环有 25 站 | 配置和输入之间要穿过整段历史；长任务越来越难操作 |
| 输入时翻页无效 | 对话 scroll_y=66/max=66，输入框焦点下 PageUp 后仍为 66 | 用户需要鼠标或先寻找另一个焦点 |
| Ctrl+C 抢占复制 | 已选中“需要复制的草稿”，按 Ctrl+C 后剪贴板为空，停止处理器调用 1 次 | 编辑和运行控制的语义冲突 |
| 命令建议不可操作 | 输入 /mod 可看到 /model；Down 仍在 Composer；Tab 后文本还是 /mod，焦点到了 VerticalScroll | 看起来可选，实际上不能选择或补全 |
| 返回路径丢失 | Ctrl+P 搜索资料并进入“资料与检索”，Esc 直接回 ChatScreen | 无法退回上级并保留搜索与选择位置 |
| 任务控制只有间接键盘路径 | InspectorScreen 只有 Esc 显式绑定；回复、补充、停止、记录依赖按钮焦点 | 用户必须逐个 Tab，提示没有说明直达方式 |
| 键盘与鼠标能力不对称 | MessageEditor 只有 Ctrl+S/Esc 发送和返回；思考、工具全文只有 D 入口 | 需要给鼠标辅助提供同一操作入口，同时避免增高面板 |
| 信息层次混乱 | 实际 80×24 截图中，协作树在内容顶部、底部又显示待答复；每段思考重复长提示；菜单搜索框 3 行、每项描述再占一行 | 真正内容被提示和控件挤压；所有区域近似相同视觉权重 |

复测脚本：C:/Users/29218/AppData/Local/Temp/nexus-tui-audit-20261019.py。它只构造临时离线数据，不修改应用源码。现有界面实拍在 baseline-80/。concept-*.svg/png 是提案效果图，不是已实现界面。

## 4+1 计划

### 1. 目标与验收

让聊天、查找操作、查看执行记录和协作回复都能连续只用键盘完成，同时保留点击、滚轮和选择文本作为辅助。

- 主聊天默认直接输入，不要求 Vim Normal/Insert 模式。
- 主屏 Tab/Shift+Tab 在输入、记录浏览和紧凑配置区之间循环，最多 3 个逻辑区域，数量不随历史长度变化。区域内用方向键选择或移动焦点；Esc 返回输入并保留草稿。
- 记录浏览将思考、工具和回答作为可选择的条目。普通回答也能打开可选择文本的详情，不再只有思考和工具有键盘入口。Enter 展开或查看，D 打开全文。鼠标使用相同操作。
- 输入框焦点时 PageUp/PageDown 翻阅对话，Ctrl+End 回到最新；多行编辑仍保留方向键移动和选择文本。
- Ctrl+C 有选区时复制；没有选区且任务运行时停止；没有运行时不触发停止提示。编辑器撤销保留编辑器语义，任务检查点操作从菜单和明确入口执行。
- Ctrl+P 可直接搜索并执行动作。常用动作不必先进分类菜单。保留 F2 会话、F3 任务、F4 多行；增加 Alt+M 模型、Alt+T 思考、Alt+A 附件、Alt+P 计划的直达入口，底栏点击同样有效。Alt 组合在 Pilot 中验证分发，Windows Terminal 与 tmux 的实际转发单列人工检查，Ctrl+P 始终作为不依赖 Alt 的入口。
- 列表统一为输入筛选、上下选择、Enter 确认；Esc 返回上一级并恢复搜索词和选中项，再按 Esc 回输入。
- 斜杠建议成为真正可操作的候选列表。上下选择，Tab 补全，Enter 对完整命令执行，对需要参数的操作进入对应选择或输入步骤。粘贴和换行不得意外执行。
- F3 中优先选中待答复子任务。R 回复、I 补充、X 停止、L 记录只在任务浏览上下文生效；编辑消息时由编辑器处理字母。有一个待答复问题时直接进入回复，多个问题时才选择。不可用动作不执行，并说明原因。
- F1 显示当前区域可用操作。底栏只展示少量高频且当前可执行的动作；完整帮助、底栏和鼠标入口来自同一组定义。
- 空会话保留已有项目 logo；进入对话后保留紧凑身份标记。用户消息依靠背景和间距识别，不恢复“你”或“AgentNexus”角色标题。
- 日常单行输入底部固定 3 行，待答复和运行信息合入状态行；附件和实际多行内容才增加高度。思考继续实时显示，不擅自隐藏或自动折叠。
- 正文保持统一左边界；层级由颜色、字重、缩进和一行间距形成。粉色主要用于焦点和当前动作。去除每段记录的常驻长快捷键提示；聚焦后由底栏提示。
- 操作选择器改为紧凑单行列表和单行搜索，显示选中项的说明，保留聊天上下文；任务详情适应宽度，窄屏纵向、宽屏可用列表加详情。
- 80×24、60×20、120×32 下检查正文、底栏、长模型名、中文、长路径和空结果；深浅主题可读。基础操作不要求 Nerd Font。
- 每条关键流程从真实按键开始，测试不通过直接 focus() 或私有 action 跳过导航，也不以点击代替键盘验收。另有少量鼠标测试证明两种入口结果一致。

### 2. 非目标

这轮不替换 Textual，不重写模型执行器、桌面端或数据库协议；不增加一个需要单独学习的全局 Vim 模式；不改变已有持久化、恢复、请求去重和工具权限语义。这里的范围是交互与显示的重构。

### 3. 受影响模块

- tui/screens/chat.py、tui/workspace.py：动作分发、按键上下文、区域焦点、返回位置。
- tui/screens/interactions.py、tui/screens/inspector.py：紧凑选择、参数输入、任务直达操作、回复编辑。
- tui/widgets/input_bar.py、message.py、hud.py、collaboration.py、welcome.py：输入和阅读、紧凑状态、点击入口、焦点表现。
- tui/styles/*.tcss、必要的 app.py 主题调整。
- tests/unit/test_tui_*、scripts/preview_tui.py、scripts/verify_tui_parity.py 和相关 TUI 文档。

具体实现以少量类型化操作定义为核心，包含动作 ID、标题、快捷键、可用状态、禁用原因及执行入口。保留既有 TerminalCommands 与 ChatService 作为业务执行层。用一份定义驱动同一动作的键盘、鼠标、菜单和帮助，不再叠加另一套命令协议。

### 4. 回滚

基线提交为 413453f。每个提交包含相应行为测试和可运行界面，按相反顺序 revert 即可回滚；不涉及数据格式迁移。三个原有无关改动继续排除：desktop/dist-electron/main.js、desktop/dist-electron/preload.js、docs/eval-improvement-plan.md。

### + 提交拆分

1. refactor(tui): unify action routing and focus navigation
   统一操作入口及状态，建立固定区域焦点和返回栈，修复 Ctrl+C/翻页冲突，补上下文帮助。验证草稿保持、25 站焦点问题、禁用操作及编辑语义。
2. feat(tui): streamline command selection and task controls
   实现可操作的斜杠建议、可直接搜索的操作、模型与附件直达、任务回复快捷键与一致的鼠标入口。验证从输入到执行、返回、回复和取消的完整路径及请求去重。
3. feat(tui): refine terminal layout and contextual feedback
   落实统一排版、三行底栏、紧凑菜单、任务布局、记录选中样式与 logo；去除重复提示。更新实际截图和文档，运行 TUI 功能对齐套件、ruff、无注释检查、diff 检查与打包后离线键盘流程。

各提交都包含自己的必要回归测试。最终验证覆盖操作结果、焦点恢复和按键数量，不只验证组件存在。外部终端是否截获 Alt 或组合键不能由 headless Pilot 证明，需在验收记录中明确区分。

## 实施记录

第一步已实现固定三个区域的 Tab 导航、区域内方向键选择、记录全文、输入时翻页、选区复制优先、常用动作直达与上下文帮助。菜单返回保留上级查询和选中值。绑定与可点击提示共用 TerminalAction 定义，业务执行仍使用现有命令和服务。

第一步提交 65142c0，415 项功能对齐测试通过。

第二步将斜杠建议改成完整候选列表，支持方向键选择、Tab 补全、Enter 执行或打开选择器，也支持点击候选。Ctrl+P 可直接搜索知识检索动作和全部命令；没有现有附件时 Alt+A 直接进入路径输入。原来只断言 Static.update 被调用的测试已由实际候选补全、选择与点击流程替代。

任务面板提供 R 回复、I 补充、L 记录、X 停止，优先待答复和运行中的子任务。按钮、按键、可点击底栏与 F1 帮助共用动作及禁用原因。回复编辑器以自己的按键上下文接管输入，R/I/X/L 是普通文字，发送支持 Ctrl+S 和底栏点击。协作回归保留不确定送达时的原请求 ID，确认相同内容的两次主动提交仍是两个请求。

新增 Pilot 回归通过真实按键覆盖空会话和长历史、配置区方向键、记录阅读返回、PageUp、Ctrl+End、F1、Alt+T、菜单逐级返回。运行中的 Ctrl+C 测试验证先复制选区、再停止任务，最后通过真实 ChatService 检查 interrupted 结果。原先脱离 Textual App、伪造内部状态调用 action_stop_task 的测试由这条完整流程替代。
