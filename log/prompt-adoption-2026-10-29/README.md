# 验证已采用的提示词修复

当前方案保留自定义主模板、模板正文和工具后提醒的装配修复，三份通用文案使用旧版。401 个包文件与上一轮正式复验的旧文案组相同。此次没有再次请求 Agnes，效果证据与限制见 [实验记录](../2026-10-27-prompt-benchmark.md)。

从仓库根目录运行以下命令。验证脚本会在文件内容偏离该已测试方案时返回非零退出码，保存每个文件的预期与实际 SHA-256。未来有意修改源码也会触发差异，这项检查用于确认本次采用版本。

```powershell
rtk proxy python -X utf8 log/prompt-adoption-2026-10-29/verify.py --output log/prompt-adoption-2026-10-29/verification-rerun.json
rtk proxy python -X utf8 -m agentnexus.evaluation.prompt_benchmark contracts --output log/prompt-adoption-2026-10-29/contracts-rerun.json
```

`before.json` 记录切换前仅三份文案存在差异，`after.json` 记录切换后 401/401 一致。`contracts.json` 保存 4/4 项检查的实际请求，覆盖自定义模板与普通、思考模型的工具后提醒。自定义模板中工具标题后的约束由相关回归另行检查。

本次回归结果为 328 通过、0 失败、0 错误，保存在 `regression.xml`。复跑同一范围：

```powershell
rtk proxy python -m pytest tests/unit/test_prompt_benchmark.py tests/unit/test_prompt_holdout.py tests/unit/test_skill_profile.py tests/unit/test_agents_prompt_builder.py tests/unit/test_todo_double_answer.py tests/unit/test_plan_mode.py tests/unit/test_prompts.py tests/unit/test_prompts_completeness.py tests/unit/test_security_prompt_injection.py tests/security/test_indirect_injection.py tests/unit/test_calling_strategy.py tests/unit/test_e2e_react_agent.py tests/unit/test_react_fsm.py tests/unit/test_react_action_parse.py tests/unit/test_agent_hooks.py tests/unit/test_agents_react_runtime.py tests/unit/test_agents_tool_runner.py -q --junitxml=log/prompt-adoption-2026-10-29/regression-rerun.xml
rtk proxy python -m ruff check agentnexus/agents/re_act_agent.py agentnexus/evaluation/prompt_benchmark.py agentnexus/evaluation/prompt_holdout.py tests/unit/test_prompt_benchmark.py tests/unit/test_prompt_holdout.py tests/security/test_indirect_injection.py log/prompt-adoption-2026-10-29/verify.py
rtk proxy python scripts/check_no_comments.py
rtk git diff --check
```

整套新文案仍保存在 `log/prompt-holdout-2026-10-28/runtime/new/agentnexus/prompts/`，本次采用的旧文案保存在对应的 `runtime/old/`。需要恢复候选时，先核对当前未提交改动，再只复制三份文案 `react.txt`、`react_think.txt`、`fragments/autonomy.txt`。保留运行代码与证据目录。恢复候选后必须重新验证；本指南没有自动切换命令。

代码、评测工具和原始证据随本次提交归档。实验目录通过 `.gitattributes` 保留原始换行字节，限流锁和启动标记等协调文件由 `.gitignore` 排除。此次不处理严格输出、todo 提前结束和子代理错误对象等其他缺陷，也不对真实仓库任务或其他模型推断收益。
