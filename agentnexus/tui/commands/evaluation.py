"""Evaluation commands backed by the shared evaluation service."""

import json

from . import COMMAND_HELP, split_action


def run(context, command, args, sid):
    """Inspect tasks and run suites with the desktop's baseline semantics."""
    from agentnexus.services.eval import get_eval_service

    service = get_eval_service(getattr(context.runtime, "modules", None))
    action, rest = split_action(args, "suites")
    if action == "suites":
        return {"suites": service.list_suites()}
    if action == "tasks":
        options = json.loads(rest) if rest else {}
        return {"tasks": service.list_tasks(**options)}
    if action == "task":
        task = service.get_task(rest)
        if task is None:
            raise ValueError(f"Task not found: {rest}")
        return task
    if action == "validate":
        return service.validate_dataset()
    if action == "baseline" and rest:
        baseline = service.load_baseline(rest)
        return {**baseline, "exists": True} if baseline is not None else {"suite_name": rest, "exists": False}
    if action == "run":
        suite, raw_options = split_action(rest, "")
        options = json.loads(raw_options) if raw_options else {}
        if not suite or set(options) - {"n_trials", "concurrency"}:
            raise ValueError("请输入套件名，可选参数为 n_trials 和 concurrency")
        if not 1 <= options.get("n_trials", 1) <= 100 or not 1 <= options.get("concurrency", 4) <= 32:
            raise ValueError("n_trials 必须在 1 到 100 之间，concurrency 必须在 1 到 32 之间")
        return service.run_suite(suite, **options)
    if action == "save" and rest:
        report = service.run_suite(rest)
        return {"status": "saved", "path": service.save_baseline(rest, report)}
    if action == "compare" and rest:
        return service.compare_with_baseline(rest, service.run_suite(rest))
    raise ValueError(f"用法: {command} {COMMAND_HELP[command]}")
