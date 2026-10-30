# Candidate A: explicit runtime module bindings

## Usage

The application has one composition owner. A binding selects a default implementation, a supplied factory, or an explicitly disabled module. Factories are resolved only when their capability is used. No implementation package is imported to read configuration, construct the server, or list unrelated CLI commands.

```python
with AppRuntime.build(modules=RuntimeModules.disabled()) as runtime:
    assert runtime.status.agent.enabled is False
    assert runtime.chat.available is False
    sessions = runtime.sessions.list()
    assert runtime.health.ready is True
```

```python
modules = RuntimeModules.disabled().with_bindings(
    agent=Provided(lambda context: ScriptedAgent()),
    short_term=Provided(lambda session: DictionaryConversation()),
    long_term=Provided(lambda resources: DictionaryMemory()),
)
with AppRuntime.build(modules=modules) as runtime:
    session = runtime.chat.start_session()
    result = runtime.chat.send_message(session.id, "Remember the blue door")
    restored = runtime.sessions.export_snapshot(session.id)
    runtime.sessions.restore_snapshot(session.id, restored)
```

The scripted engine and dictionary stores implement public contracts without subclassing, importing, or imitating default implementation fields. The same selected agent factory creates main and child agents. A child receives its own execution context and conversation buffer.

```python
modules = RuntimeModules.defaults().with_bindings(
    wiki=Disabled(), evaluation=Disabled(), observability=Disabled(),
)
with AppRuntime.build(modules=modules) as runtime:
    app = create_app(runtime=runtime)
    runtime.knowledge.ingest(source)
    outcome = runtime.memory.save(memory_record)
```

Knowledge ingestion does not reconstruct Wiki. API dependency functions and CLI command handlers use the runtime passed to them. A missing optional capability returns a typed unavailable result or the existing transport's corresponding explicit error; it never falls back to a global default implementation.

## Problem

Current constructors and singleton getters defeat independent replacement. `AppRuntime.build()` creates memory and ReAct directly; subagent tools create ReAct again; memory tools and HTTP dependencies fetch their own long-term-memory and Wiki instances. STM restoration depends on concrete private fields. Timeline storage mixes optional diagnostics with durable submission and collaboration records. The design must preserve existing SQLite tables and checkpoint JSON while eliminating those independent construction paths.

## Shape

### Selection and ownership

```python
T = TypeVar("T")

@dataclass(frozen=True)
class Disabled:
    pass

@dataclass(frozen=True)
class Default:
    pass

@dataclass(frozen=True)
class Provided(Generic[T]):
    factory: T

Binding = Union[Disabled, Default, Provided[T]]

@dataclass(frozen=True)
class RuntimeModules:
    agent: Binding[AgentFactory]
    short_term: Binding[ConversationFactory]
    long_term: Binding[MemoryFactory]
    wiki: Binding[WikiFactory]
    evaluation: Binding[EvaluationFactory]
    observability: Binding[TelemetryFactory]

    @classmethod
    def defaults(cls) -> RuntimeModules: ...
    @classmethod
    def disabled(cls) -> RuntimeModules: ...
    def with_bindings(self, **bindings: Binding) -> RuntimeModules: ...

class RuntimeComposer:
    def __init__(self, settings: Settings, bindings: RuntimeModules): ...
    def build(self) -> AppRuntime: ...
    def new_agent(self, actor: ActorIdentity) -> AgentLoop: ...
    def new_conversation(self, session: SessionIdentity) -> ConversationStore: ...
    def close(self) -> None: ...
```

The sketches omit implementation bodies. Production `with_bindings` uses six explicit keyword parameters rather than the illustrated catch-all, so typos and wrong factory types fail at the composition boundary. Configuration parses exactly once into these bindings. Existing `wiki_enabled` maps into the Wiki binding rather than remaining a second independent switch. Configuration and runtime status use stable names for all six modules.

`Default` is resolved from a static module-name and factory-name table in the composer. Imports occur inside the selected resolver. `Disabled` never invokes a resolver. `Provided` never imports its default. Import failure identifying the selected implementation or an explicitly declared optional dependency marks only that capability unavailable; unrelated import or initialization defects propagate rather than being misreported as disablement. Resolution failures are cached per runtime and exposed in status. No package scanning, decorator registration, or import-time singleton creation is allowed.

The composer owns lazily created runtime services and closes initialized resources once in reverse order. Conversation stores and agent instances are per session or per child actor. Long-term memory, Wiki, evaluation, and telemetry providers are runtime scoped. A model provider is a lazy callable owned by the composer; all-disabled assembly does not create `AgentLLM`, an embedding model, Chroma, or a worker thread.

### Public contracts

```python
class AgentLoop(Protocol):
    def run(self, request: AgentRequest, context: AgentContext) -> AgentOutcome: ...

@dataclass(frozen=True)
class AgentContext:
    actor: ActorIdentity
    memory: SessionMemory
    tools: ToolAccess
    events: Callable[[AgentEvent], None]
    cancelled: Callable[[], bool]
    commit_round: Callable[[], bool]
    collaboration: CollaborationAccess

class ConversationStore(Protocol):
    def read(self) -> ConversationSnapshot: ...
    def append(self, message: ConversationMessage) -> None: ...
    def replace(self, snapshot: ConversationSnapshot) -> None: ...
    def clear(self) -> None: ...

class LongTermMemory(Protocol):
    def search(self, query: MemoryQuery) -> tuple[MemoryRecord, ...]: ...
    def save(self, record: MemoryRecord) -> MemoryId: ...
    def remove(self, memory_id: MemoryId) -> bool: ...
    def list(self, query: MemoryListing) -> tuple[MemoryRecord, ...]: ...

class Wiki(Protocol):
    def ingest(self, source: KnowledgeSource) -> WikiIngestResult: ...
    def query(self, query: WikiQuery) -> WikiAnswer: ...
    def inspect(self, query: WikiListing) -> WikiInventory: ...
    def change(self, command: WikiChange) -> WikiChangeResult: ...

class Evaluation(Protocol):
    def run(self, request: EvaluationRequest) -> EvaluationReport: ...
    def inspect(self, query: EvaluationListing) -> EvaluationInventory: ...
    def change(self, command: EvaluationChange) -> EvaluationChangeResult: ...

class Telemetry(Protocol):
    def emit(self, event: DiagnosticEvent) -> None: ...
    def span(self, operation: Operation) -> ContextManager[DiagnosticSpan]: ...
    def snapshot(self, snapshot: ContextSnapshot) -> None: ...
    def inspect(self, query: DiagnosticQuery) -> DiagnosticResult: ...
```

Factory aliases are typed callables: actor context to `AgentLoop`, session identity to `ConversationStore`, and runtime resources to each of the other four ports. Domain commands and results are frozen application values, not FastAPI models, ReAct enums, SQLite rows, or Chroma records. `AgentRequest` carries question, attachment data, profile, and run options; `AgentOutcome` carries final answer and usage. Existing event payload compatibility belongs to transport serializers and the default ReAct adapter.

Conversation snapshots contain immutable message values and summary. Message values preserve role, content, timestamp, metadata, and unknown legacy fields. A single application snapshot codec reads and writes the existing checkpoint JSON. Both replacement stores and the default store receive the same decoded snapshot; callers never assign `_messages`, `_summary`, or `_token_count`. Compaction reads and replaces complete snapshots through `SessionMemory`; its policy remains in the memory coordinator rather than becoming a requirement for every storage implementation.

`SessionMemory` is an application coordinator. It owns the current run buffer, consults the selected conversation store for cross-turn context, and consults optional long-term memory for recall and extraction. Disabling STM skips cross-turn restore and save, but preserves the current turn buffer required by the engine and durable turn commits. Disabling LTM skips its retrieval, extraction, embedding initialization, and tools. STM and LTM selection are independent; an enabled LTM can recall facts with STM disabled.

Public Wiki and evaluation command unions must enumerate existing CLI/API operations before implementation. A replacement supplies one typed command dispatcher for mutation rather than matching default service internals. Inspection returns domain records so transport code retains pagination and existing response schemas. This keeps read and command paths behind the selected provider, without pretending only the currently audited ingest and evaluation-run methods exist.

The current Wiki API requires listing pages, retrieving a page, statistics, review items, and calibration through inspection; mutation covers page deletion, linting, review resolution, overdue review processing, and backfill. File and text ingestion share `ingest`. The existing evaluation service requires task and suite listing/detail, baseline listing/loading, statistics, and comparison through inspection; commands cover dataset validation and baseline saving; task and suite execution share `run`. CLI-only benchmark, transcript, and report operations must join these domain commands and inspections during the caller inventory, rather than retaining imports of the alternate evaluation package.

Telemetry is always callable through an application-owned disabled implementation when unselected. It may discard diagnostics but never owns authoritative state. Trace correlation uses explicit run identity or a context-local span, with context copied into existing worker pools. There is no global mutable provider shared by unrelated runtimes. Diagnostic export and querying are available only through the selected provider; default tracer, drift detector, audit exporter, and alerts are implementation details of the default telemetry package. A diagnostic sink failure is isolated from execution. Persistence failures remain explicit run failures.

### Durable state and call paths

`services/execution_store.py` takes ownership of the existing timeline database, submission claims, saved run records, collaboration messages, and the minimum lifecycle events needed for recovery. It retains existing tables, keys, transaction semantics, and SQLite filenames. Historical diagnostic rows remain readable by the default telemetry provider. Disabling observability suppresses optional events and snapshots, while deduplication, cancellation receipts, recovery, and collaboration remain functional.

Required execution history is persisted by `ChatService` and `TurnRuntime` before optional telemetry receives a copy. Child lifecycle summaries needed after restart are application state, even though the current implementation stores them in `session_events`. Their persistence cannot depend on telemetry being enabled. Ordinary optional diagnostic writes do not acquire authority over durable transitions.

Dominant paths remain short:

- Main or child execution: chat/subagent service calls the same composer factory; the resulting engine consumes `AgentContext`.
- Memory tools and routes: a runtime memory application service uses the same selected LTM instance; no global memory getter remains in these entry points.
- Knowledge ingestion: the knowledge service performs RAG ingestion, then invokes the selected Wiki port only when available. Wiki may still run independently of the agent engine.
- Evaluation CLI and API: the command/route retrieves the selected evaluation provider from the runtime, then invokes its domain operation. No root CLI or service package imports either default evaluation package.
- Engine disabled: chat and subagent operations return an explicit unavailable result before claiming a submission or loading models; health, stored sessions, memory, Wiki, and evaluation remain available according to their own bindings.

### Module map

| Location | Owns |
| --- | --- |
| `app/contracts.py` | Six ports, factory types, immutable domain values |
| `app/modules.py` | Bindings, lazy default resolution, runtime status, lifetime ownership |
| `app/runtime.py` | One composition entry shared by API, CLI, and TUI |
| `services/execution_store.py` | Mandatory durable execution and collaboration state |
| `services/session_memory.py` | Per-run working buffer, optional retained conversation, optional LTM coordination |
| Existing implementation packages | Default providers and their adapters; no registration side effects |
| Existing routes, commands, tools | Input/output conversion and calls to runtime-owned services |

Per foundational-thinking, ownership and lifetime are represented before execution details. Per boundary-discipline, configuration and transport parsing terminate at composition and domain commands. Per separate-before-serializing-shared-state, actor conversation buffers are independent; only the existing durable database has shared transactional writers. The ports hide backend encoding, private memory layout, and provider construction. They do not expose ReAct transition stages or make callers sequence initialization methods.

## Tradeoffs accepted

- Explicit constructor plumbing touches many existing callers in exchange for predictable runtime ownership and independent replacement tests.
- Runtime-scoped LTM and Wiki instances may duplicate lightweight wrappers when multiple runtimes share a process; this avoids a global selection that one runtime can overwrite.
- Application durability retains some existing lifecycle event storage when telemetry is off. Its retention is required for recovery, not optional instrumentation.
- Existing implementation algorithms remain behind adapters while public consumers migrate in the same implementation wave. Superseded public singleton entry points are removed after all production callers move.

## Alternatives considered

- A string-keyed service registry exposes lookup keys, lifetimes, dependency order, and missing-service handling throughout business code. It hides construction but gives callers more coordination work and less type checking than six explicit bindings.
- Replacing individual singleton getters with configurable globals keeps imports short, but allows one runtime or test to change another runtime's provider and leaves actor ownership implicit.
- A universal module lifecycle interface with `start`, `stop`, and `invoke` avoids six ports but exposes an untyped operation protocol to every consumer. The common lifecycle is useful inside the composer, not as the business contract.

## Synthesis decision

Candidate A proposes explicit typed bindings as the base. The parent comparison selects or rejects this candidate and records adaptations; this document does not preselect the other candidate's shape.

## Open questions and risks

The existing API and CLI operation inventories must be completed before freezing the Wiki and evaluation command unions. Default telemetry querying must preserve access to historical rows after the ownership split. These are implementation verification obligations within the authorized scope, not reasons to pause design approval. The largest risk is retaining a hidden default reconstruction path; subprocess blockers and independent fakes must exercise actual commands and completed turns to catch it.

## Next implementation step

Write failing public tests for all-disabled assembly with all six implementation namespaces blocked, then implement typed bindings and the mandatory execution-store boundary before migrating optional consumers.
