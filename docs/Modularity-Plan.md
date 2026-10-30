# Independent runtime modules

## Goal and acceptance criteria

The agent loop, short-term memory, long-term memory, LLM Wiki, evaluation, and observability must each support independent disablement, removal, and replacement. Disabling or removing an agent loop leaves non-chat services usable. Disabling memory removes cross-turn retention, not the current turn's working messages. Optional modules must not be imported or reconstructed by unrelated services when disabled. Replacements are supplied at the composition boundary and used consistently by primary agents, subagents, tools, API, and CLI.

Default implementations remain available. Existing stored conversations and durable submissions must remain readable. Durable submission deduplication, recovery, and collaboration state belong to application persistence, not optional telemetry.

## Non-goals

Live replacement during an active run, a general plugin marketplace, database migrations, and replacing the default implementations' algorithms are outside this change.

## Public verification boundaries

Tests exercise runtime assembly and a completed chat turn, independent server routes and CLI commands, knowledge ingestion, memory tools, and conversation snapshot export and restore. Subprocess import blockers simulate missing implementations without deleting files. Replacement tests use independent implementations through the public contracts. External LLM calls and model downloads are excluded from deterministic verification.

## Affected modules

Application composition and configuration; shared module contracts; agents and subagent tools; memory and persistence; knowledge ingestion and Wiki; evaluation service and command registration; observability callers; HTTP dependency factories and routes; desktop configuration and disabled states where necessary; tests and architecture documentation.

## Rollback

Keep changes in independently verified units. Preserve existing storage schemas and default behavior. Revert a unit's implementation and matching tests together if its integration fails. Do not delete user data or rewrite Git history. No automatic commits or deployment are requested.

## Planned implementation units

1. Define module selection, replacement contracts, and executable verification helpers.
2. Separate required execution persistence from optional observability and migrate telemetry callers.
3. Make short-term and long-term memory replaceable and consistently optional across runtime, tools, and restoration.
4. Route main-agent and subagent creation through the selected engine and support an absent engine.
5. Make Wiki and evaluation independently optional and replaceable across ingestion, API, and CLI.
6. Verify combinations and absent packages, run backend gates and desktop tests if touched, and update usage documentation.

## Architecture worklist

- [x] Ground: trace current assembly, module calls, restoration, and durable execution state.
- [x] Sketch: compare two structurally distinct designs.
- [x] Agree: synthesize contracts within the authorized implementation scope.
- [x] Implement: complete each behavior with a failing test followed by implementation.
- [x] Reassess: verify boundaries and correct shared storage, background settings, event forwarding, and read-only UI assumptions exposed by integration.

## Design comparison rubric

Candidates are compared on independent missing-package behavior; consistent instance ownership across all entry points; preservation of durable execution and conversation data; small public contracts without private-field leakage; lifecycle isolation and cleanup; and direct behavioral verification through public entry points.

## Initial investigation evidence

At revision 231bd1a, 211 targeted existing tests pass. Import-removal probes show hard dependencies on observability and evaluation. Knowledge ingestion bypasses the Wiki switch; memory tools bypass the memory manager's long-term disable flag. Short-term restoration depends on concrete private fields. Main agents and subagents construct ReAct independently. These are the root causes this implementation must remove.

## Synthesis decision

Use candidate B's lazy construction, explicit failure states, lifetime ownership, and context-local observation sink with candidate A's six explicit immutable factory selections. The independent design review scored B 28/30 and A 25/30. Keep existing useful service operations behind protocols; reject generic inspection/mutation command unions and a general plugin framework. The host is used at composition and transport/tool binding boundaries, not passed into domain algorithms as an unrestricted service locator.

`core.modules` owns `ModuleSelection`, `ModulesConfig`, `ModuleFactories`, `ModuleContext`, and `ModuleHost`. Factory overrides select a callable, explicit `None` disablement, or the default provider. `optional(name, context)` skips disabled or absent implementations; `require` reports their unavailability. Initialization defects propagate as module load errors without fallback. Agent and STM instances are actor/session scoped; LTM, Wiki, evaluation, and observation are runtime scoped. `activate()` binds a runtime at entry boundaries and resets it on exit. Default imports occur inside `core.module_defaults` factory functions. Public algorithms receive the selected narrow contracts.

Current task persistence now lives in `storage.timeline`. Its file bytes and SQLite schemas were preserved, callers migrated, and the old location deleted. A subprocess blocks all observation imports while checking durable submissions, recovery, and collaboration. The new regression first failed, then passed after the move; 136 existing persistence and affected TUI tests also passed. Runtime import without evaluation likewise first failed, then passed after removing its unused eager service re-export.

## Integration evidence

All 64 startup selection combinations now pass behavioral tests through runtime assembly, chat completion or explicit agent unavailability, selected memory/Wiki/evaluation operations, observation, and durable task outcomes. Disabled and replaced factories are forbidden from invoking defaults in this matrix. Eleven focused module suites pass together, 145 tests.

Independent-process tests additionally block default implementation imports. The complete agents package can be absent while a minimal replacement completes subagents, main chat, persistence, and server startup. Pure exceptions, plan mode, context usage, JSON parsing, and token estimation moved to neutral core ownership so unrelated entry points do not import the removed implementation.

Runtime settings and resource tests exposed and fixed cross-owner Wiki storage, history database selection, and LTM vector collection reuse. The vector regression writes fixed embeddings to two real Chroma paths; before the fix one collection contained both records, after the fix each path contains one record and the surviving runtime remains searchable after its peer closes. Runtime closure preserves process-owned LLM pools and closes selected module resources once.

Transport checks cover selected HTTP and WebSocket providers in simultaneous applications, disabled submissions without durable claims, missing defaults, CLI errors and lifecycle, nested terminal operations, and CLI TUI/server composition. Additional evaluation entry points now select the runtime engine instead of directly constructing ReAct. Default-memory algorithm probes explicitly require their corresponding default modules and use an isolated owner.

The final combined CI-range backend run passed 4945 with 5 skipped and 9 existing asynchronous-test warnings. This includes 4572 unit, 147 complete integration, and 226 security tests. Top-level queue, retriever-reuse, and LTM-search regressions passed all 10. A focused module batch passed 179, and the actual Textual boundary suites passed all 6. Active-owner configuration tests, configured factory loading, CI evaluation behavior, and the independent source and boundary review are complete. All 65 evaluation dataset tasks validate. The actual CI evaluation command passes in an isolated empty home, with separate regressions proving that genuine failed tasks still fail the gate. Ruff against the full checkout, the no-comment gate, and `git diff --check` pass. No desktop files changed.

Earlier integration failures and the final commands are retained in the verification record. Real external model calls are outside deterministic verification. Module choices remain fixed at startup. Algorithm-specific memory and prompt-wording experiments reject unsupported selections instead of recreating default implementations.

## Acceptance evidence map

| Requirement | Behavioral evidence |
| --- | --- |
| Each of the six modules can be disabled while selected peers continue working | `test_module_combinations.py` runs all 64 selections through actual runtime assembly, chat or explicit unavailability, selected services, and durable outcomes. |
| Default implementations can be absent without breaking unrelated imports or startup | `test_optional_module_imports.py`, `test_agent_package_boundary.py`, and `test_transport_modules.py` block implementation imports in independent processes. A real default Agent also completes a turn with the other five implementations absent. |
| An independent Agent works in primary, delegated, and evaluated tasks | `test_modular_agent.py`, `test_subagent_modules.py`, and `test_evaluation_module_consumers.py` use public-only or frozen replacement engines and verify answers, events, collaboration, and persistence. |
| STM and LTM are independently optional and replaceable | `test_modular_memory.py` checks current-turn-only messages, public snapshots, text-memory tools, extraction without default embeddings, management operations, explicit storage paths, and background model-window ownership. |
| Wiki selection controls ingestion and management | `test_wiki_eval_modules.py` checks disabled hooks, selected ingestion, independent management services, failure before writes, and owned-store cleanup. |
| Evaluation selection covers API and CLI command families | `test_wiki_eval_modules.py` exercises both route namespaces and command families with a selected service. `test_memory_evaluation_ownership.py` and `test_evaluation_module_consumers.py` reject unsupported default-algorithm experiments without reconstructing defaults. |
| Observation is optional and cannot replace task outcomes | `test_optional_telemetry.py` verifies absent imports, selected sinks in parallel tools, independent trace and audit state, queries, write failures, and task-exception preservation. Factory initialization errors remain explicit. |
| Observation does not alter CI evaluation scores | `test_evaluation_module_consumers.py` runs the actual CI CLI operation with empty, answered, and aborted task records and both observation selections. Diagnostic-only records are excluded, while failed task roots remain in the denominator. |
| Required execution state survives observation removal | `test_execution_persistence.py` closes and reopens actual storage with observation imports blocked. Existing recovery, submission, collaboration, HEAD restore, retry-save, and versioned-flow suites retain their behavioral assertions. |
| Selection and resource lifetime belong to the runtime | `test_module_host.py` checks fixed selections, lazy status, concurrent construction, missing internal dependencies, no fallback, reverse cleanup, close-once behavior, and process-owned LLM pool survival. |
| Public entry points use their own selection | `test_transport_modules.py` exercises simultaneous HTTP and WebSocket applications, CLI errors, and TUI/server launchers. `test_tui_removed_agent.py` drives actual Textual history, session switching, inspector, settings, disabled submission, and custom-engine completion. |
| Configuration changes do not change startup module selections or peer settings | `test_runtime_settings_ownership.py` checks owner refresh, repeated same-value updates, YAML deltas, property-backed settings, and actual named-tool disablement and reload. |

Configuration and replacement contracts are documented in [Runtime modules](Runtime-Modules.md). Reproducible commands, environment, raw output paths, and final gate results are recorded in [the verification record](../log/2026-10-30-module-isolation.md).
