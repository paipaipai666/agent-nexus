# 复跑文案独立对照

在仓库根目录运行。使用已配置的 Agnes，不需要把密钥写入脚本或报告。

```text
rtk proxy python -m pytest tests/unit/test_prompt_holdout.py tests/unit/test_prompt_benchmark.py -q
rtk proxy python -m agentnexus.evaluation.prompt_holdout freeze --output log/prompt-holdout-replay --old log/prompt-benchmark-2026-10-27/wiring-only-sources --new log/prompt-benchmark-2026-10-27/optimized-controlled/sources --rpm 6 --max-steps 16
rtk proxy python -X utf8 -m agentnexus.evaluation.prompt_holdout run --output log/prompt-holdout-replay --old log/prompt-benchmark-2026-10-27/wiring-only-sources --new log/prompt-benchmark-2026-10-27/optimized-controlled/sources --rpm 6 --max-steps 16
rtk proxy python -m agentnexus.evaluation.prompt_holdout summary --output log/prompt-holdout-replay
```

评测器固定生成48个任务、96条交错执行计划。`freeze` 保存任务、版本、控制参数、评分规则和完整运行源码。`run` 每次启动新进程，并核对冻结指纹。`summary` 在完整执行、全部答复复核之前不作替换建议。只能启动一个 runner，共享 `last-call.txt` 用于顺序限流。

中断后使用相同命令恢复。已完成结果跳过，不重跑模型错误。若一条 `api_error` 非空，先把该条原始 JSON 移到本目录的 `errors/` 并保留唯一文件名，再恢复；仅基础设施错误允许重试。不要在运行中修改源码或候选文案。

最终答复审计写入 `delivery-audit.json`。每条判断包含 `case_id`、`arm`、`answer_sha256`、布尔值 `delivery_passed`、布尔值 `serious_error` 和理由。覆盖全部96条结果后才满足决策要求，指纹必须对应本次原文。保留自动评分副本后再应用审计，原始 `attempts/` 不修改。

环境和范围见 `plan.md`、`environment.json`、`manifest.json`。验收只支持受限纯函数 Python，Windows通过 Job Object 限制每个验收进程128MiB内存，8秒超时。任务由项目场景构造，没有宣称是真实仓库历史修复。
