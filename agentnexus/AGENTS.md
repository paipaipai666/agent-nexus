# AGENTS.md — AgentNexus

LangGraph 编排的多智能体任务协同 CLI 工具。Python 3.11+, 纯本地运行 (ChromaDB + SQLite + JSONL)。

## 项目结构

```
agentnexus/                    ← 仓库根目录
└── agentnexus/                ← pip 包根目录 (pyproject.toml 所在)
    ├── agentnexus/            ← 源码包
    │   ├── __main__.py        ← python -m 入口
    │   ├── cli/               ← Typer CLI 层 (run, chat, kb, logs, …)
    │   ├── agents/            ← Agent 实现 + LangGraph FSM
    │   │   └── multi_agent/   ← orchestrator.py (状态机核心) + state.py (AgentState)
    │   ├── core/              ← config.py (Pydantic Settings) + llm.py (AgentLLM)
    │   ├── prompts/           ← .txt 提示词模板 (str.format, 非 Jinja2)
    │   ├── rag/               ← ChromaDB + BM25 + Reranker + Grep 双路由检索
    │   ├── memory/            ← 短期 deque + 长期 SQLite+向量
    │   ├── observability/     ← JSONL Trace + Token 统计
    │   └── tools/             ← 代码执行 / Web 搜索 / 工具调度
    └── tests/
        ├── unit/              ← pytest class-based 单元测试
        ├── integration/       ← pytest function-based 集成测试
        ├── regression/        ← 全功能回归测试 (原 test_all.py 已迁移至此)
        ├── evals/             ← 空 (评估数据集待填充)
        └── conftest.py        ← temp_agentnexus_home / mock_llm fixtures
```

**⚠ 安装必须在 `agentnexus/` 目录 (不是仓库根目录):**
```bash
cd agentnexus
pip install -e ".[dev,eval]"
```

## 开发命令 (严格顺序)

```bash
# 开发安装 (在 agentnexus/ 下)
pip install -e ".[dev,eval]"

# Lint
ruff check agentnexus/ tests/

# 运行测试 (CI 入口，覆盖 unit + integration + regression)
python -m pytest tests/ -v

# PyInstaller 打包 (需先在 agentnexus.spec 检查 hiddenimports)
pyinstaller agentnexus.spec --noconfirm
```

**注意**: CI 中 test 步骤已不再使用 `continue-on-error`，测试失败会导致 CI 红灯。

## 架构关键点

### 执行入口链

```
nexus run <task> → cli/run.py → orchestrator_persistent.invoke()
nexus chat       → cli/chat.py → ReActAgent (单 Agent 循环)
nexus init       → cli/config.py
```

### LangGraph 状态机 (orchestrator.py)

```
START → plan → research → code → execute(+HITL) → analyst → END
         ↑        │          │        │               │
         │        │          │        ├─ failure → code (重试)
         │        │          │        └─ ModuleNotFoundError → research
         │        │          │
         │        └─ no code step → analyst
         │
         └─── analyst crit < 7.0 or hard_verdict → plan (重规划)
```

- FSM 有 5 个节点: plan, research, code, execute, analyst
- 4 个条件路由器: `route_after_plan`, `route_after_research`, `route_after_execute`, `route_after_analyst`
- 最大重试次数: `MAX_RETRIES = 3`
- Critic 评分阈值: 7.0/10，低于阈值回 plan 重规划

### AgentState (TypedDict, 无运行时校验)

`multi_agent/state.py` — 34 个字段。`validate_state()` 在关键节点入口做防御性类型检查。
- `messages` 字段用了 `Annotated[list, operator.add]` (LangGraph reducer: 追加式)
- code/critic/excution 相关字段通过 `state.get()` 安全访问

### 提示词管理

- 所有提示词在 `prompts/*.txt`，用 `str.format()` 注入变量 (非 Jinja2)
- `load_prompt(name)` → 读取 `{name}.txt` 原始字符串
- `format_prompt(name, **kwargs)` → 自动注入 `{date}` (UTC 日期)
- 共 12 个提示词文件: planner, analyst, coder, critic, research, react, contextual, memory_extract, memory_summarize, eval_* (3个)

### ChromaDB 双客户端 ⚠

RAG 和长期记忆各自创建独立的 `chromadb.PersistentClient`，指向同一个 `chroma_persist_dir`：
- RAG: `rag/chroma_client.py::get_chroma_client()` → collection `"documents"` (单例缓存)
- LTM: `memory/long_term.py::_get_ltm_collection()` → collection `"long_term_memories"` (每次重建)

BM25 索引仅在内存中，不持久化，每次会话重建。

### 弃用文件

- `retry_manager.py`: **真正废弃** (无运行时导入)。`critic_agent.py` 和 `critic_rules.py` 仍在正常运行。

新增功能时，如果新增了动态导入的依赖，必须同步更新 `agentnexus.spec` 中的 `hiddenimports`，否则 PyInstaller 打包会缺少依赖。

## 错误处理

### ErrorType (9 类)

`agents/schema.py` 定义：
- `MISSING_CODE`, `RUNTIME_ERROR`, `HALLUCINATION`, `TOOL_FAILURE`
- `SCHEMA_VIOLATION`, `NO_OUTPUT`, `EMPTY_RESULT`, `LOGIC_ERROR`, `TRUNCATION`

每种错误有对应的 `RETRY_STRATEGIES[ErrorType]`（策略 + 最大重试次数 + 提示指令）。

代码执行失败时 `route_after_execute` 的路由逻辑:
- 成功 → analyst
- `ModuleNotFoundError` or (retry≥2 and NO_OUTPUT) → research (查 API 文档)
- 其他 → code (修复重试)
- 超过 MAX_RETRIES → 强制 analyst

### `__main__` 自动追加

`_ensure_main_block()` 用 AST 解析代码，检测缺失的 `if __name__ == '__main__':` 块，自动追加顶层函数调用。不修改已有 `__main__` 块的代码。

## Pydantic Schema

核心输出模型 (`agents/schema.py`):
- `SourceClaim` — claim + source + confidence [0-1]
- `ResearchOutput` — summary + claims + gaps
- `CodeOutput` — reasoning + code + expected_output
- `ExecutionResult` — success + stdout + stderr + exception + exit_code
- `CriticVerdict` — passed + score [0-10] + feedback + fail_reason
- `OutputDiff` — matched + expected + actual + detail

## 配置系统

优先级: YAML 文件 (`~/.agentnexus/config.yaml`) → 环境变量 (`AGENTNEXUS_*`) → Pydantic defaults

关键环境变量:
- `AGENTNEXUS_HOME` — 数据根目录 (默认 `~/.agentnexus`)，控制 chroma/memory.db/traces 路径
- `AGENTNEXUS_LLM_API_KEY` — API Key (必须)
- `AGENTNEXUS_LLM_MODEL_ID` — 模型 ID (默认 `deepseek/deepseek-v4-flash`)
- `AGENTNEXUS_Tavily_API_KEY` / `AGENTNEXUS_E2B_API_KEY` — 可选外部服务

测试必须设置 `AGENTNEXUS_HOME` 到临时目录，使用 `conftest.py` 提供的 `temp_agentnexus_home` fixture。

## LLM 调用

`core/llm.py::AgentLLM`:
- 通过 litellm 流式调用
- 3 次指数退避重试 (`LLM_RETRY_BASE_DELAY=2.0`)
- 自动检测 `finish_reason=="length"` → `self.last_truncated = True`
- 瞬时错误 (connection/ssl/timeout/429/503) → 自动重试；非瞬时错误 → 直接返回空
- model ID 如果无 `/` 前缀会自动根据 base_url 推断 provider 前缀

## 测试约定

- 统一入口: `python -m pytest tests/ -v` (CI 和本地一致)
- 目录: `unit/` (class-based) / `integration/` (function-based) / `regression/` (全功能回归，原 test_all.py)
- `conftest.py` — 提供 `temp_agentnexus_home` (临时数据目录), `mock_llm` (mock AgentLLM.think)
- 需要 ChromaDB/SQLite 隔离的测试使用 `temp_agentnexus_home` fixture
- CLI 测试用 `typer.testing.CliRunner` + `isolated_filesystem()`

## Trace 可观测性

`observability/tracer.py`:
- 线程安全的 TraceManager 单例
- 每次 `nexus run` 创建 TraceContext → 各节点通过 `_trace_wrapper` 自动记录 span
- 输出: `~/.agentnexus/traces/{YYYY-MM-DD}.jsonl`
- ⚠ span 只在 `end_trace()` 时 flush，crash 丢未 flush 数据
- 输入输出截断 1000 字符

## CI

- 触发: push/PR to `main`
- 步骤: lint (ruff) → test (`python -m pytest tests/ -v`) → eval sanity check (需 API key secret)
- 发布: push `v*` tag → PyInstaller 跨平台构建 (ubuntu + windows) → GitHub Release

## 已知问题 / 注意点

- `plan.md` 描述的 critic 阶段在计划中但在当前 FSM 中已移除独立节点——现为 analyst 内部调用 critic 评分
- long_term.py 每次 save/search 都重建 ChromaDB client，频繁调用有性能开销
- BM25 索引不持久化，重启后需重建
- PII 过滤 (memory/manager.py) 用正则完全屏蔽 email/电话/API key，不含部分脱敏
