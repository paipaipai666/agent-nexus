# 模型能力缓存与协议输出修复

## 环境与范围

环境：Windows 11（10.0.26100）、Python 3.13.6。基线提交：`7aabc6f3d58ac1ae7e8560846d13758897c4abe3`。测量时实现尚未提交，保留此前已有的 TUI tooltip 修复。

症状：支持原生工具调用的模型使用 `PROMPT_JSON`；TUI 将 `thought/tool/params` 控制对象直接显示为聊天正文；重复启动可能再次发送能力探测。

涉及 `core/capabilities.py`、`core/capability_discovery.py`、`core/llm.py`、`core/json_helpers.py`、Agent 决策与输出事件，以及 TUI 的后台发现入口。没有修改用户配置或聊天数据库结构。

## 根因与证据

1. 旧版探测在普通工具调用成功后继续测试并行调用。如果第二次请求限流或超时，整个探测返回空结果，丢失已确认的普通工具能力。此前对当前配置的一次实测中，原生工具探测在约 1.91 秒返回工具调用，随后并行探测收到限流，旧逻辑最终没有缓存成功能力。此次实现阶段未继续调用远程模型。
2. 未知能力用 `False` 表示。策略选择因此落到提示词 JSON，底层请求构建也会剥掉 `tools`；两处必须一起修正。
3. 旧缓存只保存完整成功，24 小时过期；身份还包含思考和预算配置，失败退避仅在进程内保留。旧格式的 `False` 同时混合了“未测试”“未观察到”和“不支持”。
4. 原始模型内容先产生 `STREAM_TOKEN`，随后才解析控制 JSON；工具执行正确也无法撤回已显示的正文。解析重试还会把损坏的控制响应写入可回放记忆。
5. 原生空响应和 JSON 解析失败会禁用协议；宽泛的错误关键词也会把部分传输、参数数据错误当作能力不支持。

对应的失败测试和修正后结果保存在 `.pytest_cache/` 下的 JUnit 文件中。用例驱动真实 Agent、provider、本地 HTTP 服务和 Textual 输入/会话恢复；远程模型不可用时不依赖在线重试。

## 实现结果

- 未知模型的任务直接尝试 `NATIVE_TOOLS → JSON_MODE → PROMPT_JSON`。显式配置关闭的协议按配置跳过，只有明确的协议拒绝才逐级切换。解析失败、空响应、限流、超时和连接错误不构成不支持证据。
- 每项能力证据立即保存，后续可选探测失败保留已有结果。缓存按规范化端点、精确模型名和认证摘要区分；思考强度与输出预算不改变身份。确认结果没有固定过期时间，临时失败的 30 秒退避也可跨进程读取。
- 旧格式导入成功值；含义不明确的旧 `False` 恢复为未知。手动刷新可重新探测，刷新失败不清除成功证据。
- 每次请求捕获模型、端点、配置、缓存与会话能力对象。请求期间切换模型时，返回证据写回实际被调用的模型。
- 输出事件只发布解码后的控制内容。普通原生文本继续流式输出；结构化候选暂存到解析完成。工具控制对象、损坏协议和恢复历史不再作为答案显示，代码块内的业务 JSON 保留原文。

## 请求数量与测量方法

样本是确定性行为验收，每个列出的场景执行一次，不作为延迟分布或吞吐量基准。计数单位为 provider/HTTP 请求数。

| 工作负载 | 基线证据 | 修复后 |
| --- | --- | --- |
| 未知模型首个普通任务 | 旧执行路径及原集成断言要求 2 次探测加 1 次任务，共 3 次；本次未重跑旧版远程测量 | 本地真实 HTTP 集成断言共 1 次任务请求 |
| 已确认能力被新进程读取 | 旧版 24 小时过期、配置变化可触发重测 | 新进程读取相同身份，禁止探测的 loader 未被调用，额外探测 0 次 |
| 普通工具成功、并行探测限流/超时 | 两次请求后整体结果为空 | 同为 2 次请求，保留 `tool_calling=True`，可选能力保持未知 |
| 原生协议明确拒绝，JSON Mode 也明确拒绝 | 旧恢复规则包含解析失败降级 | 3 次真实任务请求依次使用原生、JSON Mode、提示词 JSON，最终正确返回答案 |

没有重测远程首 token 延迟，也未把请求减少换算成时间收益。手动发现仍共享最多 5 秒总预算，保留慢响应头、持续流和连接回收的集成检查。

## 验证与原始数据

- `.pytest_cache/capability-protocol-integration-final.xml`：相关单元、真实 HTTP、TUI 与历史恢复，223 项通过，约 24.93 秒。此轮在完整回归后执行，包含最后补充的截断答案和代码块流式续写修正。
- `.pytest_cache/llm-fixture-contracts.xml`：客户端、流式中断和用量回归，38 项通过，约 8.84 秒。
- `.pytest_cache/capability-protocol-focused-final.xml`：能力、错误分类和协议展示专项，76 项通过，约 5.42 秒；后续续写改动由上述 223 项最终回归覆盖。
- `.pytest_cache/capability-protocol-backend-final.xml`：完整后端回归，5,027 项通过、5 项跳过，约 367.30 秒。报告含 9 条未等待协程警告，来自未改动的 MCP/realtime 测试路径。此轮之后又完成上述 223 项相关回归。
- RED 证据：`native-policy-red.xml`、`protocol-fullwidth-red.xml`、`legacy-capability-red.xml`、`answer-event-order-red.xml`、`wrapped-transient-red.xml`、`partial-answer-stream-red.xml`、`partial-code-stream-red.xml`。部分报告在后续复测中覆盖，保留的专用 RED 报告可用于核对失败断言。

复现命令，在仓库根目录执行：

```powershell
rtk proxy python -X utf8 -m pytest tests/unit/test_capability_evidence.py tests/unit/test_native_protocol_policy.py tests/unit/test_protocol_output.py tests/integration/test_model_capability_startup.py -q --tb=short
rtk proxy python -X utf8 -m pytest tests/unit tests/integration tests/security -q --tb=short --timeout=60 --junitxml=.pytest_cache/capability-protocol-backend-final.xml
rtk ruff check .
rtk proxy python scripts/check_no_comments.py
rtk git diff --check
```

Ruff、无注释门禁及 `git diff --check` 均通过。

测试使用临时 `AGENTNEXUS_HOME` 和受控端点，不改实际用户数据。相同进程内的发现请求合并；跨进程可复用已经写好的结果与退避，不承诺多个进程同时首次冷探测只发出一组请求。损坏缓存会被忽略，实际任务仍按未知能力尝试。

## 回退

生产改动可与对应测试一同回退。新版缓存独立标记版本；回退旧代码后，旧读取器会忽略新版缓存，可能重新探测。此前的 tooltip 改动独立保留。实现验证完成后，用户已授权提交。
