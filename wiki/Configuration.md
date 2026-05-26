# ⚙ 配置参考

## 优先级

```
YAML 文件 (config.yaml)  >  环境变量 (AGENTNEXUS_*)  >  Pydantic 默认值
```

- 嵌套模型用 `__` 双下划线：`AGENTNEXUS_MCP_SERVERS__0__NAME`
- 数据根目录 `AGENTNEXUS_HOME`（默认 `~/.agentnexus`）

## 关键路径

| 数据 | 默认路径 | 环境变量 |
|------|----------|----------|
| 向量库 | `{HOME}/chroma/` | `AGENTNEXUS_CHROMA_PERSIST_DIR` |
| 记忆 | `{HOME}/memory.db` | `AGENTNEXUS_MEMORY_DB_PATH` |
| 追踪 | `{HOME}/traces/` | `AGENTNEXUS_TRACES_DIR` |
| 知识库目录 | `{HOME}/rag_catalog.db` | `AGENTNEXUS_RAG_CATALOG_DB_PATH` |
| 配置文件 | `{HOME}/config.yaml` | — |

## 配置项速览

### LLM（5 项）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `llm_api_key` | — | **必填** |
| `llm_model_id` | `deepseek/deepseek-v4-flash` | 含 provider 前缀 |
| `llm_base_url` | `https://api.deepseek.com` | |
| `llm_timeout` | `60` | 秒 |
| `model_tool_calling/json_mode/thinking` | `None` | 能力检测覆盖 |

### Judge 模型（3 项）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `judge_model_id` | `zhipu/glm-4.7-flash` | 不同模型家族避虚高 |
| `judge_api_key` | — | |
| `judge_base_url` | `https://open.bigmodel.cn/api/paas/v4/` | |

### Agent（1 项）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `max_agent_steps` | `5` | 最大 ReAct 循环步数 |

### 外部服务（2 项）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `tavily_api_key` | — | web_search 所需 |
| `e2b_api_key` | — | 云端沙箱 |

### RAG（9 项）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enable_contextual_retrieval` | `False` | 摄取时 LLM 生成上下文 |
| `enable_query_rewrite` | `True` | 口语化→关键词 |
| `enable_multi_query` | `True` | N 个变体检索 |
| `enable_hyde` | `False` | 假设文档嵌入 |
| `rag_multi_query_count` | `3` | |
| `rag_context_window` | `1` | 命中块前后 N 块 |
| `rag_context_max_chunks` | `6` | 扩展后最大块数 |

### 嵌入（2 项）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `embedding_model` | `BAAI/bge-small-zh-v1.5` | 384 维 |
| `reranker_model` | `BAAI/bge-reranker-v2-m3` | CrossEncoder |

### 记忆（2 项）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `max_memories` | `1000` | 超限触发驱逐 |
| `memory_ttl_days` | `90` | 过期天数 |

### 代码执行（5 项）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `code_execution_backend` | `auto` | auto/e2b/native/docker/disabled/local_unsafe |
| `code_execution_timeout` | `30` | 秒 |
| `code_execution_memory_mb` | `256` | Docker 内存 |
| `code_execution_docker_image` | `python:3.11-slim` | |
| `code_execution_allow_unsafe_local` | `False` | 裸 subprocess |

### Shell（5 项）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `shell_enabled` | `True` | |
| `shell_confirm` | `True` | |
| `shell_timeout` | `30` | |
| `shell_blacklist` | `[]` | 自定义黑名单 |
| `shell_execution_backend` | `auto` | |

### MCP（2 项 + N 服务器）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `mcp_enabled` | `False` | |
| `mcp_startup_timeout` | `15` | 秒 |
| `mcp_servers` | `[]` | 每项含 transport/command/url/risk_level 等 |

### Skill（8 项）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `default_skill` | `""` | qualified_id 格式 |
| `skill_auto_route` | `True` | |
| `skill_auto_route_llm_fallback` | `True` | |
| `skill_auto_route_min_score` | `2.0` | |
| `skill_auto_route_margin` | `0.75` | |

### 其他

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `trace_retention_days` | `30` | |
| `file_read_max_mb` | `10.0` | |
| `runtime_profile` | `default` | |
| `extensions_dirs` | `[]` | |
