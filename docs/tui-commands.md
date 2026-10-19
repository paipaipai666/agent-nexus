# TUI 功能命令

运行 `nexus tui` 启动终端。`nexus --continue` 继续当前目录最近的会话。

界面默认使用单列对话。空会话展示项目字符 Logo，恢复历史直接进入对话。命令可以直接输入，也可通过 Ctrl+P 搜索选择。`/help` 显示全部命令。后台任务执行期间可以继续查询状态或提交排队消息。

对话直接显示消息正文，用户消息通过背景区分。普通单行输入时底部共三行：配置与任务状态、输入、操作提示与上下文用量。多行编辑按实际行数增高，不预留空白行。底栏提示支持点击，窄终端优先保留主要操作、返回和 F1 帮助。

| 按键 | 操作 |
| --- | --- |
| Ctrl+P | 搜索和选择操作，方向键选择、Enter 确认 |
| F1 | 查看当前区域的按键、禁用原因和完整会话用量 |
| F2 | 搜索并切换历史会话，或新建会话 |
| F3 | 打开任务与协作页，所有终端宽度使用相同入口 |
| F4 | 在当前输入区切换多行模式；多行模式下 Enter 换行 |
| Ctrl+S | 发送完整消息，执行期间加入队列；子代理消息编辑器也使用此键发送 |
| Shift+Enter、Alt+Enter、Ctrl+J | 终端支持时插入换行；F4 可作为兼容入口 |
| Alt+M、Alt+T、Alt+A、Alt+P | 模型、思考强度、附件、计划模式的直接入口；终端未转发 Alt 时使用 Ctrl+P |
| Ctrl+C | 有文字选区时复制；没有选区且任务执行中时请求停止 |
| Esc | 逐级返回，恢复上级搜索和选择；在主界面返回输入区 |
| Tab、Shift+Tab | 在输入、记录和配置三个区域间切换，不随历史条数增加 |
| ↑↓、Enter、D | 浏览区选择记录、展开摘要或查看消息、打开完整原始输出 |
| ←→、Enter | 配置区选择并打开当前操作 |
| PageUp、PageDown | 输入时翻阅对话，保留编辑内容和光标 |
| Ctrl+End | 回到最新输出；向上阅读时暂停自动跟随 |

模型实际返回的思考内容在主对话中实时显示，与工具和回答按顺序排列。Tab 进入记录区，方向键选择思考块，Enter 折叠或展开，D 查看全文。历史会话中的思考与最终答案也会分别恢复显示。用量栏保持一行上下文信息，F1 或点击用量可查看累计输入、输出、模型能力和检查点。

Ctrl+P 可直接搜索常用操作、知识检索和全部命令。模型和思考强度直接选择，附件路径与资料搜索有独立参数输入，不替换正在编辑的任务。通过菜单查看的配置、检索等结果在独立记录页显示，Esc 返回原对话。“全部命令”保留完整功能及参数帮助。输入 `/` 时可用方向键选择候选、Tab 补全、Enter 执行或打开对应选择器，完整命令及参数仍可直接输入。

Ctrl+P 的主题选项支持深色、浅色和终端 ANSI 配色。也可直接输入 `/theme nexus-dark`、`/theme nexus-light`、`/theme ansi-dark` 或 `/theme ansi-light`。

## 会话与执行

| 命令 | 操作 |
| --- | --- |
| `/session list [query]` | 搜索已保存的会话，包括其他工作区 |
| `/session new [workspace]` | 新建会话并切换，可指定工作区目录 |
| `/session switch <id>` 或 `/switch <id>` | 切换会话，同时切换记忆、代理、Todo 与版本历史 |
| `/session delete <id>` | 确认后删除非当前会话，执行中的会话须先停止 |
| `/plan on`、`/plan off`、`/plan status` | 设置或查询当前会话的计划模式 |
| `/think default`、`/think none`、`/think low`、`/think medium`、`/think high` | 设置当前会话的思考强度，`default` 使用配置默认值 |
| `/stop` | 请求停止当前会话任务，资源释放前仍显示执行中 |
| `/todo` | 查看当前会话的任务 |
| `/undo`、`/redo`、`/log`、`/status` | 检查点操作与版本信息 |
| `/compact [instructions]` | 压缩上下文 |
| `/reset` 或 `/clear --all` | 确认后清空当前会话历史与上下文 |
| `/clear`、`/exit` | 清屏、退出 |

计划审批和工具确认显示完整内容。长文本可用方向键、PageUp、PageDown、Home 和 End 浏览全文，Y 批准，N 或 Esc 拒绝。停止操作使用 Ctrl+C 或 `/stop`。

切换会话后，其他会话的执行结果继续保存在原会话中，不会插入当前对话。上下文压缩后，已保存的完整消息历史仍可恢复。

## 附件与多行请求

路径可以加双引号。Windows 路径保留反斜杠。

```text
/attach add "D:\work\design notes.txt"
/attach list
/attach remove 1
/attach clear
/draft load "D:\work\request.txt"
/draft show
/draft send
/draft clear
```

附件随下一条普通消息或草稿发送。最多 10 个附件，图片最多 5 MB，发送时再次验证文件。模型支持视觉时使用共享服务的图片输入，其余情况提供文件路径。

输入区随内容增高，最多显示六行，更多内容在输入区内滚动。粘贴多行保留在原位并进入换行模式，不会自动发送。Ctrl+S 发送完整消息；F4 切换 Enter 的发送或换行行为，当前行为始终显示在输入下方。

`/draft load` 读取 UTF-8 文件并保留换行，`/draft send` 提交并消耗完整草稿。普通输入草稿、多行草稿和附件按会话保存至应用配置目录的 `tui-drafts/`，重启后恢复。输入上方可直接选择模型、思考强度、计划模式和附件，状态栏显示排队数量和待答复数量。已提交附件随请求消耗；队列推进不会清空新输入的草稿。

撤销、重做、重置和手动压缩在会话执行期间拒绝修改上下文。动态 Skill 指令与普通消息共用队列，轮到该请求时才应用技能。

## 恢复任务

| 命令 | 操作 |
| --- | --- |
| `/recover` 或 `/recover status` | 查看最新任务、保存状态和原提交 |
| `/recover save` | 仅重试保存结果，不重新执行工具 |
| `/recover retry` | 查询原提交回执；尚未接受的提交使用相同请求 ID 重试 |
| `/recover continue` | 把核对已完成操作后的继续请求放入草稿，检查后再发送 |
| `/recover export "<path>"` | 导出 JSON 恢复记录，目标文件须不存在 |

取消和失败后的恢复记录包含已执行活动。撤销会话只恢复对话上下文，不撤销已经完成的文件修改或外部操作。

## 子代理与时间线

```text
/subagents list
/subagents show <subagent_id>
/subagents cancel <subagent_id>
/subagents send <subagent_id> <child_run_id> <request_id> <追加指令>
/subagents reply <subagent_id> <child_run_id> <question_id> <request_id> <回答>
/timeline [after_id] [agent_id]
/context <run_id> <step_id> [agent_id]
```

子代理详情包括当前尝试、消息及回执、执行事件和结果。`request_id` 是调用者为该条消息选定的唯一字符串。重复查询或重试同一内容时保持该 ID；不同消息使用不同 ID。`child_run_id` 与问题 ID 从详情或问题记录中取得。

存在子任务时，输入上方的紧凑状态显示待答复数量，F3 或点击状态打开任务页。方向键选择子任务，R 回复开放的问题、I 补充指令、X 请求停止、L 查看执行记录，Tab 切换列表和正文。面板优先选中待答复或运行中的子任务；宽终端并排显示列表和正文，窄终端上下排列。回复绑定选中的子任务、尝试和问题，重试使用相同消息 ID。执行记录展示实际保存状态和检查点；“已保存”与执行完成分别显示。

回复编辑器中 Enter 换行、Ctrl+S 发送、Esc 保留草稿并返回，底栏也支持点击。R、I、X、L 在编辑器内是普通文字。任务进度更新会保留正在阅读的位置。任务数据暂不可用时显示具体原因，暂停对应修改操作，返回和其他导航继续可用。

追加消息的回执与任务完成状态分别显示。取消命令返回请求是否被接受，最终状态由后续事件或详情确认。

## 模型与能力配置

```text
/settings show
/settings set <key> <value>
/providers list
/providers save "<providers.json>"
/model list
/model select <provider/model>
/model discover <provider>
/persona show
/persona save "<persona.json>"
/plugin
```

配置读写、凭据掩码和活动模型切换与 desktop 共用服务。`providers.json` 的顶层是 `providers` 数组，字段与 desktop 提供商配置相同。列表导出的 `api_key: "****"` 再次保存时保留原密钥。`persona.json` 使用 `agent_name`、`identity`、`tone` 和 `projects` 字段。

现有 `/skill` 与 `/mcp` 命令继续可用。

```text
/skill list
/skill search <query>
/skill show <id>
/skill status
/skill use <id>
/skill enable <id>
/skill disable <id>
/skill validate [id]
/mcp status
/mcp tools
/mcp enable <server>
/mcp disable <server>
/mcp retry <server>
/mcp reload
```

`/skill` 还支持默认技能、重置与动态技能命令。`/mcp` 还支持 resources、prompts 与 failures。插件命令报告当前实际发现和加载情况，与 desktop 保持一致。

## 记忆、知识库与 Wiki

```text
/memory list [limit]
/memory search <query>
/memory short
/memory delete <id>
/memory clear
/memory reflect [days]
/kb list
/kb search <query>
/kb ingest "<path>"
/kb progress [run_id]
/kb delete <document_id>
/kb models
/kb prepare embedding
/kb prepare reranker
/kb deletions
/kb retry <deletion_log_id>
/kb rollback <deletion_log_id>
```

导入期间可在另一次输入中运行 `/kb progress` 查看当前会话最近一次导入。模型准备状态通过 `/kb models` 查看。检索使用已有本地模型和索引策略，不会用占位结果伪装成功。缺少可选依赖、模型或索引时显示实际错误。

`/kb search` 也接受 JSON 对象，可指定 `query`、`namespace`、`top_k`、`source`、`file_format`、`section_title` 和 `view` 等已有检索参数。

```text
/wiki list [namespace]
/wiki show <page_id>
/wiki stats [namespace]
/wiki query <question>
/wiki ingest "<UTF-8 file>"
/wiki text <text>
/wiki delete <page_id>
/wiki lint [namespace]
/wiki reviews [status]
/wiki resolve <item_id>
/wiki process
/wiki backfill [namespace]
```

Wiki 默认使用配置中的知识库命名空间。`/wiki query` 也接受 JSON，可指定 `question`、`source_namespace` 和 `force_rag`。回填会按现有 desktop 行为重建该命名空间页面，执行前显示确认。

删除、清空、反思、模型准备、回填和评估执行等操作显示确认。开发测试使用临时数据和本地替身，没有运行真实模型下载或付费评估。

## 运行观测与评估

```text
/stats [days]
/logs [days]
/trace <trace_id>
/health
/alerts [days] [severity]
/alerts rules
/audit [tool] [limit]
/eval suites
/eval tasks
/eval tasks {"category":"coding"}
/eval task <task_id>
/eval validate
/eval run <suite>
/eval run <suite> {"n_trials":1,"concurrency":4}
/eval baseline <suite>
/eval save <suite>
/eval compare <suite>
```

`/stats` 显示实际运行统计，图形数据以文本和 JSON 提供。日志支持按天数过滤，并读取轮转文件。审计从实际工具审计记录读取。

评估使用与 desktop 相同的 `EvalService` 和默认运行器。`save` 执行套件并保存基线，`compare` 执行套件后比较已有基线。命令运行在后台，结果返回现有对话区。

## 重复验收

运行以下命令验证行为测试与样式资源。用户已批准新的终端布局，因此不再检查样式与旧版 HEAD 一致。

```powershell
rtk proxy python -X utf8 scripts/verify_tui_parity.py
```

可用离线示例数据导出当前实际 Textual 界面的 SVG 截图。预览使用独立临时配置和数据库，不访问模型接口。

```powershell
rtk proxy python -X utf8 scripts/preview_tui.py --output .research_tmp/tui-preview --width 80 --height 24
```

构建 wheel 后，可额外检查运行时资源，并从临时安装目录启动实际安装包。

```powershell
rtk proxy python -X utf8 -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir .research_tmp/tui-wheel
rtk proxy python -X utf8 scripts/verify_tui_parity.py --wheel .research_tmp/tui-wheel/agentnexus-0.2.17-py3-none-any.whl
```
