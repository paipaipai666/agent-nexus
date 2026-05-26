# 🎯 Skill System

Skills are reusable workflow templates defined via `SKILL.md` or `workflow.yaml`.

## Lifecycle

```
Discovery → Indexing → Routing → Selection → Pre-execution Steps → Agent Execution
```

**Discovery**: `SkillRegistry.discover()` scans `~/.agentnexus/skills/` + `extensions_dirs` + built-in directories.

**Routing**: `SkillRouter` uses TF-IDF model to match user input:
- Tokenizes each skill's id/name/description for IDF computation
- Score = sum of matched token IDFs × reward coefficient
- Deterministic matching + optional LLM fallback (when score difference < margin)

**Pre-execution Steps**: `WorkflowRuntime.prepare()` executes sequentially:
- `prompt` — format prompt text
- `tool_call` — invoke specified tool
- `retrieve` — retrieve from knowledge base
- `checkpoint` — record checkpoint
- `finalize` — verify success criteria

## SKILL.md Format

```markdown
---
id: my-skill
display_name: My Skill
description: Description text
max_risk: medium
allow_tools: [web_search, file_read]
fragments: [react, security]
system: react
---

This is the skill's guidance prompt (Markdown body).
```

## Routing Decision

1. Compute TF-IDF score for user input against each skill
2. Highest score < `min_score` → no routing
3. Highest - second highest < `margin` → LLM fallback
4. Deterministic conditions met → auto-select
