> **[中文](Prompt-System.md) | [English](Prompt-System.en.md)**

# 📝 Prompt System

All prompts reside in `agentnexus/prompts/*.txt` and use `str.format()` for variable injection (not Jinja2).

## Template Categories

| Category | Files | Purpose |
| --- | --- | --- |
| **Agent** | `react.txt` | ReAct loop system prompt |
| **Contextual Retrieval** | `contextual.txt`, `contextual_generation.txt`, `contextual_retrieval.txt` | Context-augmented generation |
| **Memory** | `memory_extract.txt`, `memory_summarize.txt` | Memory extraction and summarization |
| **RAG Enhancement** | `rag_hyde.txt`, `rag_multi_query.txt`, `rag_query_rewrite.txt` | Pre-retrieval query enhancement |
| **Evaluation** | `eval_answer_relevancy.txt`, `eval_correctness.txt`, `eval_faithfulness.txt`, `eval_generate.txt`, `eval_precision.txt`, `eval_recall.txt`, `eval_relevancy.txt` | RAG evaluation metrics |
| **Behavioral** | `fragments/stance.txt`, `fragments/autonomy.txt`, `fragments/accountability.txt` | Platform-level behavioral principles, always loaded |
| **Security** | `fragments/security.txt` | Security constraint fragment (referenced by Skill Profiles) |

## Behavioral Fragments

Three platform-level behavioral fragments are **unconditionally loaded** on every agent run, regardless of Skill Profile:

| Fragment | Purpose | Core Rule |
| --- | --- | --- |
| `stance.txt` | Stance | No blind agreement; objections must come with evidence |
| `autonomy.txt` | Autonomy boundary | Low/medium/high risk triage; high-risk ops need confirmation |
| `accountability.txt` | Accountability loop | Proactively remind when user skips suggestions |

Injection order: `stance` → `autonomy` → `accountability`, placed at the end of context for highest attention weight.

See [Persona System](Persona.en.md) for details.

## Persona Fragment

Users can define the agent's identity, tone, and mission map in the `persona` section of `config.yaml`. Compiled into a prompt fragment at runtime.

```yaml
persona:
  agent_name: "Nexus"
  identity: "Dev partner"
  tone: "Direct, concise"
  projects:
    - name: "AgentNexus"
      focus: "v0.2.0 release"
```

## API

```python
load_prompt(name: str) -> str
# Reads {name}.txt raw text

format_prompt(name: str, **kwargs) -> str
# Reads + auto-injects {date} (UTC current date)

load_core_fragments() -> str
# Loads platform-level behavioral fragments (stance + autonomy + accountability)

compile_persona_fragment(persona_config: PersonaConfig) -> str
# Compiles a PersonaConfig into a prompt fragment string
```
