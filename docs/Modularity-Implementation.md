# Implementation contract

All workers use the acceptance criteria and architecture in Modularity-Plan.md. Backend changes require a failing behavioral test before implementation, followed by the relevant regression suite. Use CodeGraph before source searches and prefix every shell command with rtk. Add no explanatory source comments. Do not commit, delete data, rewrite Git history, or modify files assigned to another worker.

## Shared invariants

Disabled implementations are neither imported nor reconstructed by other entry points. Replacements flow from application composition to all consumers. Absent optional modules leave unrelated functions operational. Required submission, recovery, collaboration, and history persistence remain durable and keep their existing schemas. HEAD-aware restore, save-only retry, session ownership, and child-attempt isolation must survive unchanged.

Tests must exercise public behavior with independent implementations or blocked imports. Do not patch away the module boundary being tested. External network/model I/O may be replaced in deterministic tests. Report failing-test evidence, passing validation, unresolved cases, and exact files changed.

## Write ownership

- Parent: shared contracts, configuration, runtime composition, engine/subagent integration, verification driver, and final cross-module integration.
- Persistence worker: move observability/timeline.py to storage/timeline.py, migrate references to that ownership, and relevant persistence tests. Do not change tracer or optional-module configuration. Notify the parent before touching shared chat/runtime files and limit edits there to the timeline import.
- Memory worker: memory implementations/coordinator, snapshot codec, memory tools/provider, memory CLI/API, and memory-specific tests. Parent owns core/modules.py, core/module_defaults.py, core/config.py, tools/providers/base.py, and tools/__init__.py.
- Wiki/evaluation worker: Wiki and evaluation services, routes, CLI command modules, KB ingestion integration, and relevant tests. Parent owns CLI root registration, server/deps.py, and server/app.py.
- Telemetry worker after persistence: core/telemetry.py, default telemetry adapters, all trace imports/decorators, and observation tests. Parent owns shared runtime/module selection. Coordinate edits to services/chat.py and tools/subagent.py before changing behavior there.

## Historical constraints

Commit 1f2ee18 requires durable submission receipts, restart recovery, and retrying failed saves without executing the task again. Commit 902af56 requires durable collaboration messages applied at round boundaries. Commit 95dcd95 requires isolated child-attempt timelines. Commit 71006bb requires restoring the HEAD window rather than the journal tail. Commit 8a74069 records restart-induced journal/STM divergence and requires turn-boundary persistence. These requirements survive module disablement.

The removed extension subsystem in efbd789 supplies no recorded rationale beyond its removal. This implementation does not restore its manifest/plugin machinery.

## Integration verification swarm

- [x] Frame: partition the remaining entry points and runtime ownership checks. Each slice must return behavioral evidence for disablement, missing defaults, and replacement without affecting unrelated functionality.
- [x] Fan out: three local workers with disjoint writable paths.
- [x] Aggregate: inspect every slice and run the combined regression suites.
- [x] Report: consolidate proven behavior and outstanding gaps.

Current ownership supersedes the earlier worker split: the transport worker owns server/app.py, server/deps.py, CLI root registration, health checks, and new transport integration tests. The engine worker owns tools/subagent.py, tools/providers/subagent_provider.py, tools/collaboration.py, a neutral answer parser if needed, and subagent tests. The ownership worker owns core/modules.py, core/module_defaults.py, runtime lifecycle changes only in app/runtime.py, and host lifecycle tests. The parent owns remaining app/runtime.py behavior, services/chat.py and its tests, memory fixes, final verification, and documentation. Workers coordinate any shared-file edit before writing.

Integration is complete. The final combined backend run passed 4945 with 5 skipped: 4572 unit, 147 integration, and 226 security tests passed. Top-level regressions passed 10. The evaluation dataset validates, and the actual CI evaluation command and observation-independent scoring regressions pass. The acceptance evidence map is in `Modularity-Plan.md`; reproducible commands and limitations are in `../log/2026-10-30-module-isolation.md`.
