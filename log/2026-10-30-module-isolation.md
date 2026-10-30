# Runtime module isolation verification

## Objective and environment

Six runtime modules must independently support disablement, implementation removal, and replacement. This record documents isolation and regression evidence, not a performance improvement claim. Implementation and validation used the shared Windows workspace, based on revision 231bd1a. The environment is Windows 11 10.0.26100 and CPython 3.13.6, MSC v.1944 AMD64. Commands run through RTK. External LLM traffic and model downloads are excluded from deterministic acceptance tests. Existing local embedding weights can load during skill-service startup.

## Observed failures and root causes

- Runtime import failed when evaluation or observation implementations were absent because unrelated callers imported them eagerly.
- Main agents and subagents independently constructed ReAct; services depended on private callback and output fields.
- Submission, recovery, collaboration and timeline persistence lived beneath optional observation ownership.
- Memory tools and Wiki ingestion reconstructed defaults instead of using the selected runtime provider.
- Wiki stores, new-session history databases and LTM vector collections could select process globals instead of their owning runtime settings.
- Some evaluation commands constructed ReAct directly after entering the selected evaluation service.
- Background memory window resolution read process settings rather than its own settings. The regression resolved 16000 tokens instead of the owning model's 32000. The worker now uses its instance settings and resolves a 24000-token compaction threshold after the configured 8000-token buffer.
- Event forwarding supplied three parameters to a replacement engine's one-signal callback. The public bridge now preserves the original argument list. Both one-signal and original state-machine callbacks complete and persist their answer.
- Runtime configuration writes refreshed the process cache while leaving the active owner stale. Central YAML deltas and an explicit changed-key hint for PUT update the active owner and preserve its fixed module selection. Named tool flags now affect registration and actual invocation after reload.
- The actual CI evaluation command on an empty isolated home failed after observation became runtime-owned and configured. Its own `BEFORE_EVAL_RUN` hook wrote an orphan diagnostic span, which the evaluator counted as a failed task. Execution records now require a task or LLM span. Six CLI cases cover empty, answered, and aborted tasks with observation on and off, and preserve aborted-task failures. Legacy LLM-only execution records remain supported.

The vector isolation regression used two temporary SQLite files and two Chroma directories, one three-dimensional fixed embedding and one fact per runtime. Before the change, the first collection contained two records instead of one. `storage.chroma.get_collection_at` now caches by normalized explicit storage path and collection name, and default LTM forwards its own settings. Afterward both actual collections contain one record, and the second runtime still returns its own fact after the first closes. No database schema changes were made.

## Reproduction and validation

```text
rtk pytest tests/unit/test_module_combinations.py -q
rtk pytest tests/unit/test_module_host.py -q
rtk pytest tests/unit/test_modular_memory.py tests/unit/test_long_term.py tests/unit/test_long_term_search.py -q
rtk pytest tests/unit/test_rag_store.py tests/unit/test_rag_store_catalog.py tests/unit/test_modular_memory.py tests/perf/test_perf_long_term_stress.py -q
rtk proxy python -X utf8 -m pytest tests/unit -q --tb=no --junitxml=.pytest_cache/modularity-unit-final.xml
rtk proxy python -X utf8 -m pytest tests/unit tests/integration tests/security -q --tb=no --timeout=60 --junitxml=.pytest_cache/modularity-backend-final.xml
rtk proxy python -X utf8 -m pytest tests/security -q --tb=short --junitxml=.pytest_cache/modularity-security-final.xml
rtk proxy python -X utf8 -m pytest tests/integration/test_server_api.py tests/integration/test_versioned_flow.py tests/integration/test_multi_turn_context.py tests/integration/test_memory_compaction_integration.py tests/integration/test_cli.py -q --tb=short --junitxml=.pytest_cache/modularity-integration-final.xml
rtk pytest tests/unit/test_tui_removed_agent.py tests/unit/test_tui_module_boundaries.py -q
rtk proxy python scripts/check_no_comments.py
rtk ruff check agentnexus tests scripts
rtk git diff --check
```

The startup matrix has 64 samples, one per Boolean selection across the six modules, and passed all 64. The combined eleven focused module suites passed 145 tests. The memory regression batch passed 57, and storage plus existing LTM stress regressions passed 41. These are counts of behavioral checks, not latency or throughput measurements.

An earlier complete unit run during concurrent integration produced 4419 passed, 29 failed, 2 skipped, and 9 warnings in 280.70 seconds. A later run produced 4558 passed, 3 failed, and 2 skipped in 336.99 seconds. Its three failures were fixtures still supplying private todo fields after consumers migrated to the public contract. The fields were migrated without changing their behavioral assertions; the affected performance and TUI workspace suites then passed all 48 tests. Elapsed time identifies each run and is not a before/after benchmark.

A subsequent full run passed 4564 with one failed custom-engine Textual case. A simultaneous focused run exposed the same failure. Automatic skill routing loaded cached embedding weights on the message path and exceeded the pilot's wait. The boundary fixture now disables unrelated automatic routing while keeping the real application, session navigation, selected engine, and persistence. The actual Textual boundary suites then passed all 6 cases. A fresh full run includes this fixture correction.

That pre-CI full unit run passed 4566 with 2 skipped and 9 existing asynchronous-test warnings in 309.72 seconds. A focused module batch passed all 179 in 32.17 seconds. This excludes the 6 actual Textual boundary cases, which passed separately and are also included in the full runs. Top-level event-queue, retriever-reuse, and LTM-search regressions passed all 10. Ruff across `agentnexus tests scripts`, the no-comment gate, and `git diff --check` passed. No desktop files changed, so desktop Vitest was outside the affected code.

Final security validation passed 226 with 3 skipped in 22.35 seconds. HTTP, CLI, conversation, checkpoint, and compaction integration suites passed all 44 in 10.24 seconds. The empty-log CLI test now checks the selected backend's consistent `Trace not found` message instead of a local-directory-specific message. Dedicated prompt-wording experiment tests reject disabled or replacement engines before touching their frozen default-engine inputs; 98 related evaluation tests passed. A configured `package.module:factory` loaded from a temporary Python module completed a real chat turn and persisted its answer.

The complete integration suite then exposed four remaining old `search` and `save` mock uses in `test_multi_turn_ltm.py`. Those mocks now implement the text contract, with their context and extracted-content assertions preserved. All 147 integration tests passed in 47.07 seconds. The CI diagnostic-record correction passed 110 related evaluation, CLI, statistics, and cost regressions. The exact `python -m agentnexus eval ci -d 1` command passed in an isolated empty home with `No traces to evaluate`. Evaluation dataset validation reported 65 tasks and no errors. Ruff passed against the complete checkout with `rtk ruff check .`.

The final combined unit, complete integration, and security run with CI's 60-second per-test timeout passed 4945 with 5 skipped and 9 existing asynchronous-test warnings in 357.78 seconds. JUnit confirms zero failures and zero errors across 4950 cases. The passing breakdown is 4572 unit, 147 integration, and 226 security cases. The 5 skips comprise 2 unit and 3 security cases. This run includes the final CI diagnostic-record correction and all six observation-on/off scoring regressions.

Raw JUnit files are `.pytest_cache/modularity-backend-final.xml`, `.pytest_cache/modularity-unit-final.xml`, `.pytest_cache/modularity-focused-final.xml`, `.pytest_cache/modularity-security-final.xml`, `.pytest_cache/modularity-integration-final.xml`, and `.pytest_cache/modularity-integration-all.xml`. Intermediate full results are retained in `.pytest_cache/modularity-unit-results.xml` and `.pytest_cache/modularity-unit-intermediate.xml`. The CI regression's failing cases are in `.pytest_cache/modularity-eval-ci-red.xml`. RTK command logs are under the user's AppData/Local/rtk/tee directory. The acceptance-to-test mapping is in `docs/Modularity-Plan.md`.

## Limitations

Real-provider LLM behavior and downloaded embedding models have not been exercised. Default-specific memory and prompt-wording experiments require their default implementations and do not certify arbitrary replacement algorithms. Replacements can participate in general evaluations through their public operations, with unsupported optional capabilities reported explicitly. This change does not add hot replacement during execution or change database schemas. A direct comparison with `HEAD:agentnexus/observability/timeline.py` confirmed that `storage/timeline.py` preserves the original source after normalizing checkout line endings. Verification runs on the Windows/Python environment above, not the other operating systems and Python versions in the CI matrix. All implementation and validation work required by the acceptance evidence map is complete.
