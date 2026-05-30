> **[中文](Architecture.md) | [English](Architecture.en.md)**

# 🏗 整体架构

## 分层架构

```
┌───────────────────────────────────────────────────┐
│              CLI 层 (Typer + Rich)                 │
│  6 顶层命令 + 5 子命令组 = 31 入口                  │
│  nexus init / config / tui / stats / audit / ver  │
│  nexus kb / memory / logs / eval / skill           │
└──────────────────┬────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────┐
│              服务外观层 (Services)                  │
│  ChatService  │  SkillService  │  AppServices      │
│  会话管理/事件  │  Skill 路由/前置 │  组合各类服务    │
└──────────────────┬────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────┐
│           ReActAgent (FSM 驱动)                    │
│  16 状态 × 25 转移规则                              │
│  CallingStrategy 三级: Native → JSON → Prompt JSON │
│  AgentLLM (litellm 流式, 3 次指数退避)              │
│  工具 batch 顺序执行, max_steps 硬终止               │
└──────────────────┬────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────┐
│         ToolRegistry 治理网关 (7 道关卡)             │
│  RBAC → Schema → 限流 → 超时 → 风险 → HITL → 审计  │
│  17 内置工具 + MCP 动态导入 + 子代理隔离              │
└──────────────────┬────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────┐
│              本地基础设施层                          │
│  ChromaDB(向量)  SQLite(关系型)  JSONL(追踪)        │
│  SentenceTransformers + BM25 + BGE-Reranker        │
│  E2B / bubblewrap / Docker / 本地沙箱               │
└───────────────────────────────────────────────────┘
```

## 项目结构

```
agentnexus/
├── __main__.py              ── python -m 入口
├── app/runtime.py            ── AppRuntime 依赖组装
├── cli/                      ── Typer CLI 层
├── agents/                   ── ReActAgent + FSM
├── core/                     ── Settings + LLM
├── evaluation/               ── 8 个评估器
├── extensions/               ── 插件系统
├── memory/                   ── STM/LTM/版本控制
├── observability/            ── Trace + Token 统计
├── prompts/                  ── 17 个 .txt 模板
├── rag/                      ── ChromaDB 客户端/检索/分块
├── services/                 ── 服务外观层
├── skills/                   ── Skill 发现/路由/运行时
├── tools/                    ── 注册表/提供者/MCP
└── tui/                      ── Textual 界面
```

## 服务启动顺序

`AppRuntime.build()` 按以下顺序组装所有组件：

1. 加载 `Settings`（Pydantic 懒加载单例）
2. 创建 `AgentLLM` + `ToolExecutor` + `ConfirmBridge`
3. 初始化 `MCPToolManager`（若 `mcp_enabled=True`）
4. 加载 `ExtensionManager`
5. `register_all_tools()` — 注册 8 个提供者 + MCP
6. 创建 `MemoryManager` + `ConversationVersionManager`
7. 创建 `ReActAgent`
8. `SkillRegistry.discover()` — 扫描 skill 目录
9. 创建 `SkillService`
10. 配置 Trace 输出目录
11. 组装 `AppServices`（Chat/Config/Eval/KB/Skill）
12. 返回 `AppRuntime` 实例

> 见 [ReAct Agent](ReAct-Agent.md) 了解 FSM 细节，[工具治理](Tool-Governance.md) 了解 7 道关卡。
