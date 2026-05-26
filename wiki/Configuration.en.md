> **[中文](Configuration.md) | [English](Configuration.en.md)**

# ⚙ Configuration Reference

## Priority

```
YAML file (config.yaml)  >  Environment variables (AGENTNEXUS_*)  >  Pydantic defaults
```

- Nested models use `__` double underscore: `AGENTNEXUS_MCP_SERVERS__0__NAME`
- Data root `AGENTNEXUS_HOME` (default `~/.agentnexus`)

## Key Paths

| Data | Default Path | Environment Variable |
|------|----------|----------|
| Vector store | `{HOME}/chroma/` | `AGENTNEXUS_CHROMA_PERSIST_DIR` |
| Memory | `{HOME}/memory.db` | `AGENTNEXUS_MEMORY_DB_PATH` |
| Traces | `{HOME}/traces/` | `AGENTNEXUS_TRACES_DIR` |
| Knowledge base catalog | `{HOME}/rag_catalog.db` | `AGENTNEXUS_RAG_CATALOG_DB_PATH` |
| Config file | `{HOME}/config.yaml` | — |

## Configuration Items

### LLM (5 items)

| Field | Default | Description |
|------|--------|------|
| `llm_api_key` | — | **Required** |
| `llm_model_id` | `deepseek/deepseek-v4-flash` | Includes provider prefix |
| `llm_base_url` | `https://api.deepseek.com` | |
| `llm_timeout` | `60` | Seconds |
| `model_tool_calling/json_mode/thinking` | `None` | Capability detection override |

### Judge Model (3 items)

| Field | Default | Description |
|------|--------|------|
| `judge_model_id` | `zhipu/glm-4.7-flash` | Different model family to avoid inflation |
| `judge_api_key` | — | |
| `judge_base_url` | `https://open.bigmodel.cn/api/paas/v4/` | |

### Agent (1 item)

| Field | Default | Description |
|------|--------|------|
| `max_agent_steps` | `5` | Max ReAct loop steps |

### External Services (2 items)

| Field | Default | Description |
|------|--------|------|
| `tavily_api_key` | — | Required for web_search |
| `e2b_api_key` | — | Cloud sandbox |

### RAG (9 items)

| Field | Default | Description |
|------|--------|------|
| `enable_contextual_retrieval` | `False` | LLM-generated context during ingestion |
| `enable_query_rewrite` | `True` | Conversational→keyword |
| `enable_multi_query` | `True` | N variant retrievals |
| `enable_hyde` | `False` | Hypothetical document embedding |
| `rag_multi_query_count` | `3` | |
| `rag_context_window` | `1` | N blocks before/after hit |
| `rag_context_max_chunks` | `6` | Max chunks after expansion |

### Embedding (2 items)

| Field | Default | Description |
|------|--------|------|
| `embedding_model` | `BAAI/bge-small-zh-v1.5` | 384 dimensions |
| `reranker_model` | `BAAI/bge-reranker-v2-m3` | CrossEncoder |

### Memory (2 items)

| Field | Default | Description |
|------|--------|------|
| `max_memories` | `1000` | Overflow triggers eviction |
| `memory_ttl_days` | `90` | Expiration days |

### Code Execution (5 items)

| Field | Default | Description |
|------|--------|------|
| `code_execution_backend` | `auto` | auto/e2b/native/docker/disabled/local_unsafe |
| `code_execution_timeout` | `30` | Seconds |
| `code_execution_memory_mb` | `256` | Docker memory |
| `code_execution_docker_image` | `python:3.11-slim` | |
| `code_execution_allow_unsafe_local` | `False` | Bare subprocess |

### Shell (5 items)

| Field | Default | Description |
|------|--------|------|
| `shell_enabled` | `True` | |
| `shell_confirm` | `True` | |
| `shell_timeout` | `30` | |
| `shell_blacklist` | `[]` | Custom blacklist |
| `shell_execution_backend` | `auto` | |

### MCP (2 items + N servers)

| Field | Default | Description |
|------|--------|------|
| `mcp_enabled` | `False` | |
| `mcp_startup_timeout` | `15` | Seconds |
| `mcp_servers` | `[]` | Each with transport/command/url/risk_level etc. |

### Skill (8 items)

| Field | Default | Description |
|------|--------|------|
| `default_skill` | `""` | qualified_id format |
| `skill_auto_route` | `True` | |
| `skill_auto_route_llm_fallback` | `True` | |
| `skill_auto_route_min_score` | `2.0` | |
| `skill_auto_route_margin` | `0.75` | |

### Other

| Field | Default | Description |
|------|--------|------|
| `trace_retention_days` | `30` | |
| `file_read_max_mb` | `10.0` | |
| `runtime_profile` | `default` | |
| `extensions_dirs` | `[]` | |
