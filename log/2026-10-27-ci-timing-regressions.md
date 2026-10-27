# CI timing and asynchronous completion regressions

## Scope and environment

Investigation date: 2026-10-27. Baseline: `1f2e75d4327184db2c7d3ae5c50cb50ac7dd5ecc`.
CI evidence: https://github.com/paipaipai666/agent-nexus/actions/runs/40451622129.
Local verification uses Windows 11 build 26100, CPython 3.13.6, AMD64.
CI uses Python 3.11, 3.12 and 3.13 on Linux, Windows and macOS.
No throughput improvement is claimed. This investigation checks deadline enforcement,
event-loop responsiveness and observation of completed UI operations.

## Premise and failure census

Previous fixes assumed that closing an HTTP client interrupts its blocking socket read,
and that a completed service operation or key event means the resulting UI is mounted.
Neither assumption is guaranteed by the inspected implementations.

One CI run contains nine platform/version test jobs. Linux passes all three versions.
Windows 3.13 and macOS 3.13 pass. The other four jobs contain six failed test invocations:

| Job | Failure |
| --- | --- |
| macOS 3.11 | Anthropic stream takes 1.237 seconds against a 1.2-second assertion |
| macOS 3.12 | Total-budget test hits the independent chunk-gap watchdog first |
| Windows 3.11 | Reasoning widget not mounted after switching sessions; upload probes take 3.59 seconds |
| Windows 3.12 | Final answer not rendered after service completion; deferred scroll not applied |

Local raw census input and repeatable summarizer are `.tmp_pytest/ci-census.json`
and `.tmp_pytest/ci_census.py`. They are ignored investigation artifacts, not required
test dependencies. Re-run with:

```powershell
rtk python .tmp_pytest/ci_census.py .tmp_pytest/ci-census.json
```

## Root causes and changes

The installed httpcore synchronous stream closes the socket without calling shutdown.
The Anthropic timer previously closed the client, which can leave an existing read
waiting for its per-read timeout. Per-line checks cannot run during that wait.
Cancellation now shuts down both socket directions before closing the response and
client. The existing per-line deadline checks remain in place.

A local regression deliberately makes client.close ineffective, delays headers by
0.8 seconds and pauses the stream for 3 seconds after its first event. With a 1.5-second
probe budget, the baseline takes 2.3367724 seconds, one measured sample. After the fix,
the same test passes its unchanged 1.9-second upper bound. Exact post-fix elapsed time
was not separately recorded. The assertion bounds one local verification sample.

The total-budget watchdog test formerly scheduled chunks every 0.4 seconds with a
0.5-second gap limit. Scheduling delay could legitimately trigger the gap watchdog.
It now uses a stream blocked until closure, a 0.1-second total budget and a 10-second gap
limit. It checks both the correct error and termination of the stream reader.

TUI tests now observe the final rendered answer, restored reasoning body and applied
scroll rather than treating backend status or key dispatch as completion. The fake
running agent remains gated until the test releases it, with an asserted safety timeout.

Upload tests previously created a separate TestClient portal/event loop per request.
Their wall-clock assertion could not establish that the upload's event loop remained
responsive. They now reuse a context-managed client and hold ingestion behind an event.
For both 10 MiB and 50 MiB files, health, documents and configuration requests must all
finish before ingestion is released. Fixture teardown drains the upload tasks.
Each client owns a separate task registry, so cleanup cannot await futures belonging
to another test's event loop.
This is an ordering assertion, not a replacement performance percentile measurement.

## Validation and reproduction

```powershell
rtk python -m pytest tests/integration/test_model_capability_startup.py tests/unit/test_anthropic_provider.py -q --tb=short --timeout=60
rtk python -m pytest tests/unit/test_openai_provider_watchdog.py tests/unit/test_tui_workspace.py tests/unit/test_tui_native.py tests/unit/test_memory_context.py::TestChatScreenAnswerRender -q --tb=short --timeout=60
rtk python -m pytest tests/integration/test_real_blocking.py -q --tb=short --timeout=60
rtk python -m pytest tests/unit/ tests/integration/ tests/security/ -q --tb=short --timeout=60
rtk ruff check .
rtk python scripts/check_no_comments.py
```

Targeted verification: 52 provider/TUI tests pass; eight upload tests pass.
The socket-interruption regression fails before the provider fix and passes afterward.
The ignored `.tmp_pytest/check_blocking_detection.py` substitutes inline execution for
the ingestion executor. The new upload test fails as expected after the worker's
10-second safety timeout. This confirms the test detects actual event-loop blocking.

Fix commits: `040e236` interrupts pending Anthropic reads; `70a3417` isolates asynchronous
test lifetimes and waits for completed UI updates.
The first complete verification ran all 4668 test assertions successfully but found
seven teardown errors: cleanup initially awaited tasks in a shared registry, including
futures belonging to earlier event loops. The fixture now installs a registry per
client. `.tmp_pytest/check_upload_task_isolation.py` seeds the old registry with an
unresolved future from a closed loop, then runs all eight upload tests successfully.
Final complete verification: 4668 passed, five skipped, nine pre-existing coroutine
warnings, zero errors, 207.43 seconds. Ruff, the no-comment gate and diff checks pass.
macOS and Python 3.11/3.12 have not been executed locally. CI must validate those targets.
No latency distribution, CPU-load sweep or production upload timing was measured.
