> **[中文](Agents-Detailed.md) | [English](Agents-Detailed.en.md)**

# 🤖 Agents 代理模块（详细版）

## 概述

`agents` 模块实现了 AgentNexus 的核心代理系统——基于**转移表驱动的有限状态机 (FSM)** 的 ReAct（Reasoning + Acting）代理。每个决策点是一个显式状态，每次转换是一个处理方法。

**设计哲学**：将 LLM 交互的复杂逻辑分解为 16 个状态和 25 条转移规则，每条规则对应一个处理方法，实现完全可追踪、可调试的代理行为。

## 架构总览

```
用户输入: "帮我分析这段代码"
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                    ReActAgent.run()                          │
│                                                              │
│  ┌─────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │INIT │→│SELECT    │→│PREPARE  │→│CALL_LLM │        │
│  │     │  │STRATEGY  │  │LLM_CALL │  │          │        │
│  └─────┘  └──────────┘  └──────────┘  └──────────┘        │
│                                       │                     │
│                    ┌──────────────────┤                     │
│                    ▼                  ▼                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │CHECK_TOOL    │  │CHECK_EMPTY   │  │ERROR_ABORT   │     │
│  │CALLS (Native)│  │(JSON/Text)   │  │              │     │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘     │
│         │                 │                                 │
│         ▼                 ▼                                 │
│  ┌──────────────┐  ┌──────────────┐                        │
│  │EXECUTE_TOOL  │  │JSON_PARSE    │                        │
│  │              │  │→CLASSIFY     │                        │
│  └──────┬───────┘  └──────┬───────┘                        │
│         │                 │                                 │
│         └────────┬────────┘                                 │
│                  ▼                                          │
│  ┌──────────────┐  ┌──────────────┐                        │
│  │RETRY_GATE    │→│DEGRADE       │ (策略降级)              │
│  └──────┬───────┘  └──────────────┘                        │
│         │                                                   │
│         ▼                                                   │
│  ┌──────────────┐  ┌──────────────┐                        │
│  │EMIT_ANSWER   │→│DONE          │                        │
│  └──────────────┘  └──────────────┘                        │
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

16 个状态，覆盖完整的 ReAct 循环：

| 状态 | 说明 |
| --- | --- |
| `INIT` | 入口：构建 prompt、消息、记忆 |
| `SELECT_STRATEGY` | 根据能力选择 CallingStrategy |
| `PREPARE_LLM_CALL` | 设置 tools/response_format，注入 JSON 提示 |
| `CALL_LLM` | 阻塞调用 llm_client.think() |
| `RECEIVE_RESPONSE` | 记录 AgentStep，按策略路由 |
| `CHECK_TOOL_CALLS` | NATIVE 模式：检查 last_tool_calls |
| `EXECUTE_TOOL` | 执行工具，收集观察结果 |
| `CHECK_EMPTY` | 非 NATIVE：响应文本是否为空？ |
| `JSON_PARSE` | _robust_json_parse() |
| `CLASSIFY` | _classify_parsed() → tool_call / answer / error |
| `RETRY_GATE` | 是否重试、降级或回退？ |
| `DEGRADE` | 标记失败 + 重新选择策略 |
| `EMIT_ANSWER` | 输出最终答案，保存记忆，结束 |
| `MAX_STEPS` | 达到步数限制 |
| `ERROR_ABORT` | 不可恢复错误 |
| `DONE` | 终态 |

### ReActEvent（事件）

驱动状态转换的事件：

| 事件 | 说明 |
| --- | --- |
| `START` | 用户调用 run(question) |
| `STRATEGY_READY` | _select_strategy() 完成 |
| `LLM_PARAMS_READY` | think 参数准备就绪 |
| `LLM_RESPONSE` | LLM 成功返回 |
| `LLM_ERROR` | LLM 调用失败 |
| `TOOLS_FOUND` | NATIVE：last_tool_calls 非空 |
| `NO_TOOLS` | NATIVE：无 tool_calls，有文本 → 答案 |
| `NO_TOOLS_NO_TEXT` | NATIVE：无 tool_calls，无文本 → 降级 |
| `TOOL_DONE` | 单个工具执行完成 |
| `ALL_TOOLS_DONE` | 批次中所有工具执行完成 |
| `EMPTY_RESPONSE` | 非 NATIVE：响应文本为空 |
| `HAS_CONTENT` | 非 NATIVE：响应文本有内容 |
| `PARSE_SUCCESS` | JSON 解析成功 |
| `PARSE_ERROR` | JSON 解析失败 |
| `CLASSIFIED_TOOL` | 分类为工具调用 |
| `CLASSIFIED_ANSWER` | 分类为最终答案 |
| `CLASSIFIED_ERROR` | 分类为错误 |
| `RETRIES_LEFT` | 还有重试次数 |
| `NO_RETRIES` | 无重试次数 → 降级 |
| `FALLBACK_TEXT` | 回退到文本输出 |

## 转移表

**文件**：`agentnexus/agents/react_transitions.py`

25 条转移规则定义完整的状态机行为：

```python
TRANSFER_TABLE = [
    # INIT
    Transition(INIT, START, SELECT_STRATEGY, "_on_init"),

    # SELECT_STRATEGY
    Transition(SELECT_STRATEGY, STRATEGY_READY, PREPARE_LLM_CALL, "_on_strategy_ready"),

    # PREPARE_LLM_CALL
    Transition(PREPARE_LLM_CALL, LLM_PARAMS_READY, CALL_LLM, "_on_llm_params_ready"),

    # CALL_LLM
    Transition(CALL_LLM, LLM_RESPONSE, RECEIVE_RESPONSE, "_on_llm_response"),
    Transition(CALL_LLM, LLM_ERROR, ERROR_ABORT, "_on_llm_error"),

    # RECEIVE_RESPONSE → 路由
    Transition(RECEIVE_RESPONSE, ROUTE_NATIVE, CHECK_TOOL_CALLS, "_on_receive_native"),
    Transition(RECEIVE_RESPONSE, ROUTE_JSON, CHECK_EMPTY, "_on_receive_json"),

    # CHECK_TOOL_CALLS
    Transition(CHECK_TOOL_CALLS, TOOLS_FOUND, EXECUTE_TOOL, "_on_tools_found"),
    Transition(CHECK_TOOL_CALLS, NO_TOOLS, EMIT_ANSWER, "_on_no_tools_answer"),
    Transition(CHECK_TOOL_CALLS, NO_TOOLS_NO_TEXT, DEGRADE, "_on_no_tools_degrade"),

    # EXECUTE_TOOL
    Transition(EXECUTE_TOOL, TOOL_DONE, EXECUTE_TOOL, "_on_tool_done"),
    Transition(EXECUTE_TOOL, ALL_TOOLS_DONE, PREPARE_LLM_CALL, "_on_all_tools_done"),

    # JSON_PARSE
    Transition(JSON_PARSE, PARSE_SUCCESS, CLASSIFY, "_on_parse_success"),
    Transition(JSON_PARSE, PARSE_ERROR, RETRY_GATE, "_on_parse_error"),

    # CLASSIFY
    Transition(CLASSIFY, CLASSIFIED_TOOL, EXECUTE_TOOL, "_on_classified_tool"),
    Transition(CLASSIFY, CLASSIFIED_ANSWER, EMIT_ANSWER, "_on_classified_answer"),
    Transition(CLASSIFY, CLASSIFIED_ERROR, RETRY_GATE, "_on_classified_error"),

    # RETRY_GATE
    Transition(RETRY_GATE, RETRIES_LEFT, PREPARE_LLM_CALL, "_on_retries_left"),
    Transition(RETRY_GATE, NO_RETRIES, DEGRADE, "_on_no_retries_degrade"),
    Transition(RETRY_GATE, FALLBACK_TEXT, EMIT_ANSWER, "_on_fallback_text"),

    # DEGRADE
    Transition(DEGRADE, DEGRADED, PREPARE_LLM_CALL, "_on_degraded"),

    # EMIT_ANSWER → DONE (无条件)
    Transition(EMIT_ANSWER, None, DONE, "_on_emit_answer"),
]
```

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
