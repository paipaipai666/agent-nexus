> **[中文](Getting-Started.md) | [English](Getting-Started.en.md)**

# 🚀 快速开始

## 安装

```bash
pip install -e ".[dev,eval]"   # Python 3.11+
```

可选运行时：
- `sentence-transformers` — 本地嵌入模型（否则回退到哈希词袋）
- `pytesseract` — PDF OCR
- `scipy` — 评估校准

## 初始化

```bash
nexus init
```

交互式输入：
- **LLM API Key**（必填）
- **模型 ID**（默认 `deepseek/deepseek-v4-flash`）
- **Base URL**（默认 `https://api.deepseek.com`）

配置写入 `~/.agentnexus/config.yaml`。

## 启动 TUI

```bash
nexus tui
```

快捷键：`Ctrl+Q` 退出，`Ctrl+L` 清屏，`Tab` 焦点切换。

恢复上一次会话：

```bash
nexus --continue                   # 恢复最近一次 TUI 会话
nexus --continue <session_id>      # 恢复指定会话
```

## 启动 API 服务

```bash
nexus serve                        # 默认 8000 端口
nexus serve --port 3000            # 指定端口
nexus serve --no-auth              # 禁用认证
```

启动 HTTP/WebSocket API 服务，供桌面 GUI 客户端连接。

## Wiki 快速开始

```bash
nexus wiki init my-project         # 初始化 wiki 命名空间
nexus wiki ingest ./docs -n my-project  # 摄入文档
nexus wiki query "问题内容" -n my-project  # 查询 wiki
nexus wiki stats -n my-project     # 查看健康统计
```

Wiki 提供基于置信度路由的 RAG 查询、文档摄入、健康检查和审核流程。详见 [命令参考](Commands.md) 中的 `nexus wiki` 部分。

## 基础工作流

```bash
nexus tui                           # TUI 对话
nexus kb add ./docs                 # 添加知识库
nexus kb search "查询内容" --top-k 5 # 搜索知识库
nexus stats --days 7                # 查看成本
nexus eval agent --days 1           # 评估 Agent 质量
nexus audit -n 10                   # 审计近期工具调用
```

## 配置查看

```bash
nexus config                        # 列出全部配置
nexus config --set max_agent_steps --value 10
```

## 下一步

- [⌨ 命令参考](Commands.md)
- [⚙ 配置详解](Configuration.md)
- [🤖 Agent 执行引擎](ReAct-Agent.md)
