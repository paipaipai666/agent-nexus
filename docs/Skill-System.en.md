> **[中文](Skill-System.md) | [English](Skill-System.en.md)**

# 🎯 Skill System

Skills are reusable workflow templates defined via `SKILL.md` or `workflow.yaml`.

## Lifecycle

```
Discovery → Indexing → Routing → Selection → Pre-execution Steps → Agent Execution
```

**Discovery**: `SkillRegistry.discover()` recursively scans these directories by default. Both `.agents/skills` scopes work without configuring `extensions_dirs`:

- `$AGENTNEXUS_HOME/skills/`, or `~/.agentnexus/skills/` when the environment variable is unset.
- User skills in `~/.agents/skills/`.
- Configured `extensions_dirs`.
- Built-in skill directories.
- Project skills in `<workspace>/.agents/skills/`.

The CLI uses the current working directory as its workspace. Application startup uses `workspace_path`, falling back to the current directory. A chat session with another workspace loads only that workspace's project skills, together with user and configured shared skills. Each session owns its skill selection and routing index, so project skills do not leak into other workspaces.

Missing directories are skipped without being created. Each physical skill file loads once. User and project definitions with the same `namespace/id` remain a conflict rather than overriding each other.

Discovery runs at startup or on the first access to a session's skills. File changes require an explicit refresh. There is no file watcher. Global refreshes and individual enable changes synchronize on the next access to a session's skills. Refresh updates the selected skill's instructions and clears the selection if its definition is removed or conflicts.

Skill API endpoints accept an optional `session_id` query parameter. For example, `GET /api/skills?session_id=<id>` and `POST /api/skills/refresh?session_id=<id>` list or refresh that session's workspace. Without the parameter, endpoints use the application startup workspace. Selection, reset, and enable changes with the parameter affect only that session. An unknown session returns 404.

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
