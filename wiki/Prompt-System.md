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
| **安全** | `fragments/security.txt` | 安全约束片段（被 react.txt 引用） |

## API

```python
load_prompt(name: str) -> str
# 读取 {name}.txt 原始文本

format_prompt(name: str, **kwargs) -> str
# 读取 + 自动注入 {date} (UTC 当前日期)
```
