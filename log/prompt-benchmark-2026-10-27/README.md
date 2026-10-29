# 复跑提示词对照

在仓库根目录执行以下命令。使用已配置的 Agnes 模型。保留现有实验目录，为复跑选择新目录。

## 检查请求组装

```text
rtk proxy python -m agentnexus.evaluation.prompt_benchmark contracts --output log/prompt-benchmark-replay/contracts.json
rtk proxy python -m pytest tests/unit/test_prompt_benchmark.py tests/unit/test_agents_prompt_builder.py tests/unit/test_skill_profile.py -q
```

## 复跑三个方案

先运行冻结的旧版，再运行当前整套改写，最后运行仅组装修复。保持模型、回合上限、限流和重复数一致。

```text
rtk proxy python -m agentnexus.evaluation.prompt_benchmark run --output log/prompt-benchmark-replay/baseline --source-snapshot log/prompt-benchmark-2026-10-27/baseline-controlled/sources --model agnes/agnes-3.0-flash --protocol native --rpm 6 --repeats 3 --max-steps 12
rtk proxy python -m agentnexus.evaluation.prompt_benchmark run --output log/prompt-benchmark-replay/full --source-snapshot log/prompt-benchmark-2026-10-27/optimized-controlled/sources --model agnes/agnes-3.0-flash --protocol native --rpm 6 --repeats 3 --max-steps 12
rtk proxy python -m agentnexus.evaluation.prompt_benchmark run --output log/prompt-benchmark-replay/wiring --source-snapshot log/prompt-benchmark-2026-10-27/wiring-only-sources --model agnes/agnes-3.0-flash --protocol native --rpm 6 --repeats 3 --max-steps 12
```

## 比较完整结果

确认两组各有 45 条任务结果，再执行比较。比较器会拒绝缺失用例、重复用例和不一致控制参数。

```text
rtk proxy python -m agentnexus.evaluation.prompt_benchmark compare --before log/prompt-benchmark-replay/baseline --after log/prompt-benchmark-replay/full --output log/prompt-benchmark-replay/full-comparison.json
rtk proxy python -m agentnexus.evaluation.prompt_benchmark compare --before log/prompt-benchmark-replay/baseline --after log/prompt-benchmark-replay/wiring --output log/prompt-benchmark-replay/wiring-comparison.json
```

## 恢复中断

- 同一版本中断时，以相同命令恢复。已保存的结果会跳过。
- 提供商读取超时时，把带 `api_error` 的尝试文件移入该运行目录的 `errors/`，保留内容。
- 把失败阶段的 `summary.json` 也移入 `errors/`，然后按相同控制参数恢复。
- 只有记录器改变时，添加 `--resume-instrumentation`。其他源码或控制参数改变时，使用新的实验目录。
- 要使用原始记录器恢复，添加 `--frozen-evaluator`。不要同时添加 `--resume-instrumentation`。
- 保留模型产生的错误答案、格式违约和运行时崩溃。不要重跑这些结果来提高分数。

## 核对最终代码

将当前生产文件与所选方案的 `sources/` 比较。运行本轮的回归命令，检查 `regression.xml`。要回退提示词候选，只恢复本轮修改的对应文件；保留评测工具和原始证据，不重置其他改动。

## 复核修复答复

检查每条修复答复是否报告具体结果，而不是只说将给出答案。Thought 标签不单独决定通过或失败。为新运行生成独立的 `delivery-audit.json`；记录 `case_id`、`repeat`、布尔值 `delivery_passed`、`answer_sha256` 和 `reason`。指纹必须对应本次答复，不能复制旧运行的判断到不同答复。

保留自动汇总后，再应用审计文件重新执行比较命令。比较器会校验指纹。原始 `attempts/` 文件保持不变，模型失败不因重算评分而重跑。既有实验的 `*-automated.json` 是审计前结果，普通命名的比较文件是审计后结果。
