# 🤖 ReAct Agent 执行引擎

## FSM 状态机

Agent 的执行循环由一个 **16 状态 × 25 规则** 的 FSM 驱动，而非简单的 while 循环。

| 状态 | 含义 | 进入条件 |
|------|------|----------|
| `INIT` | 初始化 | 收到用户问题 |
| `SELECT_STRATEGY` | 选择 LLM 策略 | 系统提示就绪 |
| `PREPARE_LLM_CALL` | 准备 LLM 参数 | 策略已选定 |
| `CALL_LLM` | 调用 LLM | 参数已准备好 |
| `RECEIVE_RESPONSE` | 接收响应 | LLM 返回 |
| `CHECK_TOOL_CALLS` | 检查工具调用 | Native Tool Calling 结果 |
| `EXECUTE_TOOL` | 执行工具 | 发现工具调用 |
| `CHECK_EMPTY` | 检查空响应 | JSON 模式 |
| `JSON_PARSE` | 解析 JSON | 响应非空 |
| `CLASSIFY` | 分类结果 | 解析成功 |
| `RETRY_GATE` | 重试门控 | 失败 |
| `DEGRADE` | 降级策略 | 重试耗尽 |
| `EMIT_ANSWER` | 输出答案 | 收到最终 answer |
| `MAX_STEPS` | 步数超限 | current_step >= max_steps |
| `ERROR_ABORT` | 不可恢复错误 | LLM 调用彻底失败 |
| `DONE` | 结束 | 任意终态 |

## 执行流程

```
用户问题 → INIT → SELECT_STRATEGY → CALL_LLM
    │
    ├── Native Tool Calling:
    │   RECEIVE_RESPONSE → CHECK_TOOL_CALLS
    │     ├── 有 tool_calls → EXECUTE_TOOL(逐个执行)
    │     │     ↕ 循环 → PREPARE_LLM_CALL(继续)
    │     └── 无 → EMIT_ANSWER
    │
    └── JSON / Prompt JSON:
        RECEIVE_RESPONSE → CHECK_EMPTY → JSON_PARSE → CLASSIFY
          ├── {"tool":...} → EXECUTE_TOOL
          ├── {"answer":...} → EMIT_ANSWER
          └── 解析失败 → RETRY_GATE(重试2次)
                ├── 有重试 → PREPARE_LLM_CALL(加错误提示)
                ├── 降级 → PREPARE_LLM_CALL(换策略)
                └── 兜底 → 从原始文本提取

EMIT_ANSWER → 保存 LTM → DONE
```

## LLM 策略三级降级

自动检测模型能力，运行时降级，跨会话持久化：

| 等级 | 策略 | 依赖条件 |
|------|------|----------|
| 1 | **Native Tool Calling** | 模型支持 `tools` 参数 + `tool_choice="auto"` |
| 2 | **JSON Mode** | 模型支持 `response_format={"type":"json_object"}` |
| 3 | **Prompt JSON** | 系统提示嵌入 JSON 格式指令 |

检测来源：静态注册表（20+ 模型）→ litellm API 检测 → 用户配置覆盖。

## JSON 解析容错

`_robust_json_parse()` 四级流水线：

1. **Markdown 代码块提取**：``` ````json...```` ```` 正则抽取
2. **直接 json.loads**
3. **修复尾逗号**：`re.sub(r',\s*([}\]])', ...)` + 重试
4. **中英文符号归一化**：中文引号/逗号/冒号 → ASCII
5. **括号深度匹配**：扫描最外层 `{...}` 对

## AgentLLM 设计

- **流式调用**：始终 `litellm.completion(stream=True)`
- **重试**：最多 3 次，指数退避 2^attempt × 2.0s，仅重试瞬时错误
- **截断检测**：`finish_reason in ("length", "max_tokens")`
- **思考模式**：模型支持 `reasoning_effort` 时自动启用，`thinking_budget` 可配

> 见 [Tool-Governance](Tool-Governance.md) 了解工具治理，[Memory-System](Memory-System.md) 了解记忆管线。
