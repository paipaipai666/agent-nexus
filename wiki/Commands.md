# ⌨ 命令参考

31 个命令入口，6 顶层 + 5 子命令组。

## 全局行为

- 自动加载 `~/.agentnexus/config.yaml`
- 错误输出 stderr（Rich 格式化），退出码 0/1
- 交互模式弹 HITL 确认；`-n` 跳过

## 顶层命令

| 命令 | 说明 |
|------|------|
| `nexus init` | 首次交互式初始化（LLM Key/Model/URL） |
| `nexus config` | 查看/设置配置 (`--set <key> --value <val>`) |
| `nexus version` | 显示版本 |
| `nexus tui` | 启动 Textual TUI 对话界面 |
| `nexus stats [--days N]` | Token 成本统计 |
| `nexus audit [-n N] [-t tool]` | 工具审计日志 |

## 知识库 `nexus kb`

| 命令 | 说明 |
|------|------|
| `kb add <path>` | 添加文档（PDF/MD/TXT/HTML/JSON/DOCX/XLSX） |
| `kb list` | 知识库状态 |
| `kb search <query> [--top-k] [--view] [--source] [--format] [--section] [--page] [--block-type] [--has-code/--no-code] [--has-list/--no-list] [--heading-depth]` | 混合检索 |

## 记忆 `nexus memory`

| 命令 | 说明 |
|------|------|
| `memory list [--limit N]` | 查看长期记忆 |
| `memory clear` | 清空长期记忆 |

## 日志 `nexus logs`

| 命令 | 说明 |
|------|------|
| `logs list [--days N]` | 列出历史 Trace |
| `logs view --trace-id <id>` | 查看 Trace Span 树 |

## 评估 `nexus eval`

| 命令 | 说明 |
|------|------|
| `eval agent [--days N]` | Agent 执行质量 |
| `eval trajectory [-t ID] [-d N]` | 轨迹质量 |
| `eval component` | 组件分解评估 |
| `eval hallucination [-t ID]` | 幻觉检测 |
| `eval tool-selection` | 工具选择准确率 |
| `eval coherence [-t ID]` | 多步推理连贯性 |
| `eval list` | 列表评估数据集 |
| `eval run [--ci] [--top-k N] [--dataset ...]` | RAG 质量评估 |
| `eval history` | 历史评估报告 |
| `eval compare -b <baseline> -c <candidate>` | 对比两次评估 |
| `eval ci [-d N]` | CI 模式 |
| `eval calibrate [-o <path>] [-s <score_file>]` | Judge 校准 |
| `eval humaneval [--dataset ...] [-t ID]` | HumanEval 代码生成 |
| `eval swe-bench --dataset <path>` | SWE-bench |

## Skill `nexus skill`

| 命令 | 说明 |
|------|------|
| `skill list` | 列出所有 skill |
| `skill init <target> [--display-name] [--force] [--workflow]` | 创建 skill 模板 |
| `skill validate [<target>]` | 验证 skill 结构 |
| `skill use <target>` | 设置默认 skill |
| `skill reset` | 清除默认 skill |
| `skill status` | 当前 skill 状态 |
