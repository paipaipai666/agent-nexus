# AGENTS.md — AgentNexus

ReAct 单智能体任务协同 CLI 工具。Python 3.11+, 纯本地运行 (ChromaDB + SQLite + JSONL)。

## 项目结构

```
<repo-root>/                  ← 仓库根目录（`pyproject.toml` 在这里）
├── .github/                  ← CI / Release workflows
├── agentnexus/               ← 源码包
│   ├── __main__.py           ← python -m 入口
│   ├── cli/                  ← Typer CLI 层 (kb, memory, logs, eval, stats, config, init, audit, tui)
│   ├── agents/               ← Agent 实现 (ReActAgent)
│   ├── core/                 ← config.py (Pydantic Settings) + llm.py (AgentLLM)
│   ├── prompts/              ← .txt 提示词模板 (str.format, 非 Jinja2)
│   ├── rag/                  ← ChromaDB + BM25 + Reranker + Grep 双路由检索
│   ├── memory/               ← 短期 deque + 长期 SQLite+向量
│   ├── observability/        ← JSONL Trace + Token 统计
│   └── tools/                ← 代码执行 / Web 搜索 / 工具调度
├── tests/
│   ├── unit/                 ← pytest class-based 单元测试
│   ├── integration/          ← pytest function-based 集成测试
│   ├── regression/           ← 全功能回归测试 (原 test_all.py 已迁移至此)
│   ├── evals/                ← 空 (评估数据集待填充)
│   └── conftest.py           ← temp_agentnexus_home / mock_llm fixtures
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── agentnexus.spec
├── pyproject.toml
└── requirements.txt
```

**⚠ 安装直接在仓库根目录进行:**
```bash
pip install -e ".[dev,eval]"
```

## 开发命令 (严格顺序)

```bash
# 开发安装 (在仓库根目录下)
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

### 执行入口

```
nexus init       → agentnexus/cli/config.py
nexus config     → agentnexus/cli/config.py
nexus kb         → agentnexus/cli/kb.py
nexus memory     → agentnexus/cli/memory_cmd.py
nexus logs       → agentnexus/cli/logs.py
nexus stats      → agentnexus/cli/stats.py
nexus eval       → agentnexus/cli/eval_cmd.py
nexus audit      → agentnexus/cli/audit.py
nexus tui        → agentnexus/cli/tui_cmd.py
```

### 提示词管理

- 所有提示词在 `agentnexus/prompts/*.txt`，用 `str.format()` 注入变量 (非 Jinja2)
- `load_prompt(name)` → 读取 `{name}.txt` 原始字符串
- `format_prompt(name, **kwargs)` → 自动注入 `{date}` (UTC 日期)
- 共 7 个提示词文件: react, contextual, memory_extract, memory_summarize, eval_* (3个)

### ChromaDB 双客户端 ⚠

RAG 和长期记忆各自创建独立的 `chromadb.PersistentClient`，指向同一个 `chroma_persist_dir`：
- RAG: `agentnexus/rag/chroma_client.py::get_chroma_client()` → collection `"documents"` (单例缓存)
- LTM: `agentnexus/memory/long_term.py::_get_ltm_collection()` → collection `"long_term_memories"` (每次重建)

BM25 索引仅在内存中，不持久化，每次会话重建。

### 已移除

多 Agent LangGraph 编排器 (`agentnexus/agents/multi_agent/`) 及子 Agent (`coder_agent.py`, `research_agent.py`, `executor_agent.py`, `critic_agent.py`, `critic_rules.py`, `analyst_agent.py`, `schema.py`) 已在清理中移除；当前 CLI 仅保留 kb / memory / logs / eval / stats / config / init / audit / tui / version 等入口。

新增功能时，如果新增了动态导入的依赖，必须同步更新 `agentnexus.spec` 中的 `hiddenimports`，否则 PyInstaller 打包会缺少依赖。

## `__main__` 自动追加

`_ensure_main_block()` 用 AST 解析代码，检测缺失的 `if __name__ == '__main__':` 块，自动追加顶层函数调用。不修改已有 `__main__` 块的代码。

(该功能原为多 Agent 编排器的 coder_agent 实现，现保留为共享工具方法。)

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
- TraceContext 由对应执行流程创建 → 各节点通过 `_trace_wrapper` 自动记录 span
- 输出: `~/.agentnexus/traces/{YYYY-MM-DD}.jsonl`
- ⚠ span 只在 `end_trace()` 时 flush，crash 丢未 flush 数据
- 输入输出截断 1000 字符

## CI

- 触发: push/PR to `main`
- 步骤: lint (ruff) → test (`python -m pytest tests/ -v`) → eval sanity check (需 API key secret)
- 发布: push `v*` tag → PyInstaller 跨平台构建 (ubuntu + windows) → GitHub Release

## 已知问题 / 注意点

- long_term.py 每次 save/search 都重建 ChromaDB client，频繁调用有性能开销
- BM25 索引不持久化，重启后需重建
- PII 过滤 (memory/manager.py) 用正则完全屏蔽 email/电话/API key，不含部分脱敏
