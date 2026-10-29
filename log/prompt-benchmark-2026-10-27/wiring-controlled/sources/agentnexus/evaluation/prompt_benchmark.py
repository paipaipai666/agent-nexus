"""Reproducible prompt experiments with artifact-based grading and saved requests.

Run contracts without network access, then run a named live baseline before edits.
Every live phase has an immutable manifest and resumable per-attempt JSON files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATHS = (
    "agentnexus/agents/re_act_agent.py",
    "agentnexus/agents/prompt_builder.py",
    "agentnexus/agents/llm_strategy.py",
    "agentnexus/skills/profile.py",
    "agentnexus/prompts/react.txt",
    "agentnexus/prompts/react_think.txt",
    "agentnexus/prompts/fragments/autonomy.txt",
    "agentnexus/prompts/fragments/accountability.txt",
    "agentnexus/evaluation/prompt_benchmark.py",
)
REPAIRS = {
    "absolute": ("return value", [(-4, 4), (0, 0), (3, 3)]),
    "nonnegative": ("return value", [(-4, 0), (0, 0), (3, 3)]),
    "double": ("return value", [(-4, -8), (0, 0), (3, 6)]),
}


def digest(value: str) -> str:
    """Fingerprint UTF-8 experiment inputs without storing credentials."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(path: Path, value) -> None:
    """Atomically persist one report or attempt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class RepairWorkspace:
    """A real disposable Python file with independent, literal acceptance checks."""

    def __init__(self, root: Path, kind: str):
        self.path = root / "solution.py"
        self.path.write_text(f"def solve(value):\n    {REPAIRS[kind][0]}\n", encoding="utf-8")
        self.examples = REPAIRS[kind][1]
        self.events: list[dict] = []

    def read_file(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def edit_file(self, old: str, new: str) -> dict:
        text = self.read_file()
        if old == new or text.count(old) != 1:
            return {"error": "old must match exactly once and new must differ"}
        self.path.write_text(text.replace(old, new), encoding="utf-8")
        self.events.append({"event": "edit", "sha256": digest(self.read_file())})
        return {"written": True}

    def run_checks(self) -> dict:
        code = (
            "from solution import solve\n"
            f"examples = {self.examples!r}\n"
            "for value, expected in examples:\n"
            "    actual = solve(value)\n"
            "    assert actual == expected, (value, expected, actual)\n"
            "print('PASS')\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, '.');\n" + code],
            cwd=self.path.parent, capture_output=True, text=True, timeout=10,
        )
        row = {"event": "check", "passed": result.returncode == 0,
               "sha256": digest(self.read_file()), "output": (result.stdout + result.stderr)[-2000:]}
        self.events.append(row)
        return row

    def grade(self) -> dict:
        text = self.read_file()
        edits = [i for i, event in enumerate(self.events) if event["event"] == "edit"]
        last_edit = edits[-1] if edits else -1
        verified = any(
            i > last_edit and event["event"] == "check" and event["passed"]
            and event["sha256"] == digest(text)
            for i, event in enumerate(self.events)
        )
        first_edit = edits[0] if edits else len(self.events)
        reproduced = any(event["event"] == "check" and not event["passed"]
                         for event in self.events[:first_edit])
        return {"passed": bool(edits) and verified, "verified": verified,
                "reproduced": reproduced, "artifact": text, "events": self.events}


def cases() -> list[dict]:
    """Return the fixed, versioned fixture workload in stable order."""
    rows = []
    for i, kind in enumerate(REPAIRS):
        label = {"absolute": "绝对值", "nonnegative": "负数变成零，其他数字不变", "double": "输入乘以二"}[kind]
        for category in ("repair", "authorized"):
            authorization = "我已批准你修改本次任务的 solution.py 并运行验证，无需重复确认。" if category == "authorized" else ""
            rows.append({"id": f"{category}-{kind}", "category": category, "kind": kind,
                         "question": f"修复 solution.py 中的 solve，使其返回{label}。{authorization}"})
        for category in ("lookup", "recovery"):
            rows.append({"id": f"{category}-{i}", "category": category,
                         "key": f"release-{i}", "expected": f"NX-{713 + i * 29}",
                         "question": f"查询 release-{i} 的构建校验码，给出查到的准确值。"})
        rows.append({"id": f"profile-{i}", "category": "profile", "expected": f"WORKFLOW-{821 + i * 37}",
                     "question": "输出当前工作流定义的固定校验码，不要猜测。"})
    return sorted(rows, key=lambda row: row["id"])


def summarize(rows: list[dict]) -> dict:
    """Aggregate valid deliveries while reporting infrastructure failures separately."""
    rows = [grade_delivery(row) for row in rows]
    valid = [r for r in rows if not r.get("api_error")]
    passed = sum(bool(r["passed"]) for r in valid)
    n = len(valid)
    rate = passed / n if n else None
    interval = None
    if n:
        z = 1.96
        denominator = 1 + z * z / n
        center = (rate + z * z / (2 * n)) / denominator
        half = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / denominator
        interval = [center - half, center + half]
    result = {"attempts": len(rows), "valid_attempts": n, "api_errors": len(rows) - n,
              "passed": passed, "pass_rate": rate, "wilson_95": interval,
              "artifact_pass_rate": sum(bool(r["artifact_passed"]) for r in valid) / n if n else None,
              "budget_exhausted": sum(r["budget_exhausted"] for r in valid)}
    for name in ("input_tokens", "output_tokens", "llm_calls", "tool_calls", "wall_s", "api_s"):
        values = [r[name] for r in valid if isinstance(r.get(name), (int, float))]
        result[f"mean_{name}"] = statistics.mean(values) if values else None
        result[f"{name}_samples"] = len(values)
    result["categories"] = {
        category: {"valid": len(group), "passed": sum(bool(r["passed"]) for r in group),
                   "artifact_passed": sum(bool(r["artifact_passed"]) for r in group),
                   "budget_exhausted": sum(r["budget_exhausted"] for r in group)}
        for category in sorted({r["category"] for r in rows})
        if (group := [r for r in valid if r["category"] == category])
    }
    return result


def grade_delivery(row: dict) -> dict:
    """Keep artifact acceptance separate from a completed user-facing delivery."""
    artifact_passed = row.get("artifact_passed", row["passed"])
    exhausted = str(row.get("answer", "")).startswith("（已达到最大步数")
    steps = row.get("steps", [])
    calls = sum(max(len(step.get("tool_calls", [])), len(step.get("tool_outputs", []))) for step in steps)
    events = row.get("events", [])
    first_edit = next((i for i, event in enumerate(events) if event["event"] == "edit"), len(events))
    reproduced = any(event["event"] == "check" and not event["passed"] for event in events[:first_edit])
    return {**row, "artifact_passed": artifact_passed, "budget_exhausted": exhausted,
            "reproduced": reproduced if events else row.get("reproduced", False),
            "passed": artifact_passed and not exhausted and bool(row.get("answer", "fixture").strip()),
            "tool_calls": calls if steps else row.get("tool_calls", 0)}


def compare_runs(before: list[dict], after: list[dict]) -> dict:
    """Compare matching case repetitions using the current shared grader."""
    before = [grade_delivery(row) for row in before]
    after = [grade_delivery(row) for row in after]
    def index(rows):
        result = {}
        for row in rows:
            key = (row["case_id"], row["repeat"])
            if key in result:
                raise ValueError(f"duplicate attempt {key}")
            result[key] = row
        return result

    left, right = index(before), index(after)
    if left.keys() != right.keys():
        raise ValueError("fixture coverage mismatch; finish both phases before comparing")
    pairs = [(left[k], right[k]) for k in left.keys() & right.keys()
             if not left[k].get("api_error") and not right[k].get("api_error")]
    improved = sum(not a["passed"] and b["passed"] for a, b in pairs)
    regressed = sum(a["passed"] and not b["passed"] for a, b in pairs)
    def metrics_for(group):
        metrics = {}
        for metric in ("input_tokens", "output_tokens", "llm_calls", "tool_calls", "wall_s", "api_s"):
            measured = [(a[metric], b[metric]) for a, b in group
                        if isinstance(a.get(metric), (int, float)) and isinstance(b.get(metric), (int, float))]
            metrics[metric] = {"samples": len(measured),
                               "before": statistics.mean(a for a, _ in measured) if measured else None,
                               "after": statistics.mean(b for _, b in measured) if measured else None}
        return metrics

    non_profile = [(a, b) for a, b in pairs if a["category"] != "profile"]
    return {"paired_attempts": len(pairs), "improved": improved, "regressed": regressed,
            "paired_delta": (improved - regressed) / len(pairs) if pairs else None,
            "before": summarize(before), "after": summarize(after), "paired_metrics": metrics_for(pairs),
            "common_success_metrics": metrics_for([(a, b) for a, b in pairs if a["passed"] and b["passed"]]),
            "non_profile": {"samples": len(non_profile),
                            "before_passed": sum(a["passed"] for a, _ in non_profile),
                            "after_passed": sum(b["passed"] for _, b in non_profile),
                            "metrics": metrics_for(non_profile)}}


def contract_probe() -> list[dict]:
    """Observe actual outgoing requests through the public Agent.run boundary."""
    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.core.capabilities import ModelCapabilities, SessionCapabilityTracker
    from agentnexus.skills.workflow import Workflow
    from agentnexus.tools.registry import ToolMeta, ToolRegistry

    class ScriptedLLM:
        def __init__(self, thinking):
            self.model = "contract-fixture"
            self.capabilities = ModelCapabilities(supports_tool_calling=True, supports_thinking=thinking)
            self.session_tracker = SessionCapabilityTracker()
            self.total_usage = {}
            self.last_usage = {}
            self.last_error = ""
            self.last_truncated = False
            self.last_reasoning_content = ""
            self.last_tool_calls = []
            self.requests = []

        def think(self, **kwargs):
            self.requests.append(json.loads(json.dumps(kwargs["messages"])))
            self.last_tool_calls = ([{"id": "probe-1", "name": "inspect", "arguments": {}}]
                                    if len(self.requests) == 1 else [])
            return "检查状态。" if self.last_tool_calls else "完成。"

    rows = []
    for thinking in (False, True):
        llm = ScriptedLLM(thinking)
        registry = ToolRegistry()
        registry.register(ToolMeta("inspect", "读取测试状态", {"type": "object", "properties": {}}, read_only=True),
                          lambda: "ok")
        agent = ReActAgent(llm, registry, max_steps=3, output=lambda *_: None)
        agent.run("读取状态后回答")
        rows.append({"case_id": f"neutral-observation-{thinking}", "passed": not any(
            "请先用 Thought" in str(message.get("content", ""))
            for request in llm.requests[1:] for message in request), "requests": llm.requests})
        profile = Workflow.model_validate({
            "id": "contract", "version": "1", "display_name": "contract",
            "prompt_profile": {"system": "probe_custom"}, "tool_policy": {},
            "steps": [], "success_criteria": [],
        }).to_session_profile()
        with patch("agentnexus.skills.profile.load_prompt", return_value="CUSTOM-CONTRACT-9127"):
            agent.set_session_profile(profile)
        llm.requests.clear()
        agent.run("读取状态后回答")
        rows.append({"case_id": f"custom-profile-{thinking}",
                     "passed": llm.requests[0][0]["content"] == "CUSTOM-CONTRACT-9127",
                     "requests": llm.requests})
        registry.close(wait=True)
    return rows


def run_live(case: dict, llm, directory: Path, max_steps: int) -> dict:
    """Run the production agent against disposable tools and inspect actual artifacts."""
    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.memory.todo import SessionTodoList
    from agentnexus.skills.workflow import Workflow
    from agentnexus.tools.providers.base import ToolProviderContext
    from agentnexus.tools.providers.todo_provider import TodoToolProvider
    from agentnexus.tools.registry import ToolMeta, ToolRegistry

    registry = ToolRegistry()
    todo = SessionTodoList()
    TodoToolProvider().register(registry, ToolProviderContext(todo_list=todo))
    workspace = RepairWorkspace(directory, case["kind"]) if "kind" in case else None
    lookups = []

    def lookup_release(key: str, source: str = "primary") -> dict:
        lookups.append({"key": key, "source": source})
        if key != case.get("key"):
            return {"error": "release not found"}
        if source == "primary" and case["category"] == "recovery":
            return {"error": "primary unavailable; retrying primary will not help; mirror is available"}
        return {"key": key, "checksum": case["expected"], "source": source}

    def register(name, description, properties, required, fn, read_only=True):
        registry.register(ToolMeta(name, description, {"type": "object", "properties": properties,
                                                       "required": required}, read_only=read_only), fn)

    if workspace:
        register("file_read", "读取 solution.py 的当前内容。", {}, [], workspace.read_file)
        register("file_edit", "在 solution.py 中进行唯一匹配的文本替换。",
                 {"old": {"type": "string"}, "new": {"type": "string"}}, ["old", "new"],
                 workspace.edit_file, False)
        register("run_checks", "运行 solution.py 的独立验收用例，返回通过状态和错误。", {}, [], workspace.run_checks)
    else:
        register("lookup_release", "查询发布构建校验码，支持 primary 主源和 mirror 备用源。",
                 {"key": {"type": "string"}, "source": {"type": "string", "enum": ["primary", "mirror"]}},
                 ["key"], lookup_release)
    agent = ReActAgent(llm, registry, max_steps=max_steps, output=lambda *_: None, confirm_fn=lambda *_: True)
    agent._todo_list = todo
    if case["category"] == "profile":
        profile = Workflow.model_validate({
            "id": "benchmark-profile", "version": "1", "display_name": "Benchmark",
            "prompt_profile": {"system": "benchmark_custom"}, "tool_policy": {}, "steps": [],
            "success_criteria": [],
        }).to_session_profile()
        with patch("agentnexus.skills.profile.load_prompt",
                   return_value=f"这是固定输出工作流。只回复 {case['expected']}，不要调用工具。"):
            agent.set_session_profile(profile)
    started = time.perf_counter()
    try:
        result = agent.run(case["question"])
        answer = result.answer or ""
        grade = workspace.grade() if workspace else {"passed": (
            answer.strip() == case["expected"] if case["category"] == "profile" else
            case["expected"] in answer and any(x["key"] == case["key"]
                                               and (case["category"] != "recovery" or x["source"] == "mirror")
                                               for x in lookups))}
        return grade_delivery({**grade, "answer": answer, "lookups": lookups,
                "steps": [{**asdict(step), "strategy_used": step.strategy_used.name} for step in result.steps],
                "wall_s": time.perf_counter() - started,
                "tool_calls": sum(len(step.tool_calls) for step in result.steps)})
    except Exception as exc:
        grade = workspace.grade() if workspace else {"artifact_passed": False}
        return {**grade, "artifact_passed": grade.get("passed", False), "passed": False,
                "answer": "", "agent_error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(),
                "steps": [], "wall_s": time.perf_counter() - started,
                "tool_calls": len(registry.get_audit_log()), "lookups": lookups}
    finally:
        registry.close(wait=True)


def main() -> None:
    """Run offline contracts, a controlled live phase, or a complete paired comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["contracts", "run", "compare"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--model", default="agnes/agnes-3.0-flash")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--rpm", type=float, default=6)
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--source-snapshot", type=Path)
    parser.add_argument("--frozen-evaluator", action="store_true")
    parser.add_argument("--resume-instrumentation", action="store_true")
    parser.add_argument("--protocol", choices=["native", "prompt_json"], default="native")
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.frozen_evaluator and not args.source_snapshot:
        parser.error("frozen-evaluator requires source-snapshot")
    if args.source_snapshot:
        snapshot = args.source_snapshot.resolve()
        if args.phase != "run" or not all((snapshot / name).is_file() for name in SOURCE_PATHS):
            parser.error("source-snapshot must contain a complete run source snapshot")
        with tempfile.TemporaryDirectory(prefix="nexus-baseline-code-") as staging:
            staged = Path(staging)
            shutil.copytree(ROOT / "agentnexus", staged / "agentnexus", ignore=shutil.ignore_patterns("__pycache__"))
            for name in SOURCE_PATHS:
                if name != "agentnexus/evaluation/prompt_benchmark.py" or args.frozen_evaluator:
                    shutil.copyfile(snapshot / name, staged / name)
            command = [sys.executable, "-X", "utf8", "-m", "agentnexus.evaluation.prompt_benchmark", "run",
                       "--output", str(args.output), "--model", args.model, "--repeats", str(args.repeats),
                       "--rpm", str(args.rpm), "--max-steps", str(args.max_steps), "--limit", str(args.limit),
                       "--protocol", args.protocol]
            if args.resume_instrumentation:
                command.append("--resume-instrumentation")
            environment = {**os.environ, "PYTHONPATH": str(staged),
                           "AGENTNEXUS_BENCH_REVISION": subprocess.check_output(
                               ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
            subprocess.run(command, cwd=staged, env=environment, check=True)
        return
    if args.phase == "contracts":
        rows = contract_probe()
        write_json(args.output, {"passed": sum(r["passed"] for r in rows), "total": len(rows), "rows": rows})
        print(f"contracts {sum(r['passed'] for r in rows)}/{len(rows)}", flush=True)
        return
    if args.phase == "compare":
        def load(path):
            return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((path / "attempts").glob("*.json"))]
        manifests = [json.loads((p / "manifest.json").read_text(encoding="utf-8"))["experiment"]
                     for p in (args.before, args.after)]
        controls = ("model", "repeats", "rpm", "max_steps", "dataset_sha256", "temperature", "protocol",
                    "thinking_effort", "max_output_tokens")
        if any(manifests[0][key] != manifests[1][key] for key in controls):
            raise ValueError("experiment controls differ; comparison is invalid")
        allowed_changes = {"agentnexus/agents/re_act_agent.py", "agentnexus/prompts/react.txt",
                           "agentnexus/prompts/react_think.txt", "agentnexus/prompts/fragments/autonomy.txt",
                           "agentnexus/evaluation/prompt_benchmark.py"}
        old_hashes, new_hashes = (m["runtime_sha256"] for m in manifests)
        changed = {key for key in old_hashes.keys() | new_hashes.keys()
                   if old_hashes.get(key) != new_hashes.get(key)}
        if changed - allowed_changes:
            raise ValueError(f"unrelated runtime drift: {sorted(changed - allowed_changes)}")
        before, after = load(args.before), load(args.after)
        planned = {(case["id"], repeat) for case in manifests[0]["dataset"]
                   for repeat in range(manifests[0]["repeats"])}
        if any({(r["case_id"], r["repeat"]) for r in rows} != planned for rows in (before, after)):
            raise ValueError("incomplete phase; finish every planned attempt before comparing")
        result = compare_runs(before, after)
        result["scorer_sha256"] = digest(Path(__file__).read_text(encoding="utf-8"))
        result["changed_runtime_files"] = sorted(changed)
        write_json(args.output, result)
        return
    if args.repeats < 1 or args.max_steps < 1 or args.rpm <= 0 or args.limit < 0:
        parser.error("repeats, max-steps, rpm must be positive; limit must be nonnegative")
    from agentnexus.core.config import Settings, get_settings
    from agentnexus.core.llm import AgentLLM
    from agentnexus.eval.benchmarks.ratelimit import RateLimiter

    found = get_settings().find_model(args.model)
    if found is None or not args.model.startswith("agnes/"):
        parser.error("select a configured agnes/model")
    provider, entry = found
    selected = cases()[:args.limit or None]
    sources = {name: (ROOT / name).read_text(encoding="utf-8") for name in SOURCE_PATHS}
    runtime_hashes = {
        path.relative_to(ROOT).as_posix(): digest(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "agentnexus").rglob("*"))
        if path.is_file() and path.suffix in {".py", ".txt", ".yaml", ".json", ".tcss"}
    }
    manifest = {"model": args.model, "repeats": args.repeats, "rpm": args.rpm, "max_steps": args.max_steps,
                "dataset": selected, "dataset_sha256": digest(json.dumps(selected, ensure_ascii=False, sort_keys=True)),
                "source_sha256": {name: digest(text) for name, text in sources.items()},
                "python": sys.version, "platform": platform.platform(), "temperature": 0,
                "protocol": args.protocol, "thinking_effort": "none", "max_output_tokens": 2048,
                "runtime_sha256": runtime_hashes}
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "manifest.json"
    if manifest_path.exists():
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))["experiment"]
        if stored != manifest:
            recorder = "agentnexus/evaluation/prompt_benchmark.py"

            def without_recorder(value):
                return {**value, "source_sha256": {k: v for k, v in value["source_sha256"].items() if k != recorder},
                        "runtime_sha256": {k: v for k, v in value["runtime_sha256"].items() if k != recorder}}

            if not args.resume_instrumentation or without_recorder(stored) != without_recorder(manifest):
                raise ValueError("manifest changed; use a new output directory")
            write_json(args.output / "recorders" / f"{digest(sources[recorder])}.json", manifest)
            (args.output / "recorders" / f"{digest(sources[recorder])}.py").write_text(sources[recorder], encoding="utf-8")
    else:
        revision = os.environ.get("AGENTNEXUS_BENCH_REVISION") or subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        write_json(manifest_path, {"created_at": datetime.now(timezone.utc).isoformat(), "revision": revision,
                                   "experiment": manifest})
        for name, text in sources.items():
            target = args.output / "sources" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    limiter = RateLimiter(args.rpm)

    class RecordedLLM(AgentLLM):
        def __init__(self):
            super().__init__(model=entry.model_id, api_key=provider.api_key.get_secret_value(),
                             base_url=provider.base_url, timeout=45)
            from agentnexus.core.capabilities import ModelCapabilities
            self._capabilities = ModelCapabilities(supports_tool_calling=args.protocol == "native",
                                                  supports_thinking=False, from_default_fallback=False,
                                                  max_output_tokens=2048, thinking_effort="none")
            self.requests = []

        def think(self, *a, **kw):
            kw["max_attempts"] = 1
            return super().think(*a, **kw)

        def _call_via_provider(self, provider_impl, messages, *a, **kw):
            limiter.acquire()
            row = {"messages": json.loads(json.dumps(messages)), "error": "", "model": self.model,
                   "temperature": a[0], "tools": a[1], "response_format": a[2], "thinking": a[3],
                   "effort": kw.get("effort"), "max_output_tokens": self.capabilities.max_output_tokens}
            started = time.perf_counter()
            try:
                result = super()._call_via_provider(provider_impl, messages, *a, **kw)
                row.update({"text": result.text, "usage": result.usage})
                return result
            except Exception as exc:
                row["error"] = str(exc).replace(self.api_key, "[REDACTED]") if self.api_key else str(exc)
                raise
            finally:
                row["api_s"] = time.perf_counter() - started
                self.requests.append(row)

    attempts_dir = args.output / "attempts"
    attempts_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nexus-prompts-") as temporary:
        sandbox = Path(temporary)
        settings = Settings(llm_providers=[], memory_db_path=str(sandbox / "memory.db"),
                            traces_dir=str(sandbox / "traces"), persona={}, append_system_prompt="")
        previous_cwd = Path.cwd()
        os.chdir(sandbox)
        try:
            with patch("agentnexus.core.config._settings_cache", settings), patch(
                "agentnexus.agents.re_act_agent.load_project_instructions", return_value=""
            ):
                for repeat in range(args.repeats):
                    for case in selected:
                        target = attempts_dir / f"{repeat:02d}-{case['id']}.json"
                        if target.exists():
                            continue
                        llm = RecordedLLM()
                        directory = sandbox / f"{repeat}-{case['id']}"
                        directory.mkdir()
                        row = run_live(case, llm, directory, args.max_steps)
                        errors = [r["error"] for r in llm.requests if r["error"]]
                        row.update({"case_id": case["id"], "category": case["category"], "repeat": repeat,
                                    "api_error": "\n".join(errors), "llm_calls": len(llm.requests),
                                    "api_s": sum(r["api_s"] for r in llm.requests),
                                    "requests": llm.requests, **llm.total_usage})
                        write_json(target, row)
                        print(f"{repeat + 1}/{args.repeats} {case['id']} passed={row['passed']} api_errors={len(errors)}", flush=True)
                        if errors:
                            rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(attempts_dir.glob("*.json"))]
                            write_json(args.output / "summary.json", summarize(rows))
                            print("API failure saved; stopping without scoring this as a behavioral failure.", flush=True)
                            return
        finally:
            os.chdir(previous_cwd)
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(attempts_dir.glob("*.json"))]
    write_json(args.output / "summary.json", summarize(rows))


if __name__ == "__main__":
    main()
