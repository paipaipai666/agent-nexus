# Candidate B: six-slot module registry

## Problem

The current runtime constructs concrete agents and memories, while tools, API dependencies, KB ingestion, and subagents construct their own instances. A local disable flag cannot govern those independent creation points. Some observability storage also owns durable submissions and recovery. The replacement must centralize selection and lifetime without turning the registry into a business-logic service locator or making persistence optional. Existing schemas stay unchanged.

## Usage

Choose providers once at the composition boundary. Six fixed slots exist; the registry does not discover arbitrary plugins or install packages. Built-in providers use deferred imports. Supplying a replacement factory prevents the built-in implementation from being imported.

```python
from agentnexus.app.modules import ENGINE, LTM, WIKI, ModuleRegistry
from agentnexus.app.runtime import AppRuntime

providers = ModuleRegistry.defaults(settings)
providers.disable(WIKI)
providers.replace(LTM, lambda scope: MyLongTermStore(scope.settings))
providers.replace(ENGINE, lambda scope: MyEngine(scope.llm, scope.tools))

with AppRuntime.build(providers=providers) as app:
    session = app.chat.create_session(workspace=workspace)
    app.chat.send_message(session.id, "Explain the project")
```

The composition layer resolves the same selected engine for every actor. Sessions and subagents receive separate engine instances, selected from the same provider definition. A registry is sealed when attached to a runtime; replacement during an active run is unsupported.

```python
def create_actor(host: ModuleHost, actor: ActorScope) -> AgentEngine:
    return host.require(ENGINE, actor)

def save_user_memory(host: ModuleHost, memory: MemoryWrite) -> MemoryId:
    store = host.require(LTM, ApplicationScope())
    return store.save(memory)

def after_kb_ingest(host: ModuleHost, source: KnowledgeSource) -> None:
    wiki = host.optional(WIKI, ApplicationScope())
    if wiki is not None:
        wiki.ingest(source)
```

These are boundary functions owned by application composition, tool binding, and KB integration. The engine and storage algorithms do not receive the host. API and CLI handlers use the host carried by their application context; they never construct a default implementation. A disabled memory tool is omitted from advertised tools and returns an explicit unavailable result if invoked directly. A disabled HTTP feature returns the project's structured unavailable response. Engine disablement leaves KB, history, settings, and other non-chat services available.

## Shape

### Selection and lifetime

The host hides deferred loading, typed resolution, instance ownership, startup failure, and shutdown behind `optional` and `require`. Resolution records one explicit status: disabled, uninitialized, ready, unavailable, failed, or closed. `optional` returns `None` for disabled or physically absent implementations, recording the distinction in `status`. It never treats a broken enabled provider as disabled. `require` raises `ModuleDisabled` or `ModuleUnavailable` for those states. An initialization defect raises `ModuleLoadError` from either method. Errors carry the slot name and original cause. A built-in provider declares its import entry; only a missing entry or ancestor package counts as physically absent. Missing dependencies inside an existing provider are initialization defects. Custom factory failures always propagate as initialization defects.

Optional integrations explicitly tolerate absence: no Wiki generation during KB ingestion, no LTM recall, current-turn-only working memory, and no telemetry. Direct operations such as a Wiki query, memory save, or chat execution require their selected feature and return its explicit unavailable state. This keeps unrelated services usable when an implementation is removed, while retaining visible diagnostics and never substituting a second implementation for a failed provider.

```python
T = TypeVar("T")

@dataclass(frozen=True)
class ModuleKey(Generic[T]):
    name: Literal["engine", "short_term", "long_term", "wiki", "evaluation", "observability"]
    lifetime: Literal["application", "session", "actor"]

@dataclass(frozen=True)
class Disabled:
    pass

@dataclass(frozen=True)
class Selected(Generic[T]):
    open: Callable[[ProviderScope], T]
    import_entry: str | None = None

class ModuleRegistry:
    @classmethod
    def defaults(cls, settings: Settings) -> ModuleRegistry: ...
    def disable(self, key: ModuleKey[T]) -> None: ...
    def replace(self, key: ModuleKey[T], factory: Callable[[ProviderScope], T]) -> None: ...
    def seal(self, resources: ApplicationResources) -> ModuleHost: ...

class ModuleHost:
    def optional(self, key: ModuleKey[T], scope: Scope) -> T | None: ...
    def require(self, key: ModuleKey[T], scope: Scope) -> T: ...
    def status(self, key: ModuleKey[T], scope: Scope) -> ModuleStatus: ...
    def close_scope(self, scope: Scope) -> None: ...
    def close(self) -> None: ...
```

The six keys are created once with their contract types; external callers cannot add keys. Validate configuration, duplicate replacements, and scope compatibility at `seal`, per boundary-discipline. Registry internals have one heterogeneous slot map, with its single type assertion encapsulated in typed key resolution. No `Any`, casts, provider names, or raw dictionaries escape into service consumers.

Each application host owns its own caches. LTM, Wiki, evaluation, and observation providers live for the application. STM lives for a session. Engines live for a main-session actor or a subagent actor. The cache is indexed by key and immutable scope identity; a per-scope acquisition lock prevents concurrent first access from opening duplicate resources. Each session and actor owns separate mutable state, per separate-before-serializing-shared-state. LTM intentionally shares one application store, whose existing synchronization remains the writer invariant.

Enabled provider factories import only their implementation and its necessary dependencies. Factories receive settings, model/tool handles, persistence, and actor/session identity through `ProviderScope`; they cannot ask the registry to resolve arbitrary other modules. This prevents dependency cycles and hidden service lookup. Runtime composition supplies cross-module ports explicitly when constructing a selected provider.

Scope closure is idempotent, rejects future acquisitions, and closes successful acquisitions once in reverse acquisition order. A provider factory owns cleanup of resources it opens before returning. Failed acquisition is retained as an explicit failed state; another unrelated feature does not retry it silently. Retrying a failed provider requires rebuilding the runtime.

### Six module contracts

Keep contracts and their domain types in `agentnexus/contracts/`, which imports none of the six implementation packages. This table defines the minimum required behavior; exact existing route operations must be listed during implementation rather than exposed through a generic `invoke` method.

| Slot | Public operations | Boundary invariant |
| --- | --- | --- |
| Engine | `run(request, turn) -> AgentAnswer`, `close()` | Turn input carries event sink, cancellation, tool access and checkpoint callback; no ReAct setters or private fields |
| STM | `read()`, `append(message)`, `snapshot()`, `restore(snapshot)`, `close()` | Neutral snapshot describes messages and summary; implementation-specific counters remain private |
| LTM | `save(write)`, `search(query)`, `revision()`, existing management operations, `close()` | Store instance supplied to every memory/tool/API path; no module singleton getter |
| Wiki | `ingest(source)`, `query(query)`, explicit lint/review/calibration/management operations, `close()` | All entry points resolve the same provider; KB ingestion cannot construct Wiki |
| Evaluation | `run(request) -> EvaluationReport`, `close()` | Production execution imports evaluation contracts only; evaluation package loads on demand |
| Observability | `span(name, fields)`, `emit(event)`, inspection/export operations, `close()` | Diagnostic failures cannot erase or replace durable execution persistence |

Short-term compaction remains in the default memory implementation. Its public state changes use `read`, `snapshot`, and `restore`; the manager and restoration code stop accessing `_messages`, `_summary`, and `_token_count`. The default implementation retains its current algorithms. A replacement controls its own storage and compaction policy behind the same domain operations. Engine execution receives prepared working messages and a session memory port; it does not depend on a concrete memory manager.

When STM is disabled, required execution code still owns a fresh working-message list for the current turn. It does not restore prior conversation messages from durable history or create `ShortTermMemory`. LTM can remain enabled independently. Conversation snapshots use a neutral serializer in persistence and preserve the current serialized representation; old snapshots remain readable without importing the default STM implementation.

Observation callers use a small required execution-side facade containing a `ContextVar` of the currently selected sink. Binding occurs at actor/run entry and resets in `finally`. With no binding it yields a null context manager and emits nothing; it never loads or reconstructs an observer. Background work receives an explicit copied observation context. This preserves decorator ergonomics while isolating the concrete observation provider. The facade owns no module selection or lifecycle.

### Module map and required persistence

| Location | Responsibility |
| --- | --- |
| `agentnexus/contracts/modules.py` | Six protocols, typed domain records, errors, neutral snapshot types |
| `agentnexus/app/modules.py` | Fixed keys, selected/disabled definitions, lazy scoped host |
| `agentnexus/app/builtin_modules.py` | Six provider factory closures with imports inside factory bodies |
| `agentnexus/app/runtime.py` | Register selections, bind integration ports, create actors and sessions, own shutdown |
| `agentnexus/persistence/execution.py` | Durable submission deduplication, recovery, collaboration records, existing table access |
| `agentnexus/persistence/conversations.py` | Versioned conversation persistence and snapshot codec |
| `agentnexus/execution/observation.py` | Required null-safe observation facade, no concrete telemetry imports |
| Existing six implementation packages | Default algorithms and provider implementations |

Move durable methods and their SQL from the current timeline implementation into required application persistence without changing table names or schemas. Chat submission, recovery, and collaboration use this store directly; optional telemetry receives emitted records after persistence succeeds. Move the conversation version manager out of optional memory implementation ownership. Update all consumers and remove old internal construction/getter paths in the same implementation unit, per migrate-callers-then-delete-legacy-apis.

CLI registration contains only command metadata and lazy handlers, including evaluation commands. Importing the CLI, creating the server, and assembling a headless application must not load any selected implementation. Management handlers resolve only their own slot. Existing tool-provider discovery remains separate; this registry governs six runtime modules and does not expand capability kinds.

## Synthesis decision

Pending comparison with candidate A. This candidate's distinguishing choice is a sealed, scoped host that owns lazy construction for six fixed typed keys. Concrete algorithms receive narrow ports, while application adapters share one module-selection authority.

## Tradeoffs accepted

- We accept one internal heterogeneous map and a small lifetime state machine in exchange for one authoritative creation and cleanup path across API, CLI, tools and actors.
- We accept distinct disabled and unavailable states, with optional integrations skipping both, in exchange for keeping unrelated application services usable when a package is removed. Direct feature operations fail explicitly, initialization defects propagate, and no alternate provider is constructed.
- We accept a sink-only observation context variable in exchange for avoiding observer parameters on every existing decorated helper. The variable cannot create services, and runtime boundaries own its binding.
- We accept explicit Wiki and LTM management contracts larger than their agent-facing methods in exchange for preserving existing management behavior without leaking concrete stores into routes.

## Alternatives considered

A dataclass containing six ready-made dependencies is simpler to inspect but exposes all initialization and lifetime coordination to every composition entry point. It also needs another mechanism for deferred missing-package behavior and per-session instances. The registry hides those decisions behind typed resolution.

Import-based entry-point discovery would hide provider installation details but introduces package scanning, ordering, conflict policy and a public plugin API unrelated to these six modules. Fixed built-in factories and explicit replacements provide the required selection without those contracts.

## Open questions and risks

Can the existing Wiki management routes be represented by one service contract without returning the concrete store? Enumerate their actual operations before implementing the provider. Can every existing observation call be classified as diagnostics or required persistence before moving it? Submission recovery and collaboration tests must decide ambiguous cases. These are implementation evidence checks, not reasons to block the authorized work.

## Verification and next implementation step

First add failing public tests that disable a slot and block imports of its implementation while assembling the app and executing unrelated services. Add replacements for all six slots through the registry, completed main/subagent turns, KB ingestion with Wiki disabled, direct memory-tool invocation with LTM disabled, snapshot round trips, and durable submission deduplication/recovery with observation absent. Exercise concurrent first acquisition and repeated scope closure. A selected provider that raises an initialization error must surface that error and must never trigger a built-in fallback.
