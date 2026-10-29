# 复跑文案独立对照

在仓库根目录运行。使用已配置的 Agnes，不需要把密钥写入脚本或报告。

```text
rtk proxy python -m pytest tests/unit/test_prompt_holdout.py tests/unit/test_prompt_benchmark.py -q
rtk proxy python -m agentnexus.evaluation.prompt_holdout freeze --output log/prompt-holdout-replay --old log/prompt-benchmark-2026-10-27/wiring-only-sources --new log/prompt-benchmark-2026-10-27/optimized-controlled/sources --rpm 6 --max-steps 16
rtk proxy python -X utf8 -m agentnexus.evaluation.prompt_holdout run --output log/prompt-holdout-replay --old log/prompt-benchmark-2026-10-27/wiring-only-sources --new log/prompt-benchmark-2026-10-27/optimized-controlled/sources --rpm 6 --max-steps 16
rtk proxy python -m agentnexus.evaluation.prompt_holdout summary --output log/prompt-holdout-replay
```

评测器固定生成48个任务、96条交错执行计划。`freeze` 保存任务、版本、控制参数、评分规则和完整运行源码。`run` 每次启动新进程，并核对冻结指纹。`summary` 在完整执行、全部答复复核之前不作替换建议。只能启动一个 runner，共享 `last-call.txt` 用于全局限流。

中断后使用相同命令恢复。已完成结果跳过，不重跑模型错误。若一条 `api_error` 非空，先把该条原始 JSON 移到本目录的 `errors/` 并保留唯一文件名，再恢复；仅基础设施错误允许重试。不要在运行中修改源码或候选文案。

最终答复审计写入 `delivery-audit.json`。每条判断包含 `case_id`、`arm`、`answer_sha256`、布尔值 `delivery_passed`、布尔值 `serious_error` 和理由。覆盖全部96条结果后才满足决策要求，指纹必须对应本次原文。保留自动评分副本后再应用审计，原始 `attempts/` 不修改。

环境和范围见 `plan.md`、`environment.json`、`manifest.json`。验收只支持受限纯函数 Python，Windows通过 Job Object 限制每个验收进程128MiB内存，8秒超时。任务由项目场景构造，没有宣称是真实仓库历史修复。

## 应用答复复核与计算消耗

完成全部结果后生成匿名包，再由复核者按冻结规则填写 `audit-reviews/`。版本映射文件不给复核者，答复文风仍可能暴露版本，因此只称隐藏分配的模型辅助复核。

```text
rtk proxy python -X utf8 log/prompt-holdout-2026-10-28/audit_answers.py pack
rtk proxy python -X utf8 log/prompt-holdout-2026-10-28/audit_answers.py apply
rtk proxy python -m agentnexus.evaluation.prompt_holdout summary --output log/prompt-holdout-2026-10-28
rtk proxy python -X utf8 log/prompt-holdout-2026-10-28/analyze.py
```

这两个辅助脚本按自身所在目录读取数据。复跑到新目录时，先复制辅助脚本，再从新目录中的对应路径调用。保留 `summary-automated.json` 后才应用复核；`analyze.py` 拒绝数据或复核不完整的集合，并分别计算全部结果、共同成功任务的消耗。

`all_attempts_first_request_order` 包括归档超时，表示每题实际最先请求的版本，本次为 24/24。`retained_attempt_first_request_order` 仅使用最终保留的有效尝试，本次为旧 25/新 23。不要把重试后的有效记录顺序误当成最初执行顺序。

若要复用本次完整冻结的运行代码，在 `runtime/new/` 目录运行同一评测入口，给出输出、old、new 的绝对路径。通常在仓库根目录重跑只保证沿用方法和控制参数，后续代码版本和供应商后端可能已经变化。
