> **[中文](ReAct-Agent.md) | [English](ReAct-Agent.en.md)**

# 🤖 ReAct Agent 执行引擎

## FSM 状态机

Agent 的执行循环由一个 **6 状态 × 13 规则** 的 FSM 驱动，而非简单的 while 循环。
（重设计于 2026-09-24；旧形态是 15 状态 × 33 规则，其中多数状态只有一个出口——
它们是流水线的一步，不是决策点，详见 `docs/fsm-redesign-proposal.md`。）

| 状态 | 含义 | 进入条件 |
|------|------|----------|
| `INIT` | 初始化：建上下文、选协议档位 | 收到用户问题 |
| `AWAIT_MODEL` | 一轮模型往返 + 解释成标准化决策 | 循环体（`event=None` auto-advance 自环） |
| `EXECUTE_TOOL` | 执行整批工具 | 模型请求了工具（原生 tool_calls 或协议 JSON） |
| `RECOVER` | 重试 / 降档 / 兜底提取 / 终止 | 本轮输出不可用（FAULT） |
| `ANSWER` | 交最终答案 | 有最终 answer（可被 AGENT_STOP 钩子打回） |
| `DONE` | 结束 | 无条件落点 / ABORT |

协议档位（原生工具 / JSON Mode / Prompt JSON）不再是状态，而是 `AWAIT_MODEL` 的内部属性。

## 执行流程

```
用户问题 → INIT → AWAIT_MODEL（每轮：调用 LLM + 解释输出）
    │
    ├── TOOLS_REQUESTED → EXECUTE_TOOL（整批执行，读写分区并发）
    │     ├── TOOLS_DONE → AWAIT_MODEL（继续）
    │     └── ANSWER_READY → ANSWER（记账类工具的 fast path）
    │
    ├── ANSWER_READY → ANSWER →(stop 钩子否决)→ ANSWER_VETOED → AWAIT_MODEL
    │
    └── FAULT → RECOVER
          ├── ROUND_READY（重试加提示 / 换策略档位）→ AWAIT_MODEL
          ├── ANSWER_READY（从原始文本兜底提取）→ ANSWER
          └── ABORT → DONE

ANSWER → 保存 LTM → DONE
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
