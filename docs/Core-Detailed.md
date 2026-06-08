> **[中文](Core-Detailed.md) | [English](Core-Detailed.en.md)**

# ⚙️ Core 核心模块（详细版）

## 概述

`core` 模块是 AgentNexus 的基础设施层，提供以下核心能力：

| 文件 | 职责 |
| --- | --- |
| `config.py` | 全局配置管理（Pydantic Settings） |
| `llm.py` | LLM 客户端（流式调用、重试、能力检测） |
| `capabilities.py` | 模型能力检测与静态注册表 |
| `hooks.py` | 生命周期钩子系统（60+ 钩子点） |
| `providers/` | LLM 提供者抽象层（路由 + OpenAI 兼容） |
| `judge_llm.py` | 独立 Judge LLM（避免自评偏差） |
| `pricing.py` | Token 定价与成本估算 |
| `pii.py` | PII 检测与脱敏 |
| `text_utils.py` | 文本工具函数 |

## config.py — 全局配置

### 核心类：Settings

基于 `pydantic_settings.BaseSettings`，支持 YAML 文件 + 环境变量两种配置源。

```python
class MCPServerConfig(BaseModel):
    name: str
    enabled: bool = True
    transport: str = "stdio"          # stdio | streamable_http
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    risk_level: str = "medium"        # low | medium | high
    require_hitl: bool = False
    timeout_sec: int = 60
    rate_limit_per_min: int = 10
    max_concurrency_per_server: int = 4
    # ... 更多字段

class LLMSettings(BaseModel):
    api_key: SecretStr
    model_id: str
    base_url: str
    timeout: int
    model_tool_calling: bool | None
    model_json_mode: bool | None
    model_thinking: bool | None
    model_thinking_budget: int
    max_output_tokens: int = 8192
    judge_model_id: str
    judge_api_key: SecretStr
    judge_base_url: str

class RAGSettings(BaseModel):
    enable_contextual_retrieval: bool
    enable_query_rewrite: bool
    enable_multi_query: bool
    enable_hyde: bool
    # ...
```

### 函数

| 函数 | 说明 |
| --- | --- |
| `get_settings()` | 获取全局 Settings 单例（懒加载） |
| `load_config_yaml(path)` | 从 YAML 文件加载配置 |
| `write_config_yaml(path, data)` | 写入 YAML 配置文件 |
| `get_config_dir()` | 获取配置目录路径 |

## llm.py — LLM 客户端

### 核心类：AgentLLM

```python
class AgentLLM:
    def __init__(self, model=None, apiKey=None, baseUrl=None, timeout=None):
        # 从 Settings 读取默认值
        # 自动检测模型能力

    def think(self, messages, temperature=0, silent=False,
              tools=None, response_format=None,
              thinking=None, max_attempts=None,
              on_token=None) -> str:
        # 主要调用方法
        # 支持流式输出、工具调用、思考模式
        # 3 次指数退避重试
```

### 关键特性

| 特性 | 说明 |
| --- | --- |
| 流式输出 | 基于 litellm 的流式 API |
| 指数退避重试 | 最多 3 次，基础延迟 2.0s |
| 断路器 | 连续 3 次失败后冷却 60s |
| 能力检测 | 自动检测模型的 tool_calling/json_mode/thinking 支持 |
| 钩子集成 | 调用前后触发 `BEFORE_LLM_CALL` / `AFTER_LLM_CALL` 钩子 |
| Provider 路由 | 优先使用直接 Provider，回退到 LiteLLM |

### 使用的钩子

```
BEFORE_LLM_CALL → (可中断) → LLM 调用 → AFTER_LLM_CALL
```

## capabilities.py — 模型能力检测

### 核心类：ModelCapabilities

```python
@dataclass
class ModelCapabilities:
    supports_tool_calling: bool = False
    supports_json_mode: bool = False
    supports_json_schema: bool = False
    supports_thinking: bool = False
    supports_parallel_tool_calls: bool = False
    supports_system_role: bool = True
    max_context_tokens: int = 128_000
    max_output_tokens: int = 8_192
    thinking_budget_tokens: int = 4_000
    thinking_effort: str = "medium"
```

### 静态注册表（前缀匹配，首次匹配即返回）

| 模型前缀 | 能力 |
| --- | --- |
| `deepseek/deepseek-v4-pro` | tool_calling, thinking, parallel, 262K ctx |
| `openai/gpt-5*` | tool_calling, json_mode, json_schema, thinking, parallel |
| `openai/gpt-4*` | tool_calling, json_mode, json_schema, parallel |
| `anthropic/claude-4.6*` | tool_calling, thinking, parallel, 200K ctx |
| `anthropic/claude-4.5*` | tool_calling, thinking, parallel |
| `*` | 默认保守能力 |

### 能力检测优先级

```
用户配置覆盖 > LiteLLM 动态检测 > 静态注册表 > 默认值
```

### SessionCapabilityTracker

运行时追踪器，记录会话中模型的实际表现（如工具调用失败次数），用于动态调整策略。

## hooks.py — 生命周期钩子系统

### HookType 枚举（60+ 钩子点）

分三个层级：

**Tier 1: 核心治理路径**

| 钩子 | 触发时机 |
| --- | --- |
| `BEFORE_TOOL_CALL` / `AFTER_TOOL_CALL` | 工具调用前后 |
| `ON_TOOL_ERROR` | 工具调用出错 |
| `BEFORE_MODEL_CALL` / `AFTER_MODEL_CALL` | 模型调用前后 |
| `AGENT_START` / `AGENT_END` | 代理启动/结束 |
| `BEFORE_LLM_CALL` / `AFTER_LLM_CALL` | LLM 调用前后 |
| `BEFORE_LTM_SAVE` / `AFTER_LTM_SAVE` | 长期记忆保存前后 |
| `BEFORE_SHELL_EXEC` / `AFTER_SHELL_EXEC` | Shell 执行前后 |
| `BEFORE_REGISTRY_INVOKE` / `AFTER_REGISTRY_INVOKE` | 工具注册表调用前后 |

**Tier 2: 操作生命周期**

| 钩子 | 触发时机 |
| --- | --- |
| `BEFORE_MCP_CONNECT` / `AFTER_MCP_CONNECT` | MCP 连接前后 |
| `BEFORE_MCP_CALL_TOOL` / `AFTER_MCP_CALL_TOOL` | MCP 工具调用前后 |
| `BEFORE_SUBAGENT_RUN` / `AFTER_SUBAGENT_RUN` | 子代理运行前后 |
| `BEFORE_RAG_SEARCH` / `AFTER_RAG_SEARCH` | RAG 搜索前后 |
| `BEFORE_KB_INGEST` / `AFTER_KB_INGEST` | 知识库摄入前后 |
| `BEFORE_CHECKPOINT` / `AFTER_CHECKPOINT` | 检查点操作前后 |

**Tier 3: 基础设施生命周期**

| 钩子 | 触发时机 |
| --- | --- |
| `BEFORE_PLUGIN_LOAD` / `AFTER_PLUGIN_LOAD` | 插件加载前后 |
| `BEFORE_APP_BUILD` / `AFTER_APP_BUILD` | AppRuntime 组装前后 |
| `BEFORE_COMPACT` / `AFTER_COMPACT` | 上下文压缩前后 |
| `BEFORE_WORKFLOW_STEP` / `AFTER_WORKFLOW_STEP` | 工作流步骤前后 |
| `BEFORE_EVAL_RUN` / `AFTER_EVAL_RUN` | 评估运行前后 |

### HookContext

```python
@dataclass
class HookContext:
    hook_type: HookType
    payload: dict[str, Any]
    _abort: bool = False
    _abort_code: str = ""
    _abort_reason: str = ""

    def abort(reason, code=None, message=None, details=None):
        """中断钩子链"""

    @property
    def aborted(self) -> bool:
        """是否被中断"""
```

### HookManager

```python
class HookManager:
    def register(hook_type, callback, name=None, priority=200, enabled=True) -> str
    def unregister(name)
    def enable(name) / disable(name)
    def fire(hook_type, payload) -> HookContext
    def list_hooks() -> list[dict]
```

### 可中断钩子（_MUTABLE_HOOKS）

以下钩子支持通过 `ctx.abort()` 中断操作：

- `BEFORE_TOOL_CALL`
- `BEFORE_MODEL_CALL` / `AFTER_MODEL_CALL`
- `BEFORE_LLM_CALL`
- `BEFORE_SHELL_EXEC`
- `BEFORE_MCP_CALL_TOOL`
- `BEFORE_RAG_SEARCH`

## providers/ — LLM 提供者抽象层

### 架构

```
AgentLLM.think()
    │
    ▼
select_provider(model, base_url)
    │
    ├── anthropic/* → None (回退 LiteLLM)
    ├── Azure URL   → None (回退 LiteLLM)
    └── 其他        → OpenAIProvider
                        │
                        ▼
                  stream_chat() → StreamResult
```

### BaseLLMProvider（抽象基类）

```python
class BaseLLMProvider(ABC):
    @abstractmethod
    def stream_chat(messages, model, api_key, base_url,
                    temperature, tools, max_tokens, timeout,
                    parallel_tool_calls, stream_options,
                    reasoning_effort, on_token) -> StreamResult
```

### StreamResult

```python
@dataclass
class StreamResult:
    text: str = ""
    tool_calls: list[dict] = []
    reasoning_content: str = ""
    usage: dict[str, int] = {}
    finish_reason: str = ""

    @property
    def truncated(self) -> bool:
        return self.finish_reason in ("length", "max_tokens")
```

## judge_llm.py — 独立 Judge LLM

**设计原则**：Judge 使用与 Generator 不同的模型族，避免自评偏差（15-30% 高估）。

```python
def get_judge_llm() -> AgentLLM:
    """返回 Judge LLM 单例（默认 GLM-4.7-Flash，与 DeepSeek Generator 不同族）"""
```

## pricing.py — Token 定价

### 定价表（CNY/百万 tokens）

| 模型 | 输入价格 | 输出价格 |
| --- | --- | --- |
| deepseek-v3 | 1.0 | 2.0 |
| deepseek-v4-flash | 0.6 | 1.2 |
| deepseek-v4-pro | 1.0 | 4.0 |
| deepseek-r1 | 4.0 | 16.0 |
| qwen-max | 2.5 | 10.0 |
| gpt-4o | 17.5 | 70.0 |
| claude-3.5-sonnet | 18.0 | 90.0 |
| claude-4 | 90.0 | 450.0 |

```python
def estimate_cost(input_tokens, output_tokens, model) -> float:
    """估算 CNY 成本"""
```

## pii.py — PII 检测与脱敏

### 检测模式

| 类型 | 正则模式 |
| --- | --- |
| 邮箱 | `[\w.-]+@[\w.-]+\.\w+` |
| 手机号 | `1[3-9]\d{9}` |
| API Key | `sk-[A-Za-z0-9]{32,}` |
| 银行卡 | `\b\d{15,19}\b` |

### 函数

```python
def contains_pii(text: str) -> bool    # 检测是否含 PII
def mask_pii(text: str) -> str         # 脱敏处理
```

## 模块依赖关系

```
Settings (config.py)
    │
    ├──→ AgentLLM (llm.py)
    │       ├──→ ModelCapabilities (capabilities.py)
    │       ├──→ ProviderRouter (providers/router.py)
    │       │       └──→ OpenAIProvider (providers/openai_provider.py)
    │       ├──→ HookManager (hooks.py)
    │       └──→ TraceManager (observability/tracer.py)
    │
    ├──→ JudgeLLM (judge_llm.py)
    │       └──→ AgentLLM
    │
    ├──→ Pricing (pricing.py)
    ├──→ PII (pii.py)
    └──→ TextUtils (text_utils.py)
```
