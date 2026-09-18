# 🪝 Hooks 钩子系统

AgentNexus 的确定性规则强制层：在 agent 生命周期的固定挂点执行用户声明的命令，或框架内注册的 Python 回调。**钩子的价值是确定性**——不靠 LLM 自觉，规则必然执行。

两类钩子：

| 类型 | 注册方式 | 适用 |
| --- | --- | --- |
| **命令钩子** | `config.yaml` / `hooks.yaml` 声明，子进程执行 | 用户：格式化、保护文件、通知、审计 |
| **进程内钩子** | `@on(HookType.X)` Python 装饰器 | 框架/插件：深变更（改 messages/tools/payload） |

## 命令钩子配置

```yaml
# ~/.agentnexus/hooks.yaml（用户级，默认信任）
# <workspace>/.agentnexus/hooks.yaml（项目级，需 hash 信任审查）
# config.yaml 的 hooks: 段（托管层，默认信任）
hooks:
  - event: before_tool_call
    matcher: "file_write|file_edit"     # 可选，正则，作用于 payload["name"]
    command: "python3 ~/.agentnexus/hooks/protect.py"
    timeout: 30                          # 秒，默认 10，超时杀整棵进程树
    async: false                         # true = 后台执行，仅观测语义，不能阻断
    enabled: true
    on_failure: warn                     # warn | block（仅拦截类事件可 block）
```

来源分层（低→高优先级，匹配的钩子全部执行）：`config.yaml` → `~/.agentnexus/hooks.yaml` → `<workspace>/.agentnexus/hooks.yaml`。

### 进程契约（与 Codex / Claude Code 收敛）

- **stdin**：JSON `{"hook_event", "session_id", "workspace", "payload", "abort_supported"}`
- **exit 0**：成功；stdout JSON 可携带 `{"update": {合并进 payload}, "context": "日志文本"}`
- **exit 2**：阻断。stderr 全文 = 阻断原因，经 `HookContext.to_feedback()` 标准化后**必然喂回模型**（observation）+ 进审计
- **其他非零 / 超时 / 启动失败**：按 `on_failure` 策略（`warn` 记录并继续 / `block` 阻断）

### 安全

- 钩子以**擦洗后的环境**运行：只传 `AGENTNEXUS_*` 与 OS 基础变量（PATH 等），LLM API key 等机密永不传递
- 项目级钩子按 `(源文件路径 + event + command)` 的 sha256 指纹审查，存于 `~/.agentnexus/hook_trust.json`；新增/变更的钩子跳过执行并告警，直到：
  - `nexus hooks trust approve <fingerprint>`，或
  - `POST /api/hooks/trust {"fingerprint": "...", "action": "approve"}`
- `hook_trust: bypass` 可全局放行（仅建议一次性自动化场景）
- 管理入口：`nexus hooks list` / `nexus hooks test <event>`（干跑）/ `GET /api/hooks`

## 事件矩阵

可变性：✅ = payload 可改（变更对调用方生效）；❌ = 只读（误改会在日志被点名）。阻断：✅ = 可 `abort()` 短路；封顶说明见各事件。

### Tier 0：用户交互

| 事件 | payload | 可变 | 阻断 |
| --- | --- | --- | --- |
| `user_prompt_submit` | `prompt`, `agent_id` | ✅（改写 prompt） | ✅ 拒绝输入，run 不启动 |
| `agent_stop` | `answer`, `steps`, `question`, `agent_id` | ❌ | ✅ 否决最终答案，agent 带反馈继续；**连续否决封顶 2 次** |
| `permission_request` | `name`, `params`, `caller`, `risk_level` | ✅（`decision: "allow"` 需 `hitl_hooks_may_approve: true`） | ✅ 拒绝 = 阻断，**审计记 `hitl_decision=hook_denied:*`** |
| `notification` | `kind`（如 `hitl_request`），`tool`, `caller`, `risk_level` | ❌ | 否（异步通知） |

### Tier 1：agent / 工具 / 模型

| 事件 | 可变 | 阻断 |
| --- | --- | --- |
| `before_tool_call` / `after_tool_call` / `on_tool_error` | ✅（params）/ ❌ / ❌ | ✅ / 否 / 否 |
| `before_model_call`（messages, tools, strategy）/ `after_model_call` | ✅ | ✅ / 否 |
| `before_llm_call` / `after_llm_call` | ✅ | ✅ / 否 |
| `agent_start` / `agent_end` | ❌ | 否 |
| `before_memory_op` / `after_memory_op`、`before_ltm_save/search` 等 | ❌ | ✅（before_*） |

### Tier 2/3：基础设施

`before_shell_exec`（✅ 阻断）、`before_registry_invoke`、`before/after_mcp_connect`、`before/after_mcp_call_tool`、`before/after_subagent_run`、`before/after_rag_search`、`before/after_kb_ingest`、`before/after_checkpoint`、`before/after_plugin_load`、`before/after_app_build`、`before/after_compact`、`before/after_workflow_step`、`before/after_eval_run`。全部只读，`before_*` 可阻断。

完整 payload 键与类型见 `agentnexus/core/hooks.py` 的 `PAYLOAD_SCHEMAS`（`hook_schema_check: true` 时 fire 点漂移即报错）。

## 进程内钩子 API

```python
from agentnexus.core.hooks import AbortCode, HookType, on

@on(HookType.BEFORE_TOOL_CALL, priority=100)   # 小数字先执行；同名钩子后者覆盖
def protect_env(ctx):
    path = str(ctx.payload.get("params", {}).get("path", ""))
    if ".env" in path:
        ctx.abort("不允许修改 .env", code=AbortCode.POLICY_VIOLATION)
```

- `ctx.payload` 共享可变字典；非可变事件的误改会产生 warning（changed keys 被点名）
- `ctx.abort(reason, code=..., details=...)` 短路链；code 用 `AbortCode` 枚举（`BLOCKED / PERMISSION_DENIED / VALIDATION_FAILED / POLICY_VIOLATION / RATE_LIMITED`）
- 回调异常被隔离：同名钩子首次 warning（带 traceback），后续 debug 计数
- 慢链（>100ms）warning；每次 fire 有 `hook_fire` trace span
- 注册表管理：`list_hooks()` / `enable()` / `disable()` / `unregister()`

## 可靠性

- **per-hook 超时**：`register(..., timeout=秒)` 或 `@on(..., timeout=秒)` 只约束同步回调；超时后循环继续（残留线程自行跑完，Python 无法杀线程——同 registry SEC-008 的约定）
- **journal**：每次 fire 的逐钩子结果追加到 `{traces_dir}/hooks.jsonl`（append-only JSONL：event/hook/outcome/duration_ms/changed_keys），`hooks_journal: false` 关闭；`GET /api/hooks` 返回最近 50 条，`read_hook_journal()` 可编程读取
- **trace**：`hook_fire` span 下挂逐钩子 `hook_call` 子 span
- **顺序保证**：同 priority 按注册顺序执行（sort 稳定性）

## 插件代码挂钩

声明式插件（`plugin.yaml`）默认零代码。插件目录可提供 `hooks.py`（暴露 `register(manager)`），在 manifest 声明 `entrypoint: hooks.py` 后，**且仅当** `plugins_allow_code: true` 时执行加载。entrypoint 必须是插件目录内的相对路径。

## 执行顺序

1. 进程内钩子链（priority 升序，abort 短路）
2. 命令钩子链（来源分层顺序；拦截类事件顺序执行，观测类并发；abort 短路）
