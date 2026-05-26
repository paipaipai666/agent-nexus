# 📝 Prompt System

All prompts reside in `agentnexus/prompts/*.txt` and use `str.format()` for variable injection (not Jinja2).

## Template Categories

| Category | Files | Purpose |
|------|------|------|
| **Agent** | `react.txt` | ReAct loop system prompt |
| **Contextual Retrieval** | `contextual.txt`, `contextual_generation.txt`, `contextual_retrieval.txt` | Context-augmented generation |
| **Memory** | `memory_extract.txt`, `memory_summarize.txt` | Memory extraction and summarization |
| **RAG Enhancement** | `rag_hyde.txt`, `rag_multi_query.txt`, `rag_query_rewrite.txt` | Pre-retrieval query enhancement |
| **Evaluation** | `eval_answer_relevancy.txt`, `eval_correctness.txt`, `eval_faithfulness.txt`, `eval_generate.txt`, `eval_precision.txt`, `eval_recall.txt`, `eval_relevancy.txt` | RAG evaluation metrics |
| **Security** | `fragments/security.txt` | Security constraint fragment (referenced by react.txt) |

## API

```python
load_prompt(name: str) -> str
# Reads {name}.txt raw text

format_prompt(name: str, **kwargs) -> str
# Reads + auto-injects {date} (UTC current date)
```
