# TUI 功能对齐验收记录

日期：2026-10-17。第一阶段按用户确认先对齐业务功能，保留原界面；用户随后批准终端界面重设计。当前工作区 desktop 的 15 个页面是功能基准。下文保留功能阶段记录，并追加界面阶段的验收。

## 入口与行为证据

以下用例位于 `tests/unit/`。管理命令直接调用共享领域服务；Textual Pilot 用例通过输入框发送命令并检查实际会话与显示结果。

| desktop 范围 | TUI 入口 | 主要行为证据 |
| --- | --- | --- |
| ChatPage 会话、工作区、历史 | `/session`、`/switch`、`/undo`、`/redo`、`/reset` | `test_tui_shared_runtime.py::test_switching_sessions_replaces_memory_agent_and_version_together`；`test_continue_mode_restores_each_sessions_own_context`；`test_tui_commands.py::test_undo_restores_the_selected_context_and_its_token_counter` |
| ChatPage 输入、执行、草稿 | `/attach`、`/draft`、`/plan`、`/think`、`/stop`；现有确认弹窗 | `test_plan_and_thinking_controls_are_session_scoped`；`test_attachments_use_windows_paths_and_remain_in_their_session`；`test_drafts_and_attachments_survive_a_new_terminal_controller`；`test_stop_keeps_processing_until_the_worker_releases_resources`；`test_tui_pilot.py::TestConfirmDialogPilot::test_long_plan_can_be_reviewed_in_full` |
| ChatPage 恢复与保存 | `/recover status/save/retry/continue/export` | `test_recovery_exports_saved_result_without_running_again`；共享 `test_failure_recovery.py`、`test_history_persistence.py`、`test_chat_attachments.py` |
| SubagentPage | `/subagents list/show/send/reply/cancel` | `test_subagent_reply_preserves_attempt_question_and_request_identity`；`test_child_stream_tokens_do_not_flood_chat_and_questions_are_visible`；共享 `test_collaboration_run.py`、`test_collaboration_api.py` |
| TimelinePage | `/timeline`、`/context` | `test_timeline_and_context_are_scoped_to_the_target_session`；持久化时间线使用相同 store |
| SettingsPage | `/settings`、`/providers`、`/model`、`/persona` | `test_settings_commands_preserve_keys_and_switch_models`；`test_provider_validation_does_not_echo_secret_inputs`；`test_configuration_service.py` |
| SkillsPage | `/skill list/search/show/use/enable/disable/default/validate/reset`、动态技能命令 | `test_skill_search_and_detail_show_the_workflow`；`test_dynamic_skill_waits_in_the_same_session_queue`；既有 `TestSkillCommandHelpers` |
| MCPPage | `/mcp status/tools/resources/prompts/failures/retry/enable/disable/reload` | 既有 `test_tui_chat_screen.py` 的 MCP 命令、错误反馈与格式用例；MCP 领域测试 |
| PluginsPage | `/plugin` | 调用 desktop 同一 `configuration.get_extensions()` 加载报告；能力配置契约测试 |
| MemoryPage | `/memory list/search/short/delete/clear/reflect` | `test_memory_list_delete_and_clear_use_the_real_store`；`test_memory_query_service.py::test_http_and_terminal_search_use_query_embeddings`；共享反思测试 |
| KnowledgePage | `/kb list/search/ingest/progress/delete/models/prepare/deletions/retry/rollback` | `test_knowledge_list_works_without_downloading_models`；`test_knowledge_ingestion_progress_and_search_preserve_domain_results`；共享检索、模型交付与删除恢复测试 |
| WikiPage | `/wiki list/show/stats/query/ingest/text/delete/lint/reviews/resolve/process/backfill` | `test_wiki_page_and_review_commands_use_persisted_records`；`test_wiki_query_and_file_import_use_the_shared_service`；共享 Wiki 服务测试 |
| StatsPage | `/stats`、`/logs`、`/trace` | `test_trace_lookup_reads_the_actual_log`；`test_statistics_health_and_alerts_show_actual_records`；共享统计测试 |
| HealthPage | `/health` | `test_statistics_health_and_alerts_show_actual_records`；调用现有 `run_health_checks`，使用相同组件检查 |
| AlertsPage | `/alerts`、`/alerts rules` | `test_statistics_health_and_alerts_show_actual_records`，写入并筛选实际告警记录 |
| AuditPage | `/audit [tool] [limit]` | `test_audit_commands_show_real_tool_entries`，读取实际工具审计日志 |
| EvalPage | `/eval tasks/task/validate/suites/run/baseline/save/compare` | `test_evaluation_lists_shipped_suites_and_rejects_missing_tasks`；`test_evaluation_saves_and_compares_real_baseline_files` |

测试中的生成、下载和外部服务使用本地替身。记忆、Wiki 页面、时间线、导入进度、草稿和评估基线使用隔离的实际存储。未运行真实付费评估或模型下载。安装包启动检查只证明资源与启动；行为对齐由上表用例及共享服务测试分别证明。

## 会话隔离与复核

独立只读复核使用 gpt-6-astra。初次复核发现五项问题，追加复核发现两项边界问题，均已落实为失败复现和修复后的回归测试：

| 问题 | 修复与回归用例 |
| --- | --- |
| 动态技能绕过队列，修改执行中的技能 | 统一提交入口，执行开始时选择技能；`test_dynamic_skill_waits_in_the_same_session_queue` |
| 提供商验证错误回显密钥 | 边界处去掉校验错误的输入值；`test_provider_validation_does_not_echo_secret_inputs` |
| 五分钟无事件后丢失最终结果 | 消费到共享服务结束信号；`test_quiet_execution_keeps_listening_for_the_final_answer` |
| 草稿附件被复用或被后续操作修改 | 提交时复制并消耗附件；`test_sending_a_draft_consumes_an_independent_attachment_snapshot` |
| 后台压缩提示污染当前会话 | 回调绑定会话；`test_background_compaction_cannot_change_the_selected_chat` |
| 队列推进清掉尚未发送的输入 | 仅用户提交时清输入；`test_queue_drain_preserves_a_new_unsent_draft` |
| 切换会话后准备好的提交被跳过 | 先处理会话所属提交，再判断是否显示；`test_prepared_background_draft_is_submitted_to_its_original_session` |

另外验证迟到的正常结果与命令错误不会进入其他会话，工具结果仍使用原有摘要与 diff 格式，HUD 保留累计 token 用量，手动历史变更在执行期间被拒绝。

## 重复执行

本轮结果：TUI 与共享运行时、配置相关测试最终 387 项通过；desktop Vitest 40 个文件、229 项通过；Python 全量 4232 项通过、2 项跳过、5 项失败。五项失败全部在未修改 HEAD 源码副本复现，详见下节。全量回归后新增的草稿存储边界测试已在相关套件补验，确保切换会话时也不会保存设置命令中的密钥。Ruff、注释门禁、desktop 配置元数据检查通过。wheel 包含 7 个终端样式文件、6 个评估套件 YAML 与内置技能资源，安装后启动检查通过。

```powershell
rtk proxy python -X utf8 scripts/verify_tui_parity.py
rtk proxy python -X utf8 scripts/check_no_comments.py
rtk proxy python -X utf8 scripts/generate_desktop_settings.py --check
rtk proxy python -X utf8 -m ruff check agentnexus/ scripts/verify_tui_parity.py
```

`verify_tui_parity.py` 运行 TUI 与共享配置、运行时相关测试，检查样式资源。使用 `--wheel <path>` 额外验证资源、安装到新目录并启动安装后的 80×24 TUI，断言全宽聊天、隐藏侧栏、Logo 欢迎区、Ctrl+P 操作面板和 F4 多行编辑器。界面批准后不再检查样式与旧版 HEAD 一致。

## 终端界面验收

`test_tui_workspace.py` 通过实际 Textual 应用、按键和持久化结果验证以下行为：

- 空会话的项目字符 Logo、历史恢复跳过欢迎区、首次发送后收起欢迎区。
- 80×24 默认单列；宽屏 F3 侧栏、缩放后自动恢复全宽；60×20 的浅色和 ANSI 主题下仍能输入和编辑。
- Ctrl+P 筛选与执行、F2 新建与切换、F4 保留换行且只发送一次、多行粘贴不会误发送。
- 工具折叠、键盘展开和完整原始输出；读取历史时保留位置，切换会话清除旧的新输出提示。
- 真实子代理状态、问题回复、尝试与问题归属、投递回执。重复主动指令使用新 ID；已入库但丢失回执后重试同一 ID，不重复投递。
- 关闭并重新打开检查页保留本次终端中的子代理消息草稿。主会话草稿继续写入原有草稿存储。
- 会话切换与初次恢复时，Todo、用量、上下文和能力提示属于目标会话，旧时间线不会保留在新会话中。

界面截图由 `scripts/preview_tui.py` 导出实际 Textual 帧，数据是隔离的离线示例，不代表真实模型执行成绩。Rich 导出中的中文 `textLength` 按终端单元格宽度修正，不改动应用内容或布局。80×24、100×30 和浅色截图保存在本次可视化目录的 `tui-implemented/` 中。

界面阶段最终：相关 Python 测试 404 项通过，desktop 40 个文件、229 项通过，wheel 安装启动通过。Python 全量 4250 项通过、2 项跳过、5 项既有 Windows 失败；最终 XML 为 `.research_tmp/tui-ui-unit-final.xml`。Ruff、注释门禁和 desktop 配置元数据检查通过。独立只读复核发现的四项状态与消息身份问题全部修复，详情见 [界面实施记录](tui-interface-plan.md)。

## 已知的全量基线失败

Windows 上五项既有测试失败，已在 `git archive HEAD` 提取的未修改源码副本中复现相同五项失败：

- `test_hook_commands.py::TestExecutorContract::test_timeout_kills_and_continues`：超时后等待子进程约 30 秒。
- `test_shell_backends.py::TestWindowsNativeSandboxReal::test_runs_at_low_integrity`。
- `test_shell_backends.py::TestWindowsNativeSandboxReal::test_writes_workspace_denied_outside`。两项均为 `CreateRestrictedToken failed: 87`。
- `test_tool_process_kill.py::test_cancel_kills_tool_process_tree_immediately`。
- `test_tool_process_kill.py::test_run_tracked_timeout_kills_tree`。

本阶段未改动上述实现或测试，也未屏蔽这些失败。原始基线结果保存在本地 `.research_tmp/tui-baseline-windows.xml`，完整回归结果保存在 `.research_tmp/tui-final-unit.xml`。因此全量 Python 单测不能报告为全部通过。
