> **[中文](Prompt-System.md) | [English](Prompt-System.en.md)**

# 📝 提示词系统

所有提示词位于 `agentnexus/prompts/*.txt`，使用 `str.format()` 注入变量（非 Jinja2）。

## 模板分类

| 类别 | 文件 | 用途 |
|------|------|------|
| **Agent** | `react.txt` | ReAct 循环系统提示词 |
| **上下文检索** | `contextual.txt`, `contextual_generation.txt`, `contextual_retrieval.txt` | 上下文增强生成 |
| **记忆** | `memory_extract.txt`, `memory_summarize.txt` | 记忆提取和摘要 |
| **RAG 增强** | `rag_hyde.txt`, `rag_multi_query.txt`, `rag_query_rewrite.txt` | 检索前查询增强 |
| **评估** | `eval_answer_relevancy.txt`, `eval_correctness.txt`, `eval_faithfulness.txt`, `eval_generate.txt`, `eval_precision.txt`, `eval_recall.txt`, `eval_relevancy.txt` | RAG 评估指标 |
| **行为原则** | `fragments/stance.txt`, `fragments/autonomy.txt`, `fragments/accountability.txt` | 平台级行为准则，无条件加载 |
| **安全** | `fragments/security.txt` | 安全约束片段（被 Skill Profile 引用） |

## 行为原则 Fragment

三个平台级行为 fragment 在每次 Agent 运行时**无条件加载**，不受 Skill Profile 影响：

| Fragment | 作用 | 核心规则 |
| --- | --- | --- |
| `stance.txt` | 立场总纲 | 不无脑同意，反对必须带证据 |
| `autonomy.txt` | 自主权边界 | 低/中/高风险三级分类，高风险操作需确认 |
| `accountability.txt` | 问责闭环 | 用户跳过建议时主动提醒 |

注入顺序：`stance` → `autonomy` → `accountability`，位于 context 末尾，权重最高。

详见 [Persona 系统](Persona.md)。

## Persona Fragment

用户可在 `config.yaml` 的 `persona` 区块定义 Agent 的身份、语气和任务地图。运行时编译为 prompt fragment 注入。

```yaml
persona:
  agent_name: "Nexus"
  identity: "开发搭档"
  tone: "直接、简洁"
  projects:
    - name: "AgentNexus"
      focus: "v0.2.0 发布"
```

## 运行环境块

每次构建提示词时注入 `== 运行环境 ==`（`agents/runtime_context.py`）：操作系统、工作目录（会话级 workspace 生效）、终端（`TERM_PROGRAM`/`TERM`）、本地当前时间（精确到分钟，带时区偏移）。不读取 CLAUDE.md。

## 项目指令（AGENTS.md 层级发现）

Codex 语义的 AGENTS.md 发现，按优先级从低到高：

1. `~/.agentnexus/AGENTS.md`（用户全局，可用 `AGENTNEXUS_HOME` 覆盖目录）
2. 从文件系统根到当前工作目录，每层目录的 `AGENTS.md`

更深的文件在冲突时覆盖更浅的文件；块内显式声明该优先级，且安全约束永远优先。单文件上限 8000 字符、总量上限 20000 字符，超预算时保留高优先级文件。

## 用户追加指令

`config.yaml` 的 `append_system_prompt`（多行文本）原样注入系统上下文**末尾**——用户个人指令的最高优先级位置，高于平台默认行为准则，但不覆盖安全约束。

## 工具描述策略

- **NATIVE_TOOLS 策略**：只下发 native function-calling schema，不再重复发送文本工具清单（消除双轨冗余）
- **JSON_MODE / PROMPT_JSON 策略**：无 native schema，文本清单 `== 可用工具 ==` 作为唯一工具面保留
- 策略中途降级（degrade）到 JSON 类策略时自动重建初始消息块，补回文本清单

## Section 组装与增量维护

上下文块是**命名 section 字典**（`build_react_sections`），不是匿名文本拼接。重建时 `diff_sections` 对比上一次渲染，只重渲染含变更 section 的消息组；未变更组字节级一致，provider 前缀缓存命中到第一个变更点。

消息按**稳定→易变**分组排列（变更只失效自己及之后的消息）：

| 顺序 | 组 | 内容 | 易变性 |
| --- | --- | --- | --- |
| 0 | rules | react.txt 规则前缀 | 几乎不变 |
| 1 | memory | 记忆上下文 | 压缩时变 |
| 2 | conversation | 对话 + persona + 行为 fragments | 压缩时变 |
| 3 | static | skills / mcp / profile / 项目指令 / 用户 appendix | 运行内不变 |
| 4 | tools | 文本工具清单（仅 JSON 策略） | 降级时插入 |
| 5 | volatile | 运行环境（分钟级时间戳）/ todo | 每次重建都可能变 |

Agent 每次重建记录 `prompt sections rebuilt: <变更 section 名>` 调试日志，可精确审计哪块上下文变了。

## 主提示词

`react.txt` / `react_think.txt` 均包含四段：身份（Persona 未配置时的默认身份）、输出契约（语言跟随用户、简洁优先、最终答案自包含）、工作流程、关键规则。

## API

```python
load_prompt(name: str) -> str
# 读取 {name}.txt 原始文本

format_prompt(name: str, **kwargs) -> str
# 读取 + 自动注入 {date} (UTC 当前日期)

load_core_fragments() -> str
# 加载平台级行为原则 fragment（stance + autonomy + accountability）

compile_persona_fragment(persona_config: PersonaConfig) -> str
# 将 PersonaConfig 编译为 prompt fragment 文本

build_environment_block(cwd=None, now=None) -> str
# 渲染 == 运行环境 == 块（OS/工作目录/终端/本地时间到分钟）

load_project_instructions(cwd=None, global_path=None) -> str
# AGENTS.md 层级发现，渲染 == 项目指令 == 块
```
