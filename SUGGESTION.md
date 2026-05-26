⚠️ 核心问题
1. 上帝对象集中
文件	行数	问题
agents/re_act_agent.py	760行	单个类含 30+ 方法，融合了提示构建/JSON 修复/策略降级/工具执行/记忆交互/跟踪——至少应拆出 JSON 工具函数
core/config.py:Settings	235行	40+ 个字段的扁平配置类，所有子系统都依赖它，任何系统改动都可能波及此文件
agents/react_types.py:ExecutionContext	141行	17 个字段的"可变上下文袋"，多处类型为 Any
2. 文件职责过载
文件	行数	混入的职责数
tools/mcp_adapter.py	1017行	MCP 连接生命周期 + 健康检查 + 重连 + 工具/资源/提示注册 + 结果规范化（~5个职责）
rag/loaders.py	698行	7 种格式加载器混在一个文件（PDF/MD/HTML/JSON/DOCX/XLSX/TXT）
rag/retriever.py	666行	BM25 索引 + 混合检索 + 查询改写/扩展/HyDE + 上下文扩展 + 引用构建（~5个职责）
memory/manager.py	597行	5 层压缩金字塔 + PII 脱敏 + 工具结果卸载 + LLM 记忆提取 + 断路器（~4个职责）
3. 分层违规
- cli/audit.py（72行）包含全局审计缓冲区（ThreadSafeAuditLog），但被 tools/ 模块 append_audit 导入。全局状态应属于 observability/ 或 tools/，而非 CLI 层。
- cli/config.py 和 cli/skill_cmd.py 直接导入 core/config.py 的私有函数（_config_dir, _load_yaml, _write_yaml_config），内部 API 边界未执行。
- cli/kb.py（226行）包含大量内联业务逻辑（ingestion、Chroma upsert、hybrid search fusion），本应下放到 rag/ 模块。
4. 模块间耦合
rag/chroma_client.py  ←── memory/long_term.py
                         └── memory/manager.py
                         └── tools/memory_save.py
                         └── tools/memory_search.py
                         └── tools/kb_search.py
chroma_client.py 是中心耦合点——被 memory 和 tools 两个不同领域直接依赖。它自身又混合了 embedding 模型管理、ChromaDB 客户端、CRUD 操作，搞成了 RAG 层的"上帝模块"。
5. 部分边界模糊
区域	边界清晰度	说明
observability/	✅ 清晰	tracing vs stats 分工明确
tools/（大部分）	✅ 清晰	单文件单职责（file_ops, shell, web_search, subagent）
agents/	⚠️ 外部清晰内部模糊	run() 接口明确，但 ReActAgent 类内部混杂
rag/	⚠️ 部分模糊	chroma_client/retriever/loaders 三文件过载
cli/	⚠️ 部分模糊	audit 层挫，kb 过重，config 访问内部 API
memory/	⚠️ 基本清晰	manager.py 是例外（PII 脱敏不属于记忆管理）
6. 代码优雅性
- 优秀示例: fsm.py（102行，纯机械）、react_transitions.py（49行，声明式）、short_term.py（101行，干净抽象）
- 需改进: mcp_adapter.py（1017行，if/else 嵌套多）、re_act_agent.py 的 _call_llm 方法（~165行单块）
  三、总结评分
  维度	评分	理由
  组件内聚	⚠️ 6/10	多数文件高内聚，但 5 个"过载文件"拉了后腿
  模块间耦合	⚠️ 6/10	chroma_client 和 Settings 是耦合热点，但整体未出现循环依赖
  边界清晰度	⚠️ 6/10	observability/tools(大部分) 清晰；rag/cli 模糊；层挫违规 3 处
  代码优雅性	⚠️ 7/10	FSM/registry/short_term 优秀；mcp_adapter/re_act_agent 需拆
  架构整体合理性	✅ 7/10	分层 + 服务化方向正确，核心抽象选择合理（FSM/DAG/Registry），但部分实现（尤其是 MCP 和 ReActAgent）已超出合理规模

  额外建议: 
- 将 PII 脱敏提取到独立的 core/pii.py
- 将 cli/audit.py 的全局状态迁移到 observability/ 
- 考虑对 Settings 按领域拆分子模型（LLMSettings, RAGSettings, MemorySettings）