# Dynamic Multi-Agent Workflow System — Implementation Plan

> **Based on**: `dynamic-workflow-design.md` v3 (2026-06-09)
> **Date**: 2026-06-10
> **Status**: Draft v3 (incorporating architecture review — 5 blocking fixes)

---

## Executive Summary

Implement a dynamic multi-agent workflow system where a main agent decomposes tasks into phases, a workflow-scoped HR agent recruits and manages sub-agent teams across all phases, sub-agents execute tasks with dual-layer memory and Blackboard-based coordination, and a client agent validates final deliverables. The system is serial-phase, session-scoped, self-destruct-safe, and treats the workflow repository as the single source of truth.

**Key architectural decisions**:

1. **New module, not extension** — `agentnexus/workflow/` is separate from `agentnexus/skills/`. The existing `WorkflowRuntime` is a pre-step runner; this is a full multi-phase orchestration engine.

2. **Orchestrator is an async tool, not a replacement** — `WorkflowOrchestrator` is exposed as three tools: `workflow_start` (launches background execution, returns `workflow_id` immediately), `workflow_status` (polls progress by `workflow_id`), and `workflow_cancel` (signals cancellation). The main agent's ReAct loop is never blocked — it can continue handling user queries, check workflow progress between tasks, or respond to cancellation requests. This is necessary because a workflow may run for hours or days across multiple phases. The orchestrator executes in a background thread managed by a `WorkflowManager` singleton, with lifecycle decoupled from any single ReAct cycle.

3. **HR is workflow-scoped** — One HR agent per workflow, not per phase. This maximizes talent registry utilization across phases (the HR learns from earlier recruitment decisions). HR's sole responsibility is recruitment (JD→agent design + talent management). Process QA is handled by team-internal reviewers, not HR.

4. **Blackboard over /btw for v1** — Inter-agent coordination uses a shared Blackboard (structured data store) rather than async /btw messaging. The Blackboard is simpler, deterministic, and doesn't require modifying the ReAct FSM. /btw messaging is deferred to v2.

---

## Gap Analysis: What Exists vs What's Needed

| Design Requirement | Existing Code | Gap |
|---|---|---|
| FSM engine | `agents/fsm.py` — production-ready | **Reuse directly**. New workflow FSM extends the same `StateMachine` class |
| Sub-agent spawning | `tools/subagent.py` — synchronous, role-based | **Extend**: add lifecycle management (idle/wakeup), dual-layer memory, agent pool |
| Transfer table | `agents/react_transitions.py` — 16 states × 25 transitions | **Extend**: add workflow-phase states to existing ReActState enum or create parallel enum |
| Hook system | `core/hooks.py` — 35+ hook points | **Extend**: add workflow-specific hooks (PHASE_START, PHASE_END, INBOX_CHECK, etc.) |
| Tool registry | `tools/registry.py` — 7-gate governance | **Reuse**: sub-agents already use scoped ToolRegistry instances |
| Workflow models | `skills/workflow.py` — Pydantic models | **New module**: models are different (JD, PhasePlan, TaskCard vs WorkflowStep) |
| Workflow runtime | `skills/runtime.py` — linear pre-step runner | **New module**: runtime is fundamentally different (multi-phase orchestration) |
| Agent lifecycle | No idle/wakeup/destroy | **New**: agent pool with lifecycle state machine |
| Inter-agent coordination | None | **New**: Blackboard (structured shared store) for v1; /btw deferred to v2 |
| Workflow repository | None | **New**: Git-like commit-based store |
| HR / Client agents | None | **New**: specialized agent roles |
| Talent registry | None | **New**: session-scoped agent template registry |
| DAG engine | None | **New**: task dependency graph |
| Self-destruct / re-plan | None | **New**: circuit breaker with escalation |
| LLM factory for workflow agents | `agents/llm_strategy.py` — AgentLLM, `_clone_llm()` in subagent.py | **Extend**: `_clone_llm()` works for sub-agents; add factory for HR/Client agent LLM configs (different model, temperature=0 for deterministic translation) |
| Observability / tracing | `observability/tracer.py` — trace_manager with span context | **Extend**: add workflow/phase/task span types; integrate with existing trace_manager |
| Error classification | `agents/exceptions.py` — ToolExecutionError | **Extend**: add workflow-specific errors (JDTranslationError, InvalidTransitionError, SchemaValidationError, CircuitBreakerTrippedError) |
| Memory integration | `memory/` — STM/LTM with compaction pyramid | **Bridge**: sub-agent dual-layer memory (permanent/task) is independent from the framework's STM/LTM; permanent_memory maps to agent prompt injection, task_memory maps to per-run context. No direct integration needed for v1. |

---

## Module Structure

```
agentnexus/workflow/
├── __init__.py                 # Public API re-exports
├── models/
│   ├── __init__.py
│   ├── phase.py               # PhasePlan, PhaseStatus, PhaseResult
│   ├── job_description.py     # JobDescription (JD)
│   ├── task_card.py           # TaskCard, TaskStatus, TaskDAG
│   ├── agent_template.py      # AgentTemplate, AgentProfile
│   ├── commit.py              # WorkflowCommit, CommitSignature
│   ├── inbox.py               # InboxMessage, MessageClass (v2 — /btw messaging, not used in v1)
│   └── acceptance.py          # AcceptanceCriteria, AcceptanceResult
├── repository/
│   ├── __init__.py
│   ├── store.py               # WorkflowStore — in-memory Git-like store
│   ├── commit_log.py          # CommitLog — append-only audit trail
│   └── refs.py                # AgentRefs — active/idle agent references
├── engine/
│   ├── __init__.py
│   ├── orchestrator.py        # WorkflowOrchestrator — main loop (runs in background thread)
│   ├── phase_runner.py        # PhaseRunner — single phase execution
│   ├── dag.py                 # TaskDAG — dependency graph with cycle detection
│   ├── circuit_breaker.py     # CircuitBreaker — re-plan / self-destruct logic
│   └── manager.py             # WorkflowManager — singleton managing background workflow lifecycle
├── agents/
│   ├── __init__.py
│   ├── hr_agent.py            # HRAgent — JD translation, talent management
│   ├── client_agent.py        # ClientAgent — acceptance validation
│   ├── sub_agent_pool.py      # SubAgentPool — lifecycle management
│   └── talent_registry.py     # TalentRegistry — session-scoped agent registry
├── blackboard/
│   ├── __init__.py
│   ├── board.py               # Blackboard — structured shared state store
│   └── section.py             # BoardSection — typed sections (findings, decisions, artifacts)
└── cli/
    ├── __init__.py
    └── workflow_cmd.py         # CLI commands for workflow management
```

---

## Implementation Phases

### Phase 0: Data Models & Repository (Foundation)

**Goal**: Define all data structures and the workflow store. No agent logic yet — pure data.

**Estimated effort**: 3-4 days
**Dependencies**: None
**Risk**: Low — pure data, no behavioral changes to existing code

#### 0.1 Workflow Models (`workflow/models/`)

**`phase.py`** — Phase plan and status:
```python
class PhaseStatus(Enum):
    PENDING = auto()
    ACTIVE = auto()
    COMPLETED = auto()
    SKIPPED = auto()
    FAILED = auto()

# Explicit state transition matrix for phases.
PHASE_TRANSITIONS: dict[PhaseStatus, set[PhaseStatus]] = {
    PhaseStatus.PENDING:   {PhaseStatus.ACTIVE, PhaseStatus.SKIPPED},
    PhaseStatus.ACTIVE:    {PhaseStatus.COMPLETED, PhaseStatus.FAILED},
    PhaseStatus.COMPLETED: set(),  # terminal
    PhaseStatus.SKIPPED:   set(),  # terminal
    PhaseStatus.FAILED:    {PhaseStatus.ACTIVE},  # retry: re-activate
}

@dataclass
class PhasePlan:
    phase_id: str
    name: str
    jd: JobDescription          # What kind of team is needed
    dependencies: list[str]     # phase_ids this depends on
    status: PhaseStatus
    result: PhaseResult | None = None

    def transition(self, new_status: PhaseStatus) -> None:
        allowed = PHASE_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition phase {self.phase_id} from {self.status.name} to {new_status.name}"
            )
        self.status = new_status

    # NOTE: retry_count is NOT on PhasePlan. CircuitBreaker owns all retry counting.
```

**`job_description.py`** — JD (decoupled from prompt):
```python
@dataclass
class JobDescription:
    role_name: str              # e.g. "Python Security Reviewer"
    description: str            # What this role does
    required_capabilities: list[str]   # e.g. ["code_review", "security_analysis"]
    preferred_tools: list[str]         # Tool names the agent should have
    constraints: list[str]             # e.g. "Must not modify production code"
    success_criteria: list[str]        # How to judge this role did well
```

**`task_card.py`** — Task within a phase:
```python
class TaskStatus(Enum):
    PENDING = auto()
    ASSIGNED = auto()
    IN_PROGRESS = auto()
    REVIEW = auto()
    COMPLETED = auto()
    FAILED = auto()

# Explicit state transition matrix — only these transitions are legal.
TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING:     {TaskStatus.ASSIGNED},
    TaskStatus.ASSIGNED:    {TaskStatus.IN_PROGRESS, TaskStatus.PENDING},  # unassign
    TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.FAILED},
    TaskStatus.REVIEW:      {TaskStatus.COMPLETED, TaskStatus.IN_PROGRESS, TaskStatus.FAILED},
    TaskStatus.COMPLETED:   set(),  # terminal
    TaskStatus.FAILED:      {TaskStatus.ASSIGNED},  # retry: re-assign
}

@dataclass
class TaskCard:
    task_id: str
    phase_id: str
    title: str
    description: str
    assigned_to: str | None     # agent_id
    status: TaskStatus
    dependencies: list[str]     # task_ids
    artifacts: list[str]        # Paths to outputs
    created_by: str             # agent_id of creator
    created_at: float
    updated_at: float
    max_retries: int = 2        # Config hint — CircuitBreaker reads this but owns the counter

    def transition(self, new_status: TaskStatus) -> None:
        """
        Transition to new status. Raises InvalidTransitionError if not in TASK_TRANSITIONS.
        Updates updated_at timestamp.
        """
        allowed = TASK_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition {self.task_id} from {self.status.name} to {new_status.name}. "
                f"Allowed: {[s.name for s in allowed]}"
            )
        self.status = new_status
        self.updated_at = time.time()

    # NOTE: can_retry() is NOT on TaskCard. CircuitBreaker.can_retry_task() owns this.

class InvalidTransitionError(Exception):
    pass
```

**`commit.py`** — Git-like commit:
```python
@dataclass
class WorkflowCommit:
    commit_id: str
    parent_id: str | None
    author: CommitSignature     # Who executed
    approver: CommitSignature   # Who approved/triggered
    timestamp: float
    intent: str                 # Why this commit
    before: dict                # State before (diff)
    after: dict                 # State after (diff)
    result: CommitResult        # accepted | rejected | partial

@dataclass
class CommitSignature:
    agent_id: str
    agent_type: str             # main | hr | sub | client
    role: str | None            # For sub-agents

class CommitResult(Enum):
    ACCEPTED = auto()
    REJECTED = auto()
    PARTIAL = auto()
```

**`inbox.py`** — Communication message (v2, not used in v1):
```python
# v2: These models are defined now for forward compatibility but are NOT
# used in the v1 implementation. v1 uses Blackboard for inter-agent coordination.
# These will be activated when /btw async messaging is implemented.

@dataclass
class InboxMessage:
    message_id: str
    from_agent: str             # agent_id
    to_agent: str               # agent_id
    content: str
    commit_ref: str | None      # Optional reference to a commit
    timestamp: float
    classification: MessageClass | None  # Set by receiver
    reply: str | None
    replied_at: float | None

class MessageClass(Enum):
    IMMEDIATE = auto()          # Can answer without current work context
    DEFERRED = auto()           # Needs current work to answer
```

**`acceptance.py`** — Client agent acceptance:
```python
@dataclass
class AcceptanceCriteria:
    criteria_id: str
    description: str
    source_requirement: str     # Original user requirement text
    is_mandatory: bool = True

@dataclass
class AcceptanceResult:
    passed: bool
    gaps: list[AcceptanceGap]   # Empty if passed
    checked_at: float

class GapSeverity(Enum):
    BLOCKING = auto()   # Must fix before acceptance
    MINOR = auto()      # Acceptable imperfection

@dataclass
class AcceptanceGap:
    criteria_id: str
    description: str            # What's missing
    severity: GapSeverity       # BLOCKING | MINOR
    suggested_fix: str          # Actionable fix description
```

**`agent_template.py`** — Talent registry entry:
```python
@dataclass
class AgentTemplate:
    template_id: str
    role: str
    capabilities: list[str]
    prompt_fragments: list[str]  # Key prompt pieces (not full prompt)
    tool_set: list[str]
    usage_count: int = 0
    success_rate: float = 1.0
    last_used: float | None = None

@dataclass
class AgentProfile:
    agent_id: str
    template_id: str | None     # None if created from scratch
    role: str
    permanent_memory: list[str] # Survives rehire
    task_memory: list[str]      # Reset on rehire, kept on resume
    status: AgentLifecycleStatus

class AgentLifecycleStatus(Enum):
    CREATED = auto()
    ACTIVE = auto()
    IDLE = auto()
    DESTROYED = auto()
```

**Tests**: `tests/unit/workflow/test_models.py`
- Validate all dataclass construction, serialization (`to_dict()`)
- Validate enum values
- Validate Pydantic model constraints (if any fields use Pydantic)
- Test PhasePlan dependency validation
- Test TaskCard status transitions
- Test CommitSignature construction

#### 0.2 Workflow Store (`workflow/repository/`)

**`store.py`** — In-memory Git-like store:
```python
class WorkflowStore:
    """Single source of truth for workflow state. Session-scoped."""

    def __init__(self):
        self._commits: list[WorkflowCommit] = []
        self._tasks: dict[str, TaskCard] = {}
        self._phases: dict[str, PhasePlan] = {}
        self._artifacts: dict[str, Any] = {}
        self._refs: AgentRefs = AgentRefs()
        self._lock = threading.RLock()

    # --- Commit ---
    def commit(self, author: CommitSignature, approver: CommitSignature,
               intent: str, before: dict, after: dict, result: CommitResult) -> str:
        """Append a commit. Returns commit_id."""

    def commit_subagent_result(self, agent_id: str, task_id: str,
                               artifacts: list[str], output: Any) -> str:
        """
        Sub-agent scoped commit. Only allows writing artifacts and task output.
        Rejects if diff contains phase/task state mutations.
        This is the ONLY commit path for sub-agents.
        """

    # --- Phase queries ---
    def get_phase(self, phase_id: str) -> PhasePlan | None
    def update_phase(self, phase_id: str, **kwargs) -> None
    def get_phases_by_status(self, status: PhaseStatus) -> list[PhasePlan]

    # --- Task queries ---
    def add_task(self, task: TaskCard) -> None
    def update_task(self, task_id: str, **kwargs) -> None
    def get_task(self, task_id: str) -> TaskCard | None
    def get_tasks_for_phase(self, phase_id: str) -> list[TaskCard]
    def get_tasks_for_agent(self, agent_id: str) -> list[TaskCard]
    def get_tasks_by_status(self, status: TaskStatus) -> list[TaskCard]

    # --- Artifact queries ---
    def store_artifact(self, key: str, value: Any) -> None
    def get_artifact(self, key: str) -> Any | None
    def get_artifacts_for_phase(self, phase_id: str) -> dict[str, Any]

    # --- Commit queries ---
    def log_commit(self, commit: WorkflowCommit) -> None
    def get_commits(self, since: float | None = None) -> list[WorkflowCommit]
    def get_commits_for_agent(self, agent_id: str) -> list[WorkflowCommit]
    def get_commits_since(self, timestamp: float) -> list[WorkflowCommit]

    # --- Snapshot ---
    def get_current_state(self) -> dict:
        """Snapshot of current workflow state."""

    # --- Persistence (v1 stub, v2 full implementation) ---
    def save(self, path: str) -> None:
        """Serialize to JSON file. Stub for v1 — allows manual save/restore."""

    @classmethod
    def load(cls, path: str) -> "WorkflowStore":
        """Deserialize from JSON file. Stub for v1."""
```

**`commit_log.py`** — Append-only audit:
```python
class CommitLog:
    """Append-only commit log with JSONL persistence option."""

    def append(self, commit: WorkflowCommit) -> None
    def query(self, agent_id: str | None, since: float | None) -> list[WorkflowCommit]
    def to_jsonl(self) -> str
```

**`refs.py`** — Agent references:
```python
class AgentRefs:
    """Tracks active and idle agents in the workflow."""

    def register(self, agent_id: str, agent_type: str, profile: AgentProfile) -> None
    def get(self, agent_id: str) -> AgentProfile | None
    def set_idle(self, agent_id: str) -> None
    def set_active(self, agent_id: str) -> None
    def remove(self, agent_id: str) -> None
    def list_active(self) -> list[AgentProfile]
    def list_idle(self) -> list[AgentProfile]
```

**Tests**: `tests/unit/workflow/test_store.py`
- Test commit append and retrieval
- Test phase CRUD operations
- Test task CRUD and phase-scoped queries
- Test artifact storage
- Test agent refs lifecycle
- Test concurrent access (thread safety)
- Test commit log JSONL serialization

---

### Phase 1: Core Engine (Orchestration Spine)

**Goal**: Implement the main orchestration loop, phase runner, and circuit breaker. This is the "brain" of the system.

**Estimated effort**: 5-7 days
**Dependencies**: Phase 0
**Risk**: Medium — this is the most architecturally complex piece

#### 1.1 Circuit Breaker (`workflow/engine/circuit_breaker.py`)

```python
class CircuitBreaker:
    """
    Prevents infinite loops via re-plan counting and self-destruct.
    SOLE OWNER of all retry/replan counters — PhasePlan and TaskCard
    do not track retry counts. This avoids counter desync bugs.
    """

    def __init__(self, max_replans: int = 3, max_phase_retries: int = 2,
                 max_task_retries: int = 2):
        self._replan_count: int = 0
        self._phase_retry_counts: dict[str, int] = {}  # phase_id → retry count
        self._task_retry_counts: dict[str, int] = {}   # task_id → retry count
        self._max_replans = max_replans
        self._max_phase_retries = max_phase_retries
        self._max_task_retries = max_task_retries

    def record_replan(self) -> CircuitState:
        """Record a re-plan event. Returns state."""

    def record_phase_failure(self, phase_id: str) -> CircuitState:
        """Record a phase failure. Increments per-phase counter AND replan counter."""

    def record_task_failure(self, task_id: str) -> CircuitState:
        """Record a task failure. Returns state. PhaseRunner checks can_retry()."""

    def record_client_failure(self) -> CircuitState:
        """Client agent fail counts toward replan limit (design doc invariant #10)."""

    def can_retry_phase(self, phase_id: str) -> bool:
        return self._phase_retry_counts.get(phase_id, 0) < self._max_phase_retries

    def can_retry_task(self, task_id: str) -> bool:
        return self._task_retry_counts.get(task_id, 0) < self._max_task_retries

    def should_continue(self) -> bool
    def get_state(self) -> CircuitState
    def get_diagnostic(self) -> dict

class CircuitState(Enum):
    GREEN = auto()       # Normal operation
    YELLOW = auto()      # Approaching limits
    RED = auto()         # Self-destruct triggered
```

**Tests**: `tests/unit/workflow/test_circuit_breaker.py`
- Test replan counting and threshold
- Test per-phase retry counting
- Test client failure shares replan counter (invariant #10)
- Test self-destruct trigger
- Test diagnostic output

#### 1.2 Task DAG (`workflow/engine/dag.py`)

```python
class TaskDAG:
    """Directed acyclic graph for task dependencies within a phase."""

    def __init__(self):
        self._nodes: dict[str, TaskCard] = {}
        self._edges: dict[str, set[str]] = {}  # task_id -> set of dependency task_ids

    def add_task(self, task: TaskCard) -> None
    def add_dependency(self, task_id: str, depends_on: str) -> None
    def remove_task(self, task_id: str) -> None

    def would_create_cycle(self, task_id: str, depends_on: str) -> bool:
        """Check if adding this edge would create a cycle (topological check)."""

    def get_ready_tasks(self) -> list[TaskCard]:
        """Tasks with all dependencies satisfied."""

    def get_execution_order(self) -> list[str]:
        """Topological sort of all tasks."""

    def mark_complete(self, task_id: str) -> list[str]:
        """Mark task complete, return newly unblocked task_ids."""

    def apply_modification(self, requestor: str, action: str, **kwargs) -> DAGModificationResult:
        """Process a DAG modification request from a sub-agent."""

class DAGModificationResult:
    approved: bool
    reason: str
    requires_main_agent_approval: bool = False
```

**Tests**: `tests/unit/workflow/test_dag.py`
- Test acyclic invariant enforcement
- Test cycle detection
- Test topological sort
- Test ready-task identification
- Test mark-complete cascade
- Test modification request/approval flow

#### 1.3 Phase Runner (`workflow/engine/phase_runner.py`)

```python
class PhaseRunner:
    """
    Executes a single phase using a workflow-scoped HR agent.
    HR is passed in, not created per phase.
    """

    def __init__(self, store: WorkflowStore, blackboard: Blackboard, config: dict):
        self._store = store
        self._blackboard = blackboard
        self._config = config

    async def run_phase(self, phase: PhasePlan, hr: HRAgent) -> PhaseResult:
        """
        1. HR translates phase JD → agent design (if not already cached)
        2. HR recruits sub-agents (from registry or fresh)
        3. Sub-agents execute tasks (serial within team, DAG-ordered)
        4. Team-internal reviewer validates outputs (NOT HR)
        5. Commit results to store + blackboard
        6. Return PhaseResult
        """

    async def _execute_team_tasks(self, tasks: list[TaskCard], agents: dict,
                                   blackboard: Blackboard) -> dict:
        """
        Execute tasks respecting DAG order.
        Sub-agents read from and write to the blackboard for cross-task coordination.
        """

    async def _team_review(self, tasks: list[TaskCard], outputs: dict,
                           jd: JobDescription) -> dict:
        """
        Team-internal review of outputs against JD success criteria.
        Uses a dedicated reviewer sub-agent (recruited by HR for this phase).
        NOT the HR agent's responsibility.
        """
```

**Tests**: `tests/unit/workflow/test_phase_runner.py`
- Test phase lifecycle (start → execute → complete)
- Test HR recruitment flow (HR passed in, not created)
- Test task execution order respects DAG
- Test team review (separate from HR)
- Test failure handling and retry
- Test blackboard read/write during task execution

#### 1.4 Workflow Manager (`workflow/engine/manager.py`)

The `WorkflowManager` is a singleton that owns background workflow threads. It is the integration point between the main agent's ReAct loop (via tools) and the long-running orchestrator.

```python
class WorkflowManager:
    """
    Singleton managing background workflow execution.
    Bridges the async tool interface (workflow_start/status/cancel) with
    the WorkflowOrchestrator running in a background thread.

    Thread model:
    - Main agent's ReAct loop runs on the main thread
    - Each workflow runs in its own daemon thread via WorkflowOrchestrator
    - Communication via thread-safe WorkflowState snapshot
    - Cancellation via threading.Event
    """

    _instance: WorkflowManager | None = None

    def __init__(self):
        self._workflows: dict[str, ManagedWorkflow] = {}
        self._lock = threading.Lock()

    def start_workflow(self, user_input: str, llm_client, tool_executor,
                       config: dict | None = None) -> str:
        """
        Launch a workflow in a background thread. Returns workflow_id immediately.
        The main agent's ReAct loop continues without blocking.
        """

    def get_status(self, workflow_id: str) -> WorkflowStatus:
        """
        Return current workflow status (non-blocking snapshot).
        Includes: phase_plan, current_phase, phases_completed, circuit_state,
        last_event, answer (if completed).
        """

    def cancel(self, workflow_id: str) -> bool:
        """
        Signal cancellation. Sets threading.Event checked by orchestrator
        at each phase boundary. Returns True if signal was delivered.
        """

    def list_workflows(self) -> list[WorkflowStatus]
    def get_result(self, workflow_id: str) -> WorkflowResult | None

    @classmethod
    def reset(cls) -> None:
        """
        Reset singleton state. MUST be called in test teardown (or use the
        `workflow_manager_reset` fixture) to prevent state leakage between tests.
        Cancels all running workflows and clears the instance.
        """
        if cls._instance is not None:
            for wf in cls._instance._workflows.values():
                wf.cancel_event.set()
            cls._instance = None

@dataclass
class ManagedWorkflow:
    workflow_id: str
    orchestrator: WorkflowOrchestrator
    thread: threading.Thread
    cancel_event: threading.Event
    state: WorkflowState  # Thread-safe snapshot updated by orchestrator

@dataclass
class WorkflowStatus:
    workflow_id: str
    state: str              # "planning" | "running" | "completed" | "failed" | "cancelled"
    current_phase: str | None
    phases_completed: int
    phases_total: int
    circuit_state: str      # "green" | "yellow" | "red"
    last_event: str
    started_at: float
    updated_at: float
    answer: str | None      # Only set when completed

class WorkflowState:
    """
    Thread-safe mutable state shared between orchestrator thread and manager.
    All reads/writes go through a lock.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._phase_plan: list[PhasePlan] = []
        self._current_phase_idx: int = 0
        self._circuit_state: CircuitState = CircuitState.GREEN
        self._last_event: str = ""
        self._answer: str | None = None
        self._outcome: str | None = None

    def update(self, **kwargs) -> None: ...  # Lock-protected
    def snapshot(self) -> WorkflowStatus: ...  # Lock-protected
```

#### 1.5 Workflow Orchestrator (`workflow/engine/orchestrator.py`)

```python
class WorkflowOrchestrator:
    """
    Main loop. Runs in a background thread managed by WorkflowManager.
    NOT a tool itself — the tools are workflow_start/status/cancel on WorkflowManager.

    Lifecycle:
    1. Analyze user input → create phase plan with JDs
    2. Create client agent (locked to original requirements)
    3. Create workflow-scoped HR agent (persists across all phases)
    4. Main loop: run phases serially via HR + PhaseRunner
    5. Synthesis phase (mandatory second-to-last)
    6. Client acceptance (mandatory last)
    7. Exit: normal / replan / self-destruct

    Cancellation: checks cancel_event at each phase boundary (not mid-task).
    """

    def __init__(self, workflow_id: str, llm_client, tool_executor,
                 shared_state: WorkflowState, cancel_event: threading.Event,
                 config: dict | None = None):
        self._workflow_id = workflow_id
        self._store = WorkflowStore()
        self._blackboard = Blackboard()
        self._circuit = CircuitBreaker()
        self._phase_runner = PhaseRunner(self._store, self._blackboard, config or {})
        self._hr: HRAgent | None = None
        self._client_agent: ClientAgent | None = None
        self._phase_plan: list[PhasePlan] = []
        self._shared_state = shared_state
        self._cancel_event = cancel_event
        self._llm = llm_client
        self._tool_executor = tool_executor

    def run(self, user_input: str) -> WorkflowResult:
        """
        Main entry point (runs in background thread). Blocks until workflow completes.
        Updates shared_state at each phase boundary for status polling.
        Checks cancel_event at each phase boundary.
        """

    def _plan_phases(self, user_input: str) -> list[PhasePlan]:
        """Use LLM to decompose task into phases with JDs."""

    def _initialize_agents(self) -> None:
        """Create workflow-scoped HR and client agents."""

    def _run_main_loop(self) -> WorkflowExit:
        """
        The main loop from design doc section 6.2.
        At each iteration:
        1. Check cancel_event → return CANCELLED if set
        2. Read store, check progress
        3. Check exit conditions (circuit breaker)
        4. Decide next phase
        5. Run phase
        6. Update shared_state for status polling
        7. Loop
        """

    def _run_synthesis_phase(self) -> PhaseResult:
        """Mandatory second-to-last phase. Regular phase with JD."""

    def _run_acceptance(self) -> AcceptanceResult:
        """Mandatory last step."""

    def _decide_next_phase(self) -> PhasePlan | None
    def _handle_exit(self, exit_type: WorkflowExit) -> WorkflowResult
    def _check_cancelled(self) -> bool:  # Returns True if cancel requested

class WorkflowExit(Enum):
    CONTINUE = auto()
    COMPLETED = auto()
    REPLAN = auto()
    SELF_DESTRUCT = auto()
    CANCELLED = auto()  # NEW: user-initiated cancellation

class WorkflowResult:
    answer: str
    commits: list[WorkflowCommit]
    phases_run: int
    outcome: str       # "completed" | "replanned" | "self_destructed" | "cancelled"
    diagnostic: dict
```

#### 1.6 Workflow Tools (registered in `ToolRegistry`)

Three tools exposed to the main agent:

```python
def make_workflow_tools(manager: WorkflowManager) -> dict:
    """Create workflow_start, workflow_status, workflow_cancel tools."""

    def workflow_start(task: str) -> str:
        """
        Launch a dynamic workflow in the background. Returns immediately with workflow_id.
        Use workflow_status to check progress.
        """
        wf_id = manager.start_workflow(task, ...)
        return json.dumps({"workflow_id": wf_id, "status": "started"})

    def workflow_status(workflow_id: str) -> str:
        """
        Check workflow progress. Returns current phase, completion count, circuit state.
        When completed, includes the final answer.
        """
        status = manager.get_status(workflow_id)
        return json.dumps(status.to_dict())

    def workflow_cancel(workflow_id: str) -> str:
        """
        Cancel a running workflow. Cancellation takes effect at the next phase boundary.
        Returns confirmation.
        """
        success = manager.cancel(workflow_id)
        return json.dumps({"cancelled": success})

    return {
        "workflow_start": workflow_start,
        "workflow_status": workflow_status,
        "workflow_cancel": workflow_cancel,
    }
```

**Tests**: `tests/unit/workflow/test_orchestrator.py`
- Test normal completion flow (all phases pass → synthesis → acceptance → done)
- Test HR is created once and reused across phases
- Test phase skip logic
- Test backtrack logic (re-run previous phase, max 2 steps back)
- Test replan triggered by circuit breaker
- Test self-destruct when replan limit exceeded
- Test client agent fail → replan counting (invariant #10)
- Test synthesis phase is a regular phase with JD (not a special case)
- Test acceptance phase is mandatory and last

**Tests**: `tests/unit/workflow/test_manager.py`
- Test workflow_start returns immediately (non-blocking)
- Test workflow_status returns correct progress during execution
- Test workflow_cancel takes effect at next phase boundary
- Test multiple concurrent workflows (each with own thread)
- Test manager singleton lifecycle

**Test fixture** (add to `tests/unit/workflow/conftest.py`):
```python
@pytest.fixture(autouse=True)
def workflow_manager_reset():
    """Reset WorkflowManager singleton between tests to prevent state leakage."""
    yield
    WorkflowManager.reset()
```

---

### Phase 2: HR Agent & Talent Registry

**Goal**: Implement the HR agent that translates JDs into agent designs and manages a talent registry.

**Estimated effort**: 4-5 days
**Dependencies**: Phase 0, Phase 1 (models + engine)
**Risk**: Medium — JD→prompt translation is the hardest LLM integration point

#### 2.1 Talent Registry (`workflow/agents/talent_registry.py`)

```python
class TalentRegistry:
    """
    Session-scoped registry of agent templates.
    Design doc section 4.2: role + capabilities + prompt fragments + tool set.
    Maintained by the workflow-scoped HR agent across all phases.
    """

    def __init__(self):
        self._templates: dict[str, AgentTemplate] = {}

    def register(self, template: AgentTemplate) -> str:
        """Register a new template. Returns template_id."""

    def find_similar(self, jd: JobDescription, threshold: float = 0.5) -> AgentTemplate | None:
        """
        Find the most similar existing template for a JD.
        Similarity = capability overlap + tool overlap + semantic role similarity.
        Returns None if nothing above threshold.

        Known limitation: v1 uses capability overlap + tool overlap + token-based
        role name similarity (Jaccard on word tokens). This means "Python Security Reviewer"
        and "Code Security Analyst" will partially match (overlap on "security") but won't
        score as high as identical role names. Embedding-based similarity is deferred to v2.
        """

    def record_usage(self, template_id: str, success: bool) -> None:
        """Update usage stats after a task completes."""

    def get_template(self, template_id: str) -> AgentTemplate | None
    def list_templates(self) -> list[AgentTemplate]
```

**Similarity algorithm** (v1 — token-based, no embeddings):
```
capability_score = len(cap_overlap) / max(len(jd_caps), len(tmpl_caps))
tool_score = len(tool_overlap) / max(len(jd_tools), len(tmpl_tools))
role_score = len(word_overlap(jd.role_name, tmpl.role)) / max(len(jd_words), len(tmpl_words))

score = 0.4 * capability_score + 0.3 * tool_score + 0.3 * role_score
```

Where `word_overlap` tokenizes both role names by whitespace/punctuation and computes Jaccard similarity. This gives partial credit for "Python Security Reviewer" vs "Code Security Analyst" (overlap on "security") rather than the binary 0/1 of exact string match.

**Tests**: `tests/unit/workflow/test_talent_registry.py`
- Test register and retrieve
- Test find_similar with exact match
- Test find_similar with partial match
- Test find_similar returns None when nothing similar
- Test usage tracking and success rate calculation

#### 2.2 HR Agent (`workflow/agents/hr_agent.py`)

```python
class HRAgent:
    """
    Workflow-scoped HR agent. Design doc section 3.2.
    Created once per workflow, handles recruitment across ALL phases.

    Responsibilities:
    - Translate JDs → agent designs (prompt + tools)
    - Maintain talent registry (search, clone, create)
    - Push back on vague/unreasonable JDs

    NOT responsible for:
    - Process QA (that's team-internal reviewers)
    - Output validation (that's the team lead / reviewer role)
    - Task content (that's written by the main agent)
    """

    def __init__(self, llm_client, registry: TalentRegistry,
                 store: WorkflowStore, tool_executor):
        self._llm = llm_client
        self._registry = registry
        self._store = store
        self._tool_executor = tool_executor
        self._design_cache: dict[str, AgentDesign] = {}  # JD hash → cached design

    async def recruit_team(self, jd: JobDescription) -> list[RecruitmentResult]:
        """
        1. Search registry for similar templates
        2. If found → clone + tune
        3. If not → create from scratch (LLM generates prompt + tool config)
        4. Register new template in registry
        5. Return list of RecruitmentResult(profile, design, template)

        Note: HR recruits the team AND a dedicated reviewer sub-agent for this phase.
        The reviewer handles process QA; HR does not.

        Returns RecruitmentResult (not just AgentProfile) because PhaseRunner
        needs both profile AND design to call SubAgentPool.create_agent().
        """

class RecruitmentResult:
    profile: AgentProfile
    design: AgentDesign
    template: AgentTemplate | None  # None if created from scratch
```

    async def translate_jd(self, jd: JobDescription) -> AgentDesign:
        """
        Use LLM to translate a JD into:
        - System prompt for the sub-agent
        - Tool set configuration
        - Success criteria
        - Memory configuration

        Three-level fallback on failure:
        1. Retry once with more constrained prompt + temperature=0
        2. If still invalid, use FALLBACK_AGENT_TEMPLATE (predefined general-purpose agent)
        3. If fallback also fails, raise JDTranslationError (Orchestrator skips phase if deps allow)

        The fallback template is a safe, broad-capability agent that can handle most tasks
        suboptimally. It's better than failing the entire workflow.
        """

    async def _validate_design(self, design: AgentDesign) -> list[str]:
        """
        Schema validation for AgentDesign.
        Returns list of validation errors (empty = valid).
        Checks:
        - system_prompt is non-empty and > 50 chars
        - tool_set contains only valid tool names (cross-reference with ToolRegistry)
        - success_criteria is non-empty
        - memory_config has required keys
        """

    async def push_back(self, jd: JobDescription) -> PushBackResult:
        """
        HR can push back on vague or unreasonable JDs.
        Returns: accepted | needs_clarification | unreasonable
        With specific feedback for the main agent.
        """

    async def _clone_agent(self, template: AgentTemplate, jd: JobDescription) -> AgentProfile:
        """Clone an existing template and tune for this specific JD."""

    async def _create_agent(self, jd: JobDescription) -> AgentProfile:
        """Create a new agent from scratch based on JD."""

class AgentDesign:
    system_prompt: str
    tool_set: list[str]
    success_criteria: list[str]
    memory_config: dict

class PushBackResult:
    status: str  # "accepted" | "needs_clarification" | "unreasonable"
    feedback: str
    suggested_jd: JobDescription | None  # HR's alternative suggestion

class JDTranslationError(Exception):
    """Raised when LLM fails to produce a valid AgentDesign after retries."""

# Fallback template — defined after AgentDesign since it references the class.
FALLBACK_AGENT_TEMPLATE = AgentDesign(
    system_prompt="You are a general-purpose assistant. Complete the given task to the best of your ability. "
                  "If you cannot complete it, clearly state what is missing.",
    tool_set=["grep_search", "file_read", "file_list"],
    success_criteria=["Task output is provided", "Output addresses the task description"],
    memory_config={"inject_long_term": True, "allow_save": False},
)
```

**Tests**: `tests/unit/workflow/test_hr_agent.py`
- Test JD translation (mock LLM)
- Test JD translation schema validation catches incomplete output
- Test JD translation retry on invalid output
- Test recruitment with registry hit (clone path)
- Test recruitment with registry miss (create path)
- Test push-back on vague JD
- Test push-back on unreasonable JD
- Test HR is workflow-scoped (same instance across multiple recruit_team calls)

#### 2.3 Sub-Agent Pool (`workflow/agents/sub_agent_pool.py`)

```python
class SubAgentPool:
    """
    Manages sub-agent lifecycle: create, activate, idle, wakeup, destroy.
    Design doc section 4.4.

    CRITICAL INVARIANT: Sub-agents do NOT hold a reference to WorkflowStore.
    SubAgentPool mediates all store writes via commit_subagent_result(),
    which validates that the commit only contains artifacts and task output —
    not phase/task state mutations. This enforces invariant #11.
    """

    def __init__(self, llm_client, tool_executor, store: WorkflowStore,
                 blackboard: Blackboard):
        self._agents: dict[str, ManagedAgent] = {}
        self._llm = llm_client
        self._tool_executor = tool_executor
        self._store = store
        self._blackboard = blackboard

    async def create_agent(self, profile: AgentProfile, design: AgentDesign) -> str:
        """Create a new sub-agent. Returns agent_id."""

    async def activate(self, agent_id: str, task: TaskCard) -> None:
        """Wake an idle agent for a new task (rehire mode)."""

    async def resume(self, agent_id: str, task: TaskCard) -> None:
        """Wake an idle agent preserving task memory."""

    async def run_task(self, agent_id: str, task: TaskCard) -> TaskResult:
        """
        Run a task on a sub-agent. The sub-agent:
        1. Reads from Blackboard (via blackboard_read tool)
        2. Executes in ReAct loop (no direct store access)
        3. Writes findings to Blackboard (via blackboard_write tool)
        4. Produces artifacts (files, code, etc.)
        5. Returns raw output to SubAgentPool

        SubAgentPool then:
        6. Self-reviews deferred work (if any)
        7. Validates output against task requirements
        8. Calls commit_subagent_result() to write to store (engine-layer mediation)
        """

    def commit_subagent_result(self, agent_id: str, task: TaskCard,
                               result: TaskResult) -> WorkflowCommit:
        """
        Engine-layer mediated commit. Validates that:
        - result only contains artifacts and task output
        - no phase/task state mutations in the diff
        Then commits to store with proper signatures.

        This is the ONLY path for sub-agent output to reach the store.
        Sub-agents cannot call store.commit() directly.
        """

    def _validate_commit_diff(self, task: TaskCard, result: TaskResult) -> list[str]:
        """
        Validate that the commit diff only touches allowed fields:
        - task.artifacts (new artifact paths)
        - task.status (ASSIGNED → IN_PROGRESS → REVIEW)
        - Blackboard entries (indirect, via blackboard_write)
        Returns list of violations (empty = valid).
        """

    async def set_idle(self, agent_id: str) -> None:
        """Mark agent idle. Preserve context, release API resources."""

    async def destroy(self, agent_id: str) -> None:
        """Permanently remove an agent."""

    def get_agent(self, agent_id: str) -> ManagedAgent | None
    def list_active(self) -> list[ManagedAgent]
    def list_idle(self) -> list[ManagedAgent]

@dataclass
class ManagedAgent:
    profile: AgentProfile
    design: AgentDesign
    agent: ReActAgent           # The actual agent instance
    permanent_memory: list[str]
    task_memory: list[str]
    status: AgentLifecycleStatus
    created_at: float
    last_active: float

    # Growth limits for memory
    MAX_PERMANENT_MEMORY: int = 50
    MAX_TASK_MEMORY: int = 20

    def add_permanent_memory(self, entry: str) -> None:
        """Add with FIFO eviction if at capacity."""
        self.permanent_memory.append(entry)
        if len(self.permanent_memory) > self.MAX_PERMANENT_MEMORY:
            self.permanent_memory = self.permanent_memory[-self.MAX_PERMANENT_MEMORY:]

    def add_task_memory(self, entry: str) -> None:
        """Add with FIFO eviction if at capacity."""
        self.task_memory.append(entry)
        if len(self.task_memory) > self.MAX_TASK_MEMORY:
            self.task_memory = self.task_memory[-self.MAX_TASK_MEMORY:]

class TaskResult:
    task_id: str
    agent_id: str
    status: str  # "completed" | "failed" | "partial"
    output: Any
    artifacts: list[str]
    error: str | None
```

**Tests**: `tests/unit/workflow/test_sub_agent_pool.py`
- Test agent creation and lifecycle transitions
- Test rehire mode (task memory reset)
- Test resume mode (task memory preserved)
- Test idle state preserves context
- Test destroy removes agent completely
- Test commit_subagent_result validates diff
- Test commit_subagent_result rejects phase/task state mutations
- Test run_task reads from and writes to Blackboard
- Test memory growth limits (FIFO eviction)

---

### Phase 3: Blackboard (Inter-Agent Coordination)

**Goal**: Implement the Blackboard — a structured shared store that sub-agents read from and write to for cross-task coordination within and across phases.

**Estimated effort**: 2-3 days
**Dependencies**: Phase 0 (models)
**Risk**: Low — simple structured store, no FSM modification needed

**Why Blackboard instead of /btw**: /btw async messaging requires modifying the ReAct FSM (inbox check at observe→think boundary), introduces message classification complexity (IMMEDIATE vs DEFERRED with no operational definition), and adds non-deterministic behavior. The Blackboard is simpler: sub-agents write findings to structured sections, other sub-agents read from those sections as part of their normal tool-calling pattern. No FSM changes, no message classification, no timeout/retry logic. /btw is deferred to v2 when there's a concrete use case that Blackboard can't handle.

#### 3.1 Blackboard (`workflow/blackboard/board.py`)

```python
class Blackboard:
    """
    Structured shared store for inter-agent coordination.
    Replaces /btw messaging for v1.

    Sub-agents write to named sections (e.g., "findings", "decisions", "code_reviews").
    Other sub-agents read from sections as part of their task execution.
    All writes are append-only with author attribution.

    Key properties:
    - Thread-safe (RLock)
    - Section-scoped (no free-form messaging)
    - All writes are attributed (agent_id + timestamp)
    - Readable by all agents in the workflow
    - Writable by active agents only
    """

    def __init__(self):
        self._sections: dict[str, BoardSection] = {}
        self._lock = threading.RLock()

    def create_section(self, name: str, schema: dict | None = None) -> None:
        """
        Create a named section. Schema is a JSON Schema dict (validates with jsonschema lib).
        Reuses the same validation approach as ToolRegistry's parameter schema validation.
        If schema is None, any dict is accepted.
        """

    def write(self, section: str, agent_id: str, content: dict) -> str:
        """
        Append an entry to a section. Returns entry_id.
        Raises SectionNotFoundError if section doesn't exist.
        Raises SchemaValidationError if content doesn't match section's JSON Schema.
        """

    def read(self, section: str, since_entry: str | None = None) -> list[BoardEntry]:
        """Read all entries from a section (optionally since a given entry_id)."""

    def read_latest(self, section: str, n: int = 10) -> list[BoardEntry]:
        """Read the N most recent entries from a section."""

    def list_sections(self) -> list[str]
    def get_section_info(self, section: str) -> dict

@dataclass
class BoardEntry:
    entry_id: str
    section: str
    agent_id: str
    content: dict
    timestamp: float

class SectionNotFoundError(Exception):
    pass

class SchemaValidationError(Exception):
    pass
```

#### 3.2 Board Sections (`workflow/blackboard/section.py`)

```python
class BoardSection:
    """A named section of the blackboard with optional schema enforcement."""

    def __init__(self, name: str, schema: dict | None = None):
        self._name = name
        self._schema = schema
        self._entries: list[BoardEntry] = []
        self._lock = threading.Lock()

    def append(self, agent_id: str, content: dict) -> str:
        """Add an entry. Validates against schema if present."""

    def read(self, since_entry: str | None = None) -> list[BoardEntry]
    def read_latest(self, n: int = 10) -> list[BoardEntry]
    def count(self) -> int
```

**Built-in section schemas** (created at workflow start):
- `findings` — structured findings from sub-agents (type, description, evidence, confidence)
- `decisions` — key decisions made during execution (decision, rationale, alternatives)
- `artifacts` — references to produced artifacts (path, type, producer, description)
- `issues` — problems found during execution (severity, description, suggested_fix)

#### 3.3 Integration

The Blackboard is passed to sub-agents as part of their tool environment. Sub-agents access it via two tools:
- `blackboard_write(section, content)` — write to a section
- `blackboard_read(section, since_entry)` — read from a section

These are registered as safe tools in the sub-agent's `ToolRegistry`, alongside the existing `grep_search`, `file_read`, etc. No FSM modification needed — the agent uses these tools in its normal ReAct loop.

**Tests**: `tests/unit/workflow/test_blackboard.py`
- Test section creation and schema enforcement
- Test write and read round-trip
- Test read_latest with limit
- Test concurrent writes from multiple agents (thread safety)
- Test schema validation rejects invalid content
- Test append-only invariant (entries never modified)
- Test section listing and info

**Deferred to v2**: /btw async messaging (design doc section 4.7) with inbox checking at ReAct boundaries. Will be added when there's a concrete use case where synchronous Blackboard reads are insufficient.

---

### Phase 4: Client Agent & Acceptance

**Goal**: Implement the client agent that validates final deliverables against original requirements.

**Estimated effort**: 2-3 days
**Dependencies**: Phase 0 (models)
**Risk**: Low — straightforward LLM-based validation

#### 4.1 Client Agent (`workflow/agents/client_agent.py`)

```python
class ClientAgent:
    """
    Acceptance agent. Design doc section 3.4.
    Created at workflow start with locked requirements.
    Only sees final deliverables, not process.
    """

    def __init__(self, original_requirements: str, llm_client):
        self._requirements = original_requirements
        self._criteria: list[AcceptanceCriteria] = []
        self._llm = llm_client
        self._created_at = time.time()

    async def initialize(self) -> None:
        """
        Translate original requirements into structured acceptance criteria.
        Run once at workflow start.
        """

    async def validate(self, deliverables: dict[str, Any]) -> AcceptanceResult:
        """
        Validate deliverables against criteria.
        Returns pass/fail + gap list.
        Only sees artifacts, not commit history or /btw communication.
        """

    def get_criteria(self) -> list[AcceptanceCriteria]
    def get_requirements(self) -> str
```

**Tests**: `tests/unit/workflow/test_client_agent.py`
- Test criteria generation from requirements
- Test validation with all criteria met (pass)
- Test validation with gaps (fail + actionable gap list)
- Test that client agent has no access to process data

---

### Phase 5: CLI Integration & Wiring

**Goal**: Wire everything together, add CLI commands, and integrate with the existing agent system.

**Estimated effort**: 3-4 days
**Dependencies**: All previous phases
**Risk**: Medium — integration complexity

#### 5.1 Public API (`workflow/__init__.py`)

```python
class DynamicWorkflow:
    """
    Top-level API for running dynamic workflows.
    Wraps WorkflowManager — this is the canonical path for programmatic usage.
    The three workflow_* tools in ToolRegistry also delegate to WorkflowManager.

    Two usage modes:
    1. Async (recommended): start() returns workflow_id, poll with status()
    2. Sync convenience: run() blocks until workflow completes (wraps start+poll internally)
    """

    def __init__(self, llm_client, tool_executor, config: dict | None = None):
        self._manager = WorkflowManager()
        self._llm = llm_client
        self._tool_executor = tool_executor
        self._config = config or {}

    def start(self, user_input: str) -> str:
        """Launch workflow in background. Returns workflow_id immediately."""

    def status(self, workflow_id: str) -> WorkflowStatus:
        """Poll workflow progress."""

    def cancel(self, workflow_id: str) -> bool:
        """Signal cancellation at next phase boundary."""

    async def run(self, user_input: str) -> WorkflowResult:
        """Sync convenience: start + poll until completion. Blocks until done."""

    def list_workflows(self) -> list[WorkflowStatus]
```

#### 5.2 CLI Commands (`workflow/cli/workflow_cmd.py`)

```python
# nexus workflow run "Build a REST API for user management"
# nexus workflow status
# nexus workflow commits [--since TIMESTAMP]
# nexus workflow inspect <commit_id>
```

#### 5.3 Hook Integration

Add new hook types to `core/hooks.py`:

```python
# Workflow-specific hooks
BEFORE_PHASE_START = "before_phase_start"
AFTER_PHASE_END = "after_phase_end"
BEFORE_HR_RECRUIT = "before_hr_recruit"
AFTER_HR_RECRUIT = "after_hr_recruit"
BEFORE_CLIENT_ACCEPTANCE = "before_client_acceptance"
AFTER_CLIENT_ACCEPTANCE = "after_client_acceptance"
WORKFLOW_REPLAN = "workflow_replan"
WORKFLOW_SELF_DESTRUCT = "workflow_self_destruct"
```

#### 5.4 Config Extension

Add workflow config to `core/config.py`:

```python
class WorkflowSettings(BaseModel):
    max_replans: int = 3
    max_phase_retries: int = 2
    max_phases: int = 10
    talent_registry_similarity_threshold: float = 0.5
    client_agent_model: str | None = None  # Use smaller model for client agent
    synthesis_phase_required: bool = True
    blackboard_max_entries_per_section: int = 1000
```

**Tests**: `tests/unit/workflow/test_integration.py`
- Test end-to-end workflow with mocked LLM
- Test CLI command parsing
- Test config loading
- Test hook firing at correct lifecycle points

---

### Phase 6: Testing, Documentation & Polish

**Goal**: Comprehensive testing, documentation, and edge case handling.

**Estimated effort**: 3-4 days
**Dependencies**: All previous phases
**Risk**: Low

#### 6.1 Integration Tests (`tests/integration/test_dynamic_workflow.py`)

- Test full workflow lifecycle with real (mocked) LLM calls
- Test multi-phase execution with inter-phase data passing via Blackboard
- Test HR agent recruitment with talent registry (workflow-scoped)
- Test Blackboard coordination between sub-agents
- Test client agent acceptance flow
- Test circuit breaker (replan → self-destruct)
- Test workflow repository commit trail
- Test single-direction control flow (sub-agent proposals flow up through HR → Engine → Orchestrator)

#### 6.2 Fault Injection Tests (`tests/unit/workflow/test_fault_injection.py`)

- Test LLM timeout in HR translate_jd → falls back to FALLBACK_AGENT_TEMPLATE
- Test LLM returns garbage JSON in HR translate_jd → retry → fallback
- Test sub-agent crash mid-task → SubAgentPool handles exception → task marked FAILED
- Test Blackboard concurrent write conflict → thread safety verified
- Test WorkflowStore commit during cancellation → clean state
- Test CircuitBreaker threshold edge (exactly at limit, one over)

#### 6.3 Performance Tests (`tests/perf/test_workflow.py`)

- Test Blackboard read/write latency at 1000 entries/section (SLA: < 1ms)
- Test WorkflowStore commit log at 10000 commits (SLA: < 10ms query)
- Test TalentRegistry find_similar at 100 templates (SLA: < 50ms)

#### 6.4 Concurrent Safety Tests (`tests/unit/workflow/test_concurrent.py`)

- Test multiple sub-agents writing Blackboard simultaneously
- Test WorkflowManager handling concurrent workflow_start calls
- Test WorkflowState snapshot consistency during rapid updates

#### 6.5 Edge Cases

- Empty phase plan (task is trivial, no phases needed)
- Single phase (no synthesis needed? — still mandatory per design)
- All phases fail (circuit breaker triggers)
- Sub-agent crash mid-task (retry → fail → escalate)
- Client agent always fails (infinite loop prevention via shared counter)
- Very large task count within a phase
- Circular dependency detection in DAG
- User cancels workflow mid-phase → clean shutdown at phase boundary
- JDTranslationError → fallback template used → workflow completes suboptimally

#### 6.3 Documentation

- `docs/Dynamic-Workflow.md` — User-facing documentation
- Update `CLAUDE.md` with new module reference
- Update `AGENTS.md` with architecture diagram
- Update `docs/Architecture.en.md` with workflow section

---

## Invariants Checklist

Map each design doc invariant to implementation:

| # | Invariant | Implementation |
|---|---|---|
| 1 | Repository and engine separated | `repository/` and `engine/` are separate packages |
| 2 | DAG is acyclic | `TaskDAG.would_create_cycle()` blocks cycle-creating edges |
| 3 | JD / Prompt decoupled | Main agent writes `JobDescription`, HR translates to `AgentDesign` |
| 4 | Sub-agent commits are atomic | `SubAgentPool.run_task()` does self-review then single commit. **Atomicity is application-level (Python dict ops), not transactional.** Workflow is session-scoped; crash = session loss. Acceptable for v1. |
| 5 | Teams execute serially | `Orchestrator._run_main_loop()` runs one phase at a time |
| 6 | Cross-team via store + main agent | Sub-agents in different phases never talk directly. **Blackboard is cross-phase visible by design** — Phase 1 sub-agents write findings that Phase 2 sub-agents can read. This is indirect coordination through a structured store, not direct communication. No real-time messaging, no address-based routing. This is consistent with the design doc's intent: "跨团队走仓库 + 主 agent". The Blackboard is part of the repository layer. |
| 7 | Inter-agent coordination via Blackboard | v1 uses structured Blackboard; /btw deferred to v2 |
| 8 | Self-destruct mechanism | `CircuitBreaker` tracks replans and triggers SELF_DESTRUCT |
| 9 | Client agent info locked | `ClientAgent.__init__()` stores requirements; never updated |
| 10 | Client fail shares replan counter | `CircuitBreaker.record_client_failure()` increments same counter |
| 11 | Single-direction control flow | Worker (propose) → Orchestrator (approve). Sub-agents cannot directly modify DAG, phase plan, or engine state. All structural mutations require Orchestrator approval. Sub-agent output reaches the store only through SubAgentPool.commit_subagent_result() which validates the diff. |

**Invariant #11 details** (control flow principle):

The system has one decision authority: the Orchestrator. All structural changes flow through it.

```
Worker sub-agent
  ↓ proposes (via Blackboard "proposals" section, or via task output)
SubAgentPool (engine layer)
  ↓ validates (commit_subagent_result checks diff for illegal state mutations)
PhaseRunner
  ↓ collects proposals, bundles with phase result
Orchestrator
  ↓ approves or rejects (final authority for all structural changes)
```

**Why not the 4-level chain from the design doc?** The design doc proposes Worker → HR → Engine → Orchestrator. In practice, HR is a recruitment agent — it shouldn't be making DAG or scope decisions. The engine (PhaseRunner) is mechanical execution — it shouldn't be an approval authority. Collapsing to Worker → Orchestrator keeps the decision path short, auditable, and easy to debug. HR's "push back" is limited to JD quality (vague/unreasonable), not structural changes.

Enforcement:
- Sub-agents do NOT hold a reference to `WorkflowStore` — `SubAgentPool` mediates all writes
- `SubAgentPool.commit_subagent_result()` validates that the diff only contains artifacts and task output, not phase/task state mutations
- `PhaseRunner` never modifies `_phase_plan` directly; it returns proposals to the Orchestrator
- `TaskDAG.apply_modification()` returns proposals that only the Orchestrator can approve
- HR can reject a JD but cannot rewrite the phase plan; it returns `PushBackResult` for the Orchestrator to act on

---

## Deferred to v2

These are explicitly out of scope for the first implementation:

1. **Cross-session workflow persistence** — Design doc says single-session. **v1 mitigation**: `WorkflowStore.save(path)` and `WorkflowStore.load(path)` stubs are included in v1. They serialize/deserialize the store to JSON. No auto-persistence, but users can manually save and restore workflows. Full auto-persistence deferred to v2.
2. **Parallel team execution** — Design doc says serial only (API concurrency limit)
3. **/btw async messaging** — Design doc section 4.7. Requires FSM modification (inbox check at observe→think boundary), message classification heuristics with no operational definition, timeout/retry logic. Blackboard handles v1 coordination needs without any of this complexity.
4. **LLM-based message classification** — N/A for v1 (no /btw). When /btw is added, start with heuristics.
5. **Talent registry similarity with embeddings** — Use token-based Jaccard for v1
6. **Multi-HR parallel planning** — Design doc marks as P2
7. **Workflow templates / replay** — Not in design doc
8. **Token cost optimization** — Design doc says "not considering"

---

## File Count Summary

| Phase | New Files | New Tests |
|---|---|---|
| 0: Models & Store | 10 | 2 |
| 1a: CircuitBreaker + DAG + State Machines | 3 | 2 |
| 1b: PhaseRunner + Orchestrator + Manager + Tools | 5 | 3 |
| 2: HR & Registry | 4 | 3 |
| 3: Blackboard | 3 | 1 |
| 4: Client Agent | 2 | 1 |
| 5: CLI & Wiring | 3 | 1 |
| 5a: Integration Debugging | 0 | 0 |
| 6: Testing & Docs | 1 | 5 |
| **Total** | **~31** | **~18** |

---

## Recommended Execution Order

```
Phase 0 ──→ Phase 1a ──→ Phase 1b ──→ Phase 2 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5 ──→ Phase 5a ──→ Phase 6
(3-4d)      (2-3d)       (4-5d)       (4-5d)      (2-3d)      (2-3d)      (3-4d)      (3-5d)      (3-4d)

Total: 26-36 working days
```

**Phase 1 split**:
- Phase 1a (CircuitBreaker + DAG + TaskCard state machines): 2-3 days. Pure logic, no LLM integration.
- Phase 1b (PhaseRunner + Orchestrator + WorkflowManager + tools): 4-5 days. Complex integration with existing agent system.

**Phase 5a (Integration Debugging)**: 3-5 days reserved for cross-component interface fixes, end-to-end wiring, and edge case discovery that only surfaces during integration.

**Critical path**: Phase 0 → Phase 1a → Phase 1b → Phase 2 (HR agent is the hardest piece)

**Can be parallelized**:
- Phase 3 (Blackboard) can start in parallel with Phase 2 once Phase 0 is done (no dependency on engine)
- Phase 4 (client agent) can start in parallel with Phase 3

---

## Resolved Design Decisions

These were open questions in v1 of the plan. Decisions made based on review feedback:

1. **Orchestrator = three async tools, not replacement** — `WorkflowManager` singleton exposes `workflow_start` (returns `workflow_id` immediately), `workflow_status` (polls progress), and `workflow_cancel` (signals cancellation). `WorkflowOrchestrator` runs in a background daemon thread. The main agent's ReAct loop is never blocked. Simple queries never touch the workflow system.

2. **How does the main agent decide?** — Skill router pattern. Task complexity threshold (multi-step, multiple domains) triggers workflow. Otherwise direct response. The exact threshold is tuned during integration testing.

3. **Sub-agent LLM** — Clone parent LLM (same model, same config). Client agent can use smaller model (configurable via `WorkflowSettings.client_agent_model`).

4. **Inter-agent coordination** — Blackboard for v1 (structured, deterministic, no FSM changes). /btw deferred to v2.

5. **HR scope** — Workflow-scoped. One HR per workflow, not per phase. Maximizes talent registry utilization.

6. **HR responsibilities** — Recruitment only. Process QA is team-internal reviewers (recruited by HR as part of the team).
