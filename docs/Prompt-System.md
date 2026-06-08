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
```
