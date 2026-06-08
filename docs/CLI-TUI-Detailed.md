> **[中文](CLI-TUI-Detailed.md) | [English](CLI-TUI-Detailed.en.md)**

# 🖥 CLI 与 TUI 模块（详细版）

## 概述

- **CLI** 基于 **Typer** 框架，入口命令为 `nexus`，提供 6 个顶级命令 + 7 个子命令组，共 40+ 个入口
- **TUI** 基于 **Textual** 框架，使用 **Catppuccin Mocha** 主题，提供终端原生聊天界面

## CLI 命令结构

### 完整命令树

```
nexus
├── version                          -- 版本号 (v0.1.0)
├── config [key] [value]             -- 查看/修改配置
├── init                             -- 首次初始化向导
├── serve [--port] [--host]          -- 启动 HTTP/WebSocket 服务器
├── health                           -- 系统健康检查
├── stats [--days]                   -- Token 成本统计
├── audit [--limit] [--tool]         -- 工具调用审计日志
├── alerts [--days] [--severity]     -- 告警历史
├── sessions [--limit] [--restore]   -- 会话管理
├── tui                              -- 启动终端聊天界面
├── --continue [session_id]          -- 继续上一次会话
│
├── kb                               -- 知识库管理
│   ├── add <path>                   -- 添加文档（支持 PDF/MD/TXT/HTML/JSON/DOCX/XLSX）
│   ├── list                         -- 查看知识库状态
│   └── search <query> [--top-k]     -- 搜索知识库
│
├── wiki                             -- 混合 Wiki + RAG 系统
│   ├── init <namespace>             -- 初始化 Wiki
│   ├── ingest <source> [--type]     -- 摄入源文档
│   ├── query <question> [--rag-fallback] -- 置信度路由查询
│   ├── lint [--enqueue]             -- 健康检查
│   ├── stats [--namespace]          -- 统计信息
│   ├── calibrate <sample_file>      -- 阈值校准
│   ├── full-check                   -- 完整健康检查
│   └── review                       -- 审阅队列
│       ├── list [--status]          -- 列出审阅项
│       ├── resolve <item_id>        -- 解决审阅项
│       └── process                  -- 处理超期项（自动降级）
│
├── memory                           -- 长期记忆管理
│   ├── list [--limit]               -- 列出记忆条目
│   └── clear                        -- 清除所有记忆
│
├── logs                             -- 跟踪日志查看
│   ├── list [--days]                -- 列出历史跟踪
│   └── view --trace-id <id>         -- 查看 Trace 的 Span 树
│
├── skill                            -- Skill/工作流管理
│   ├── list                         -- 列出所有 Skill
│   ├── init <target> [--workflow]   -- 创建 Skill 模板
│   ├── validate [target]            -- 验证 Skill
│   ├── use <target>                 -- 设置默认 Skill
│   ├── reset                        -- 清除默认 Skill
│   └── status                       -- 显示 Skill 状态
│
├── codegraph                        -- 代码知识图谱
│   ├── build [--force]              -- 构建/更新图谱
│   ├── search <query> [--kind]      -- 语义搜索代码实体
│   ├── callers <symbol> [--depth]   -- 查找调用者
│   ├── callees <symbol> [--depth]   -- 查找调用目标
│   ├── inherits <cls>               -- 查看继承树
│   ├── imports <module>             -- 查看导入关系
│   ├── context <symbol>             -- 获取实体上下文
│   ├── stats                        -- 图谱统计
│   └── verify [--fix]               -- 一致性诊断
│
└── eval                             -- 评估系统（最复杂）
    ├── list                         -- 列出评估数据集
    ├── run [--ci] [--top-k]         -- 运行 RAG 评估
    ├── history                      -- 历史评估报告
    ├── compare                      -- 比较两次评估
    ├── trajectory                   -- 轨迹质量评估
    ├── ci                           -- CI 模式评估
    ├── component                    -- 组件级评估
    ├── hallucination                -- 幻觉检测
    ├── tool-selection               -- 工具选择准确率
    ├── coherence                    -- 多步推理连贯性
    ├── agent                        -- 单 Agent 质量评估
    ├── calibrate                    -- Judge 校准
    ├── humaneval                    -- HumanEval 代码评估
    ├── swe-bench                    -- SWE-bench 修复评估
    ├── task                         -- 评估任务管理
    │   ├── list [--category]        -- 列出任务
    │   ├── show <task_id>           -- 任务详情
    │   ├── validate                 -- 验证数据集
    │   └── run <task_id> [--trials] -- 运行单个任务
    ├── suite                        -- 评估套件
    │   ├── list                     -- 列出套件
    │   ├── run <name> [--trials]    -- 运行套件
    │   ├── show <name>              -- 套件详情
    │   └── baseline                 -- Baseline 管理
    │       ├── list                 -- 列出 baseline
    │       ├── save <suite>         -- 保存为 baseline
    │       └── compare <suite>      -- 与 baseline 对比
    └── transcript                   -- Transcript 管理
        ├── show <trace_id>          -- 显示完整 transcript
        ├── list [--days]            -- 列出 transcript
        ├── search [--tool] [--keyword] -- 搜索 transcript
        └── failures [--days]        -- 列出失败的 transcript
```

## TUI 架构

### 屏幕布局

```
┌──────────────────────────────────────────────────────────┐
│ Top Bar (workspace/branch/model/快捷键提示)                │
├─────────────────────────────────────────┬────────────────┤
│                                         │                │
│           Chat Area                     │  Side Panel    │
│         (消息流区域)                     │  (信息面板)    │
│                                         │                │
│                                         │  ┌ Model       │
│                                         │  ├ Task Timeline│
│                                         │  ├ Todo List   │
│                                         │  ├ Tools       │
│                                         │  ├ MCP         │
│                                         │  ├ Skill       │
│                                         │  └ Session     │
├─────────────────────────────────────────┴────────────────┤
│ Input Bar (> 输入框)                                      │
├──────────────────────────────────────────────────────────┤
│ HUD (模型 | 上下文进度条 | Token | 版本 | 工作目录)       │
└──────────────────────────────────────────────────────────┘
```

### Widget 组件

| Widget | 文件 | 说明 |
|--------|------|------|
| `ChatScreen` | `tui/screens/chat.py` | 主屏幕，所有交互逻辑核心（1757+ 行） |
| `InputBar` | `tui/widgets/input_bar.py` | 底部输入区，发送 `AppSubmit` 消息 |
| `ChatMessage` | `tui/widgets/message.py` | 聊天消息渲染，支持代码块 |
| `ToolCall` | `tui/widgets/message.py` | 工具调用展示（暖橙色边框） |
| `HUD` | `tui/widgets/hud.py` | 底部状态栏（上下文窗口 `█░` 进度条） |
| `SidePanel` | `tui/widgets/side_panel.py` | 右侧信息面板（7 个区块） |
| `ConfirmDialog` | `tui/widgets/confirm_dialog.py` | HITL 确认对话框（模态弹窗） |

### 内置斜杠命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/clear [--all]` | 清屏（`--all` 清除检查点） |
| `/undo` / `/redo` | 回退/重做检查点 |
| `/log` | 查看检查点日志 |
| `/status` | 版本状态 |
| `/compact [instructions]` | 压缩对话上下文 |
| `/stats` | 运行统计 |
| `/sessions` | 列出历史会话 |
| `/switch <session_id>` | 切换会话 |
| `/skill status/list/use/...` | Skill 管理 |
| `/mcp status/tools/...` | MCP 管理 |
| `/plugin status/list/...` | 插件管理 |
| `/exit` | 退出 TUI |

### 设计模式

| 模式 | 应用 |
|------|------|
| **Typer 子应用嵌套** | eval 模块三层嵌套（eval > task/suite > baseline） |
| **延迟导入** | CLI 命令函数内部使用延迟 `from ... import` |
| **Textual 消息总线** | 组件通过 `Message` 系统通信（`AppSubmit`、`AppInputChanged`） |
| **ChatService 抽象** | ChatScreen 通过 ChatService 封装消息发送、工具执行 |
| **响应式更新** | SidePanel 使用 `_refresh_section()` 按需更新单个区块 |

## 模块依赖关系

### CLI → 核心服务层

| CLI 模块 | 依赖的核心模块 |
|----------|---------------|
| `tui_cmd.py` | `AppRuntime`, `AgentNexusTUI` |
| `config.py` | `Settings`, `get_config_dir`, `load_config_yaml` |
| `serve_cmd.py` | `create_app`, `generate_token`, uvicorn |
| `kb.py` | `rag.ingestion`, `storage.chroma`, `rag.retriever` |
| `wiki_cmd.py` | `WikiService`, `wiki.store`, `wiki.calibration` |
| `eval/*.py` | `evaluation.*`, `rag.*`, `services.eval` |

### TUI → 核心服务层

| TUI 组件 | 依赖的核心模块 |
|----------|---------------|
| `ChatScreen` | `ChatService`, `SkillRegistry`, `trace_manager`, `ShortTermMemory` |
| `HUD` | `resolve_ctx_max`, `get_settings` |
| `SidePanel` | `collapse_and_truncate` |
