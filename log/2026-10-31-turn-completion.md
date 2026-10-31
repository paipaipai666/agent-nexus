# 桌面任务完成后的取消与保存冲突

## 目标与范围

目标：答案完成并保存后确认任务终态，避免桌面断连把完成结果改成中断；可选的长期记忆提取不阻塞任务完成，取消和重复提交不冒充存储故障。

不修改用户配置、聊天数据库结构或已保存的历史状态，不修改前端协议，提交由用户另行授权。保留此前能力缓存、协议输出与 tooltip 的未提交改动。

影响模块：`services/turn.py` 的终态与保存契约；Agent 的最终答案事件和 `services/chat.py` 的完成处理；记忆提取队列与应用关闭顺序；LLM 取消、传输错误和故障统计；需要立即核对提取结果的评测调用方。

回退时按下面三个单位同时撤销生产改动和对应测试。后台任务仅保存在进程内，回退不需要数据迁移。

实现分为以下三个验证单位，测量时尚未创建提交：

1. `fix: make terminal turn persistence idempotent`：已保存终态重复提交成功，失败终态重试保留完整快照，晚到的取消不设置完成任务的取消信号。
2. `fix: finalize answers before optional memory processing`：最终答案通过停止检查并保存后，通知 ChatService 确认完成；真实存储错误仍阻止后续处理。
3. `fix: isolate background memory extraction and cancellation`：按会话串行提取，独立取消信号、提交上下文与关闭流程；正常取消不计入 provider 故障。

## 环境与根因证据

环境为 Windows 11、Python 3.13.6。基线提交 `7aabc6f3d58ac1ae7e8560846d13758897c4abe3`，本次基于已有未提交改动继续修复。

用户日志对应的只读数据库核对结果：2026-10-31 08:13:35 答案就绪；08:13:45 因 `client_disconnected` 被标记中断。最终答案已有消息记录，取消快照 `persisted=True`，`save_error` 为空。没有修改这些实际记录。

旧路径在最终答案生成后同步调用长期记忆提取，约 10.20 秒后连接断开，触发前台取消并关闭模型流。Windows 读取已关闭连接出现 `WinError 10038`，随后转换成 `AgentCancelled`。记忆提取吞掉此异常后，Agent 又提交本轮记录。

`TurnRuntime.commit_round()` 对已保存快照返回 `False`，Agent 因此报 `commit rejected`。纯内存检查点实现也能复现，说明这条报错不要求数据库真的写失败。

另一个可复现问题是：取消在 provider 故障统计之后识别，三次正常取消会触发熔断。第四次正常请求无法到达 provider。`httpx.ReadError` 也没有按传输错误重试。

## 最终行为

- 最终答案通过 AGENT_STOP 检查、追加消息并完成本轮持久化后，才发送 `ANSWER_FINALIZED`。ChatService 在这个事件确认终态。`ANSWER_READY` 仍表示候选答案，不提前确认可能被停止钩子否决的内容。
- 已保存终态的重复提交是成功的空操作。终态存储失败后重试完整终态快照，保留问题、答案和错误状态；真实写入失败仍报告错误。
- `MemoryManager.conclude()` 返回 `Future[None]`。任务路径只排队，不等待提取。需要同步观察记忆结果的调用方可以等待 `.result(timeout=...)`。
- 每个会话最多一个提取 worker，空闲即退出。每项工作保存自己的提交上下文，使用独立于前台任务的取消信号。关闭时取消排队任务，正在运行的任务停止请求并拒绝晚到的写入；应用先停止这些工作，再关闭提供者。
- 正常取消不输出 provider 故障警告、不增加熔断失败次数。真实 HTTP 传输错误仍按已有预算重试。
- 长期记忆提取或提交失败只记录可选阶段的失败，不将已完成任务改成失败。收尾记忆钩子的载荷补齐 `role` 和 `content`，消除原有载荷校验错误。

## 验证与测量

所有新行为均先以失败测试确认，再修改实现。测试使用临时 `AGENTNEXUS_HOME`、本地 SQLite、受控 provider 和真实 FastAPI WebSocket，不调用用户的线上模型。

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 已保存终态再次提交 | `False`，导致 `commit rejected` | `True`，不重复写入 |
| 三次取消后再次请求 | 第四次因熔断无法调用 provider | 第四次正常返回，provider 共收到 4 次调用 |
| 提取请求等待释放 | `conclude()` 同步等待，测试中 0.5 秒无法返回 | 返回未完成 Future，不等待受控提取请求 |
| WebSocket 完成事件 | 长期记忆收尾阻塞完成 | 请求仍被测试闸门阻塞时，客户端已收到 `answer` 与 `done`；断连后保持 `finished/persisted` |
| 排队任务的提交上下文 | 新线程读取默认值 | 两项任务分别观察到自己的提交上下文 |
| 关闭时晚到的模型结果 | 无后台任务关闭契约 | 排队任务取消，晚到结果不写入记忆 |

上述请求数和阻塞行为每个场景执行一次，属于确定性验收。08:13:35 至 08:13:45 的间隔来自用户这一次事件记录，没有将它推广为延迟分布。没有重测线上模型首 token 或吞吐量。

原始数据位于 `.pytest_cache/`：

- RED：`turn-completion-red.xml`、`llm-cancel-red.xml`、`chat-finalization-red.xml`、`background-memory-red.xml`、`background-memory-hook-red.xml`、`background-memory-shutdown-red.xml`、`memory-context-red.xml`、`runtime-memory-close-red.xml`、`optional-memory-failure-red.xml`。
- GREEN：`turn-completion-green.xml` 21 项、`llm-cancel-green.xml` 28 项、`memory-background-contracts.xml` 115 项、`completion-websocket-final.xml` 34 项、`runtime-memory-close-green.xml` 92 项、`turn-lifecycle-focused.xml` 23 项。不同集合有重叠，不相加统计。
- 调用方和评测迁移：`completion-callers-final.xml` 28 项通过，`completion-eval-final.xml` 10 项通过。
- 完整后端回归：`completion-lifecycle-backend-final.xml`：5,048 项通过、5 项跳过，368.82 秒。另有 9 条来自既有 MCP/realtime 测试路径的未等待协程警告。

完整回归首轮发现两处调用问题：关闭逻辑依赖具体的 MemoryManager 类型，导致替换构造器的 CLI 测试失败；评测没有等待提取完成就核对或关闭存储。关闭改用公开接口，评测与批处理在读取新记忆前等待 Future，复测通过。Ruff、无注释门禁和 `git diff --check` 均通过。

复现命令：

```powershell
rtk proxy python -X utf8 -m pytest tests/unit/test_turn_completion_lifecycle.py tests/unit/test_llm_cancellation_outcome.py tests/unit/test_background_memory_conclusion.py tests/unit/test_chat_completion_lifecycle.py tests/unit/test_ws_disconnect_cancels.py tests/unit/test_graceful_shutdown.py -q --tb=short --timeout=45
rtk proxy python -X utf8 -m pytest tests/unit tests/integration tests/security -q --tb=short --timeout=60 --junitxml=.pytest_cache/completion-lifecycle-backend-final.xml
rtk ruff check .
rtk proxy python scripts/check_no_comments.py
rtk git diff --check
```

限制：长期记忆提取是可选的进程内工作。关闭或崩溃可能丢弃尚未提取的内容，聊天结果的保存不依赖它。自定义模型客户端若不支持中断，关闭最多等待共享的 1 秒预算，其晚到结果仍被写入检查拦住。原会话已保存的 `interrupted` 状态没有回写修改。

完整回归通过后，用户已授权提交。实际提交按 TUI tooltip、工具协议与能力缓存、任务完成与后台记忆收尾分组，可通过 `git log` 查阅。
