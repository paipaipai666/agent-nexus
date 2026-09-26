> **[中文](Agents-Detailed.md) | [English](Agents-Detailed.en.md)**

# 🤖 Agents 代理模块（详细版）

## 概述

`agents` 模块实现了 AgentNexus 的核心代理系统——基于**转移表驱动的有限状态机 (FSM)** 的 ReAct（Reasoning + Acting）代理。只有真正的决策点才是状态，流水线步骤是普通函数（见 `agents/decisions.py`）。

**设计哲学**：6 个状态、13 条转移规则。状态只承载"需要等待外部输入、且同一事件在不同情境下需要不同处理"的地方；参数准备、JSON 解析、分类这些**做完下一步必然**的步骤一律收进纯函数，方便单测也避免转移表膨胀成组合爆炸。

> 重设计记录（2026-09-24）：旧形态是 15 个状态 / 33 条转移，其中 `SELECT_STRATEGY`、`PREPARE_LLM_CALL`、`CHECK_EMPTY`、`JSON_PARSE`、`DEGRADE` 等都只有一个出口——它们是流水线的一步，不是决策点。更严重的是那张表不是全函数：handler 在落点状态发出该状态没定义的事件时 `fsm.py` 会直接抛错，且没有任何便宜手段提前发现（三个这样的缺口都能被普通模型行为触发）。现在决策函数返回值封闭，与落点状态的行一一对应，totality 由 `tests/unit/test_fsm_table_totality.py` 用 AST 静态断言守护。

## 架构总览

```
用户输入: "帮我分析这段代码"
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    ReActAgent.run()                          │
│                                                              │
│  ┌─────      ┌──────────────┐                              │
│  │INIT │─────>│ AWAIT_MODEL  │<──────────────┐              │
│  └─────┘      │ (auto-advance│               │              │
│               │  循环体)      │               │              │
│               └──────┬───────┘               │              │
│                      │                       │              │
│        TOOLS_REQUESTED│  ANSWER_READY  FAULT  │              │
│                      ▼                       │              │
│               ┌──────────────┐       ┌───────┴──────┐       │
│               │ EXECUTE_TOOL │       │   RECOVER    │       │
│               └──────┬───────┘       │ 重试/降档/兜底│       │
│        TOOLS_DONE ───┘               └───────┬──────┘       │
│        ANSWER_READY ──────────┐  ROUND_READY │ ABORT        │
│                               ▼              │              │
│                        ┌──────────────┐      ▼              │
│                        │   ANSWER     │   ┌──────┐          │
│                        │ (stop 钩子)  │   │ DONE │          │
│                        └──────┬───────┘   └──────┘          │
│                    ANSWER_VETOED │ (无条件)                  │
│                               └──────────────┘              │
└─────────────────────────────────────────────────────────────┘
```

## 核心类型

**文件**：`agentnexus/agents/react_types.py`

### CallingStrategy（调用策略）

四级降级策略，当高级策略失败时自动降级到低级：

| 策略 | 说明 | 触发条件 |
| --- | --- | --- |
| `NATIVE_TOOLS` | LLM 原生 tool_calls | 模型支持 tool_calling（首选） |
| `JSON_MODE` | response_format=json_object + 文本解析 | 模型支持 json_mode |
| `PROMPT_JSON` | Prompt 指令输出 JSON + 文本解析 | 模型不支持结构化输出 |
| `PLAIN_TEXT` | 纯自然语言，无结构化输出 | 最终回退 |

### ReActState（FSM 状态）

6 个状态，覆盖完整的 ReAct 循环（重设计后；旧形态曾有 15 个）：

| 状态 | 说明 | 吸收掉的旧状态 |
| --- | --- | --- |
| `INIT` | 入口：构建 prompt、消息、记忆、选择协议档位 | `INIT`、`SELECT_STRATEGY` |
| `AWAIT_MODEL` | 一轮模型往返 + 把输出解释成标准化决策（`event=None` auto-advance 自环即循环体） | `PREPARE_LLM_CALL`、`CALL_LLM`、`RECEIVE_RESPONSE`、`CHECK_TOOL_CALLS`、`CHECK_EMPTY`、`JSON_PARSE`、`CLASSIFY` |
| `EXECUTE_TOOL` | 执行整批工具，收集观察结果 | `EXECUTE_TOOL` |
| `RECOVER` | 所有"出问题了怎么办"的收口：重试 / 降档 / 兜底提取 / 终止 | `RETRY_GATE`、`DEGRADE`、`ERROR_ABORT` |
| `ANSWER` | 交最终答案（可被 AGENT_STOP 钩子否决打回） | `EMIT_ANSWER` |
| `DONE` | 终态 | `DONE` |

协议档位（`CallingStrategy`）不再是状态——它是 `AWAIT_MODEL` 的一个内部属性。

### ReActEvent（事件）

驱动状态转换的队列事件（8 个，返回值封闭集）：

| 事件 | 说明 |
| --- | --- |
| `START` | 用户调用 run(question) |
| `TOOLS_REQUESTED` | 解释器判定：模型要调工具（payload 带 tool_calls / thought / terminal_answer） |
| `ANSWER_READY` | 解释器判定：这是最终答案（含兜底提取） |
| `FAULT` | 本轮输出不可用或致命错误（payload 带 reason / detail / fatal） |
| `TOOLS_DONE` | 整批工具执行完成 |
| `ROUND_READY` | recover 决策：再来一轮模型调用 |
| `ABORT` | 终止 |
| `ANSWER_VETOED` | AGENT_STOP 钩子否决了最终答案 → 回到 AWAIT_MODEL |

**旁路观测事件**（`ctx.emit`，不进队列，只给 TUI 实时展示）：`TOOL_START`、`TOOL_DONE`、`STREAM_TOKEN`、`STREAM_REASONING`、`ANSWER_THOUGHT`、`LOOP_WARNING`（闭环提示）、`BUDGET_REMINDER`（预算提醒）。它们与队列事件共享同一个单调递增 `seq`，所以 UI 侧顺序不会乱。

旧事件名（`TOOLS_FOUND`、`ALL_TOOLS_DONE`、`LLM_PARAMS_READY`、`STOP_VETOED`、`NO_TOOLS`、`RETRIES_LEFT`、`DEGRADED` 等）保留为 **enum alias**，兼容下游按成员比较的代码。注意：alias 会让 `.type.name` 返回**新**的规范名，按字符串匹配旧名的代码必须同步更新。

## 转移表

**文件**：`agentnexus/agents/react_transitions.py`

13 条转移规则定义完整的状态机行为：

```python
TRANSFER_TABLE = [
    # INIT
    Transition(S.INIT, E.START, S.AWAIT_MODEL, "_on_init"),

    # AWAIT_MODEL —— event=None 的 auto-advance 自环即循环体
    Transition(S.AWAIT_MODEL, None, S.AWAIT_MODEL, "_on_round"),
    Transition(S.AWAIT_MODEL, E.ROUND_READY, S.AWAIT_MODEL, "_on_round_advance"),
    Transition(S.AWAIT_MODEL, E.TOOLS_REQUESTED, S.EXECUTE_TOOL, "_on_tools_requested"),
    Transition(S.AWAIT_MODEL, E.ANSWER_READY, S.ANSWER, "_on_answer_ready"),
    Transition(S.AWAIT_MODEL, E.FAULT, S.RECOVER, "_on_recover"),

    # EXECUTE_TOOL
    Transition(S.EXECUTE_TOOL, E.TOOLS_DONE, S.AWAIT_MODEL, "_on_round_advance"),
    Transition(S.EXECUTE_TOOL, E.ANSWER_READY, S.ANSWER, "_on_answer_ready"),

    # RECOVER —— 决策封闭于 {ROUND_READY, ANSWER_READY, ABORT}
    Transition(S.RECOVER, E.ROUND_READY, S.AWAIT_MODEL, "_on_round_advance"),
    Transition(S.RECOVER, E.ANSWER_READY, S.ANSWER, "_on_answer_ready"),
    Transition(S.RECOVER, E.ABORT, S.DONE, "_on_error_abort"),

    # ANSWER → DONE (无条件)
    Transition(S.ANSWER, E.ANSWER_VETOED, S.AWAIT_MODEL, "_on_vetoed"),
    Transition(S.ANSWER, None, S.DONE, "_on_emit_answer"),
]
```

**为什么这张表是全函数**：`_on_round` 只能返回 `TOOLS_REQUESTED` / `ANSWER_READY` / `FAULT` / `ROUND_READY`，`_on_recover` 只能返回 `ROUND_READY` / `ANSWER_READY` / `ABORT`，`_on_tools_requested` 只能返回 `TOOLS_DONE` / `ANSWER_READY`——每个 handler 的返回值封闭集都与落点状态的行一一对应。`tests/unit/test_fsm_table_totality.py` 用 AST 静态扫描断言这一点，任何破坏它的改动都会在测试里挂掉。

## FSM 引擎

**文件**：`agentnexus/agents/fsm.py`

```python
class StateMachine:
    def __init__(self, table: list[Transition])
    def subscribe(self, observer: Callable)          # 注册状态变化观察者
    def run_loop(self, initial_event, ctx, handlers)  # 主循环
```

### 运行循环

```python
def run_loop(self, initial_event, ctx, handlers):
    self._state = ReActState.INIT
    self._queue.append(initial_event)

    while True:
        if self._queue:
            event = self._queue.popleft()
        elif not self._try_auto_advance(ctx, handlers):
            break  # 无事件且无无条件转换 → 退出

        t = self._lookup(event)
        if t is None: continue

        self._state = t.next_state
        # 调用处理方法: handlers[t.handler](ctx)
```

## ReActAgent 主类

**文件**：`agentnexus/agents/re_act_agent.py`

```python
class ReActAgent:
    def __init__(self, llm_client, tool_executor,
                 max_steps=None, output=None, confirm_fn=None,
                 conversation_mode=False, agent_id="react_agent")

    def run(self, question, ...) -> ReActResult      # 单次运行
    def stream_run(self, question, ...) -> Generator  # 流式运行
    def set_session_profile(self, profile)            # 设置 Skill 配置
    def set_mcp_context(self, context)                # 设置 MCP 上下文
```

### 关键属性

| 属性 | 说明 |
| --- | --- |
| `llm_client` | AgentLLM 实例 |
| `tool_executor` | ToolRegistry 实例 |
| `max_steps` | 最大步数限制（默认从 Settings 读取） |
| `conversation_mode` | 是否为对话模式 |
| `_session_profile` | 当前 Skill 配置 |
| `_mcp_context` | MCP 工具上下文 |
| `_todo_list` | 待办列表（SQLite 持久化） |
| `_cancel_checker` | 取消检查回调 |

## LLM 策略模块

**文件**：`agentnexus/agents/llm_strategy.py`

### prepare_llm_call()

根据 CallingStrategy 准备 LLM 调用参数：

```python
def prepare_llm_call(strategy, messages, tools, json_format_section=None):
    if strategy == NATIVE_TOOLS:
        return tools, None                    # 传入工具定义
    if strategy == JSON_MODE:
        return None, {"type": "json_object"}  # 使用 JSON 模式
    if strategy == PROMPT_JSON:
        # 在消息末尾注入 JSON 格式指令
        last_msg["content"] += "\n\n" + json_format_section
        return None, None
    return None, None  # PLAIN_TEXT
```

### call_llm()

封装 LLM 调用，包含钩子触发：

```
BEFORE_MODEL_CALL (可中断) → llm.think() → AFTER_MODEL_CALL (可修改响应)
```

## 工具执行模块

**文件**：`agentnexus/agents/tool_runner.py`

```python
def execute_tool(tool_executor, name, arguments, caller,
                 hitl_approver, tool_policy, cancel_checker) -> str:
```

### 执行流程

```
BEFORE_TOOL_CALL 钩子 (可中断/修改参数)
    │
    ▼
ThreadPoolExecutor.submit(tool_executor.invoke)
    │
    ├── 轮询取消信号 (每 1s)
    ├── 超时 60s
    │
    ▼
AFTER_TOOL_CALL 钩子
    │
    ▼
返回结果字符串
```

### 关键特性

| 特性 | 说明 |
| --- | --- |
| 异步执行 | 使用 ThreadPoolExecutor 在独立线程中执行 |
| 取消支持 | 每秒轮询 cancel_checker |
| 超时保护 | 60s 硬超时 |
| 错误日志 | 写入 `tool_errors.log` |
| 钩子集成 | BEFORE/AFTER_TOOL_CALL 钩子 |

## Prompt 构建模块

**文件**：`agentnexus/agents/prompt_builder.py`

| 函数 | 说明 |
| --- | --- |
| `build_react_prompt()` | 构建 ReAct 系统提示词 |
| `build_react_messages()` | 构建消息列表（系统 + 历史 + 当前） |
| `build_conversation_context()` | 构建对话上下文 |

## JSON 辅助模块

**文件**：`agentnexus/agents/json_helpers.py`

| 函数 | 说明 |
| --- | --- |
| `_robust_json_parse()` | 鲁棒的 JSON 解析（处理 markdown 代码块、部分 JSON 等） |
| `_classify_parsed()` | 将解析结果分类为 tool_call / answer / error |

## 设计模式

| 模式 | 应用 |
| --- | --- |
| **State Machine** | 16 状态 × 25 转移规则的 FSM |
| **Transfer Table** | 转移表驱动，非硬编码 if-else |
| **Strategy Pattern** | 四级 CallingStrategy 降级 |
| **Observer** | FSM 状态变化订阅机制 |
| **Command** | 每条转移规则对应一个处理方法 |
| **Chain of Responsibility** | 钩子链式调用 |

## 模块依赖关系

```
ReActAgent (re_act_agent.py)
    ├── StateMachine (fsm.py)
    │       └── TRANSFER_TABLE (react_transitions.py)
    ├── CallingStrategy (react_types.py)
    ├── call_llm (llm_strategy.py)
    │       └── AgentLLM (core/llm.py)
    ├── execute_tool (tool_runner.py)
    │       └── ToolRegistry (tools/registry.py)
    ├── prompt_builder (prompt_builder.py)
    ├── json_helpers (json_helpers.py)
    ├── HookManager (core/hooks.py)
    ├── TraceManager (observability/tracer.py)
    ├── DriftDetector (observability/drift_detector.py)
    └── SkillRegistry (skills/)
```
