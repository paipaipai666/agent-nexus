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

## Runtime Environment Block

Injected on every prompt build (`agents/runtime_context.py`): OS, working directory (session-level workspace aware), terminal (`TERM_PROGRAM`/`TERM`), and local current time (minute precision, with UTC offset). CLAUDE.md is intentionally not read.

## Project Instructions (AGENTS.md Hierarchy)

AGENTS.md discovery with Codex semantics, lowest → highest precedence:

1. `~/.agentnexus/AGENTS.md` (user-global; directory overridable via `AGENTNEXUS_HOME`)
2. `AGENTS.md` at each directory from filesystem root down to the working directory

Deeper files override shallower ones on conflict; the block states this precedence explicitly, and safety constraints always rank above project instructions. Per-file cap 8000 chars, total cap 20000 chars; on overflow the higher-precedence files are kept.

## User Appendix

`append_system_prompt` in `config.yaml` (multi-line text) is injected verbatim at the **very end** of the system context — the highest-priority slot for personal instructions: above platform defaults, never above safety constraints.

## Tool Description Policy

- **NATIVE_TOOLS strategy**: only native function-calling schemas are sent; the text tool list is no longer duplicated (dual-track redundancy removed)
- **JSON_MODE / PROMPT_JSON strategies**: no native schemas, so the `== 可用工具 ==` text list is kept as the sole tool surface
- Mid-run strategy degradation to a JSON strategy rebuilds the initial message block to restore the text list

## Section Assembly & Incremental Maintenance

Context blocks are a **named section dict** (`build_react_sections`), not anonymous text concatenation. On rebuild, `diff_sections` compares against the last render; only message groups containing a changed section are re-rendered — unchanged groups stay byte-identical, so the provider's prefix cache keeps hitting up to the first change.

Messages are grouped stable → volatile (a change invalidates only itself and what follows):

| Order | Group | Content | Volatility |
| --- | --- | --- | --- |
| 0 | rules | react.txt rules prefix | nearly static |
| 1 | memory | Memory context | changes on compaction |
| 2 | conversation | Conversation + persona + behavior fragments | changes on compaction |
| 3 | static | skills / mcp / profile / project instructions / user appendix | fixed within a run |
| 4 | tools | Text tool list (JSON strategies only) | inserted on degrade |
| 5 | volatile | Environment (minute timestamp) / todo | may change every rebuild |

Each rebuild logs `prompt sections rebuilt: <changed section names>` for precise audit of what moved in the context.

## Main Prompt

Both `react.txt` and `react_think.txt` contain four sections: identity (default when Persona is unset), output contract (follow the user's language, concise first, self-contained final answers), workflow, and key rules.

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

build_environment_block(cwd=None, now=None) -> str
# Renders the == 运行环境 == block (OS/cwd/terminal/local time to the minute)

load_project_instructions(cwd=None, global_path=None) -> str
# AGENTS.md hierarchical discovery, renders the == 项目指令 == block
```
