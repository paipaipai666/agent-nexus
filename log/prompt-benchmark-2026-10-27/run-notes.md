Historical checkpoint notes captured before the final three-arm review. The current results are in ../2026-10-27-prompt-benchmark.md.

# Prompt baseline and controlled optimization

## Goal and scope

Measure prompt assembly contracts separately from live model task acceptance. Use the configured Agnes API only. Preserve baseline sources, full model requests and responses, tool observations, actual Python artifacts, and provider-reported usage. Do not change permission enforcement, todo termination, or public APIs in this experiment.

## Environment

- Starting revision: `4773ec17cb0e3e68e169b2f780a83722fc33bd7c`.
- Host: Windows, Python 3.13 at `D:/python3.13/python.exe`.
- Live model: `agnes/agnes-3.0-flash`.
- Formal experiment configuration: temperature 0, native tool protocol, thinking requested off, 2048 maximum output tokens, 12 maximum agent steps, 6 requests per minute. Both phases use these same controls.
- Isolated workload: 15 fixtures, 3 repetitions per phase, 45 attempts per phase. Five categories have three fixtures each: lookup, repair, recovery, authorized repair, custom profile.
- Raw data: `log/prompt-benchmark-2026-10-27/`. Manifests include selected source snapshots, hashes of package source files, dataset hash, environment, and run configuration. `environment.json` records installed versions of the key dependencies. Credentials are never written to reports.

## Measurement rules

Repair acceptance requires an actual file edit and an agent-initiated successful check after the last edit against that exact file hash. Checks run independent literal examples in a subprocess. Reproduction is recorded separately and requires a failing check before the first edit; a failure introduced by a partial edit is not original-defect reproduction. Lookup acceptance requires both the expected answer and a matching source lookup. Recovery also requires reaching the mirror. Custom profile acceptance requires an exact code known only to the configured system template.

Artifact acceptance and complete delivery are reported separately. A verified file left behind by a budget-exhausted run does not count as complete delivery. Tool-call counts include executed observations, including the JSON compatibility protocol's actions.

API failures are reported separately and excluded from behavioral denominators. A live run stops on a provider failure rather than repeatedly charging requests or treating unavailable infrastructure as an agent defect. Comparisons require every planned case/repetition, matching controls, and no unrelated package source drift. Repetitions share fixture structure and are correlated; Wilson intervals describe attempt counts, not generalization to unseen tasks. Category results must accompany the aggregate so deterministic profile repairs cannot masquerade as broad reasoning improvements.

Wall time includes rate-limit waits. API time excludes those waits but still reflects service load. Model-call counts and the rate limit refer to logical provider invocations, not separately traced HTTP requests; the unchanged OpenAI SDK may perform internal retries. Tokens are reported separately; monetary cost is unknown without verified provider pricing. This fixture set does not measure long-running real repositories, context compaction, actual external APIs, or permission enforcement. Tool confirmations are auto-approved inside the disposable fixture. The authorization fixtures measure whether the model proceeds with a requested edit, not whether a real permission gate is secure.

Replay copies the current package and overlays frozen treatment files. Other package source hashes must remain equal between phases. This is a controlled comparison in this working environment, not a self-contained installation image. Native baseline request records contain messages, response text, usage, and timing; their separate tool definitions were not captured by the early recorder. The frozen evaluator source defines those schemas. The revised recorder captures native tool definitions and generation controls for later runs. Do not describe the earlier records as complete wire-level requests.

## Observations before optimization

The initial connectivity check returned HTTP 429 three times, with a provider message requesting platform identity verification for higher RPM. It produced no usable response and reported zero token usage. This is infrastructure evidence, not a baseline behavioral score.

The deterministic Agent.run request probe passed 0/4 contracts. Both ordinary and thinking-capable clients ignored the custom profile system template and received a forced Thought reminder after tool execution. Raw captured requests are in `baseline-contracts.json`.

The first two directories, `baseline/` and `baseline-v2/`, retain manifests and source snapshots only. Their attempt persistence was defective because a relative output directory was used after entering a temporary workspace. Those attempted results were discarded with that workspace and cannot be scored. An integration test reproduced and fixed the path error before the formal experiment.

After the user completed platform verification, a JSON compatibility pilot in `baseline-live/` produced usable responses. Its first repair passed artifact checks but exhausted 10 steps, repeatedly reading or editing already changed state. A second native pilot in `baseline-native/` completed two authorized repairs with four model calls each, then hit the free-tier rate limit at 18 RPM. These pilots use different controls and are excluded from the formal comparison. Only `baseline-controlled/` and `optimized-controlled/` belong to that comparison.

The formal baseline completed 45 outcomes: 36 deliveries passed and all nine custom-profile outcomes failed. The other 36 outcomes all passed. Two provider read timeouts were archived under `baseline-controlled/errors/` and retried with unchanged controls.

One third-round custom-profile attempt crashed in the original agent's `_on_emit_answer`: a `ToolError` in `last_subagent_payload` was read with `.get`. It is counted as failed delivery, not infrastructure failure. Its original requests, tokens, and timing were lost before the old recorder could persist them. The outcome is preserved with a traceback-location note and `telemetry_missing=true`; missing cost fields are null, not zero. Consequently baseline cost telemetry has 44 samples. Final cost comparisons use only matching pairs with available measurements. The runtime crash was reproduced locally; the lasting benchmark regression tests generic exception recording rather than requiring that product defect to remain present. The product defect is outside the prompt-treatment diff and remains unresolved.

Recording-only changes during recovery are retained under `recorders/`. Resume permits a changed evaluator only when all other package source hashes, dataset, model controls, and limits match; original run manifests remain unchanged. The final shared grader re-evaluates both phases' raw events.

## Treatment

- Respect explicit custom profile templates while retaining capability-based selection for the default ReAct profile.
- Preserve the custom template after its own tool heading instead of truncating it at a legacy section marker.
- Replace the forced Thought reminder with a neutral instruction to continue necessary execution and verification.
- Rewrite both default templates around actual execution, latest observations, post-edit verification, failure recovery, available tools, and complete delivery.
- Clarify that concrete authorization persists within its scope while project requirements and separate approvals still apply. The permission engine and todo termination rules are unchanged.

These changes are one combined live treatment. This experiment cannot attribute a behavioral gain to one individual sentence. The deterministic contract result isolates only the two assembly defects.

An additional ablation was prepared after the full treatment produced one ungrounded lookup answer without calling a tool. `wiring-controlled/` uses the corrected agent assembly with the original `react`, `react_think`, and autonomy text. It runs the same 45 attempts and controls after the full-treatment phase. All arms remain available. This is an exploratory follow-up prompted by an observed regression, not a preregistered or blinded study. Prefer a candidate that preserves non-profile delivery quality; when outcomes tie, prefer the smaller demonstrated change and examine paired token/call overhead rather than assuming more instructions help.

## Completed full-treatment comparison

The baseline and full-treatment phases both completed 45 outcomes. `comparison.json` reports 8 paired improvements and 1 regression, a descriptive net gain of 7/45 outcomes.

| Outcome | Baseline | Full rewrite |
| --- | ---: | ---: |
| Complete delivery | 36/45 | 43/45 |
| Custom profile output | 0/9 | 8/9 |
| Non-profile delivery | 36/36 | 35/36 |

The aggregate gain is concentrated in the profile fixtures. `01-lookup-1.json` in the full-treatment phase fabricated `build-ok-1` instead of using the release lookup tool. `02-profile-2.json` saw the correct `WORKFLOW-895` instruction but queried primary/mirror and then added a refusal/explanation instead of the required literal output.

Across 44 cost-available pairs, mean input tokens fell from 7,694 to 7,163 and mean output tokens from 237 to 132. Across the 35 pairs BOTH phases delivered successfully, mean input tokens rose from 8,094 to 8,455, calls from 3.57 to 3.63, and wall time from 48.0 to 49.4 seconds; output tokens fell from 211 to 152. The all-attempt averages therefore do not establish general task efficiency. One full-treatment provider timeout was archived and retried unchanged. Wiring-only results remain pending.

## Verification trail

The benchmark tests first failed collection because the module did not exist. After implementation, three passed and one exposed a missing category in the test fixture. Completing that fixture yielded 4/4 passing tests. Later regressions reproduced enum serialization, relative output persistence, artifact-versus-delivery scoring, partial coverage, unrelated runtime drift, and truncation of a custom template at its own tool heading. The current benchmark suite has 20 passing tests, including temporary source replay, native request controls, reproduction order, missing telemetry, exception recording, and separating cheap failures from common-success costs. The last combined relevant regression run passed 261 tests before that final cost-comparison test was added; its report will be refreshed for the selected final source state. Two existing injection tests had asserted the old prompt's first words; they now assert that the supplied system rules remain intact as the first system message.

The post-fix request probe passed 4/4 contracts. Ruff and the no-comment gate passed. A cross-model audit identified grading and reproducibility defects that were corrected; remaining measurement limits are documented above. The audit independently confirmed the full-treatment outcomes and warned against attributing the profile gain to general prompt quality.

## Reproduction

```text
python -m pytest tests/unit/test_prompt_benchmark.py -q
python -m agentnexus.evaluation.prompt_benchmark contracts --output log/prompt-benchmark-2026-10-27/baseline-contracts.json
python -m agentnexus.evaluation.prompt_benchmark run --output log/prompt-benchmark-2026-10-27/replayed-baseline --source-snapshot log/prompt-benchmark-2026-10-27/baseline-controlled/sources --protocol native --rpm 6 --repeats 3 --max-steps 12
python -m agentnexus.evaluation.prompt_benchmark run --output log/prompt-benchmark-2026-10-27/optimized-controlled --protocol native --rpm 6 --repeats 3 --max-steps 12
python -m agentnexus.evaluation.prompt_benchmark compare --before log/prompt-benchmark-2026-10-27/baseline-controlled --after log/prompt-benchmark-2026-10-27/optimized-controlled --output log/prompt-benchmark-2026-10-27/comparison.json
```

The original baseline sources were saved before production edits; the formal baseline replays them in a temporary package copy. The manifest rejects resuming with changed sources or settings. For a new implementation, choose a separate output directory. To roll back an optimization, restore only the reviewed production-file diff while retaining benchmark tools and evidence; do not reset unrelated work.
