> **[中文](Persona.md) | [English](Persona.en.md)**

# 🎭 Persona System

The Persona system defines the agent's **identity, behavioral principles, and task context** — transforming it from a "helpful assistant" into an operator with stance, boundaries, and context.

## Design Philosophy

Most agent system prompts only define workflows (how to use tools, how to reason) but not **behavioral principles** (when to agree, what needs confirmation, what to do when output is ignored).

The Persona system fills this gap with two layers:

### Layer 1: Platform Behavioral Principles (always loaded)

Three fragment files are automatically injected on every agent run, regardless of Skill Profile:

| Fragment | File | Responsibility |
| --- | --- | --- |
| **Stance** | `fragments/stance.txt` | Core stance: no blind agreement; objections must have evidence |
| **Autonomy** | `fragments/autonomy.txt` | Autonomy boundary: confirm based on risk level |
| **Accountability** | `fragments/accountability.txt` | Accountability loop: remind when user skips suggestions |

These are the agent's core behavioral准则, taking priority over any user-defined tone preferences.

### Layer 2: User Personalization

Defined in the `persona` section of `config.yaml`, compiled into a prompt fragment at runtime:

```yaml
persona:
  agent_name: "Nexus"
  identity: "Dev partner"
  tone: "Direct, concise, no fluff"
  projects:
    - name: "AgentNexus"
      focus: "v0.2.0 release"
    - name: "SideProject"
      focus: "Prototype validation"
```

## Autonomy Boundary Details

`autonomy.txt` classifies operations into three risk levels:

### Low Risk (execute directly)

No confirmation needed, agent proceeds on its own:

- Read, query, search operations
- Generate content without writing to persistent storage
- Format conversion, text processing
- List, count, analyze existing data

### Medium Risk (notify after execution)

Agent executes and notifies in the **same reply**:

- Create new files (not overwriting existing)
- Run read-only commands (ls, cat, grep, git status)
- Modify in-memory state (todo, session variables)

### High Risk (confirm before execution)

Agent must explain intent, scope, and irreversibility, then wait for confirmation:

- Write, modify, delete files or database records
- Call external APIs with side effects
- Execute shell commands that change system state
- Single operation affects multiple systems or records
- Overwrite existing files

## Accountability Mechanism

`accountability.txt` implements a soft-trigger accountability loop:

**Trigger condition**: Agent gave a clear suggestion in the previous turn, and the user's next message completely ignores it.

**Silence conditions** (do not trigger):
- User just sent their first message
- The turn right after the output was given
- User explicitly said "skip" or "hold off"

**Trigger method**: One brief question, no lengthy explanation.

## Injection Order

Within the prompt context message, the injection order is:

```text
memory_context
conversation_context
available_skill_context
mcp_context
compiled_profile.fragments_text   ← Skill-specific fragments (if any)
persona_text                      ← User personalization (if any)
behavior_fragments_text           ← Behavioral principles (last = highest weight)
todo_context
```

Behavioral principles are placed last, leveraging the LLM's stronger attention on trailing content to ensure highest priority.

## Configuration

### config.yaml

```yaml
persona:
  agent_name: "Nexus"          # Agent name
  identity: "Dev partner"      # Role definition
  tone: "Direct, concise"      # Communication style
  projects:                     # Current projects
    - name: "ProjectName"
      focus: "Current focus"
```

### Desktop GUI

Edit directly in the Settings page **Persona** section:

- Agent Name / Identity / Tone text inputs
- Projects dynamic list (add/edit/remove)
- Saves immediately to config.yaml

### What if I don't configure it?

- The three behavioral fragments still load (unconditional)
- Persona fragment is skipped (no content = no injection)
- Agent behaves as a standard ReAct workflow without personalization
