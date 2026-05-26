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
