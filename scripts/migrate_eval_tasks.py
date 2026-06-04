"""迁移 JSONL eval 数据集到 YAML task 格式。"""

import json
import sys
from pathlib import Path

import yaml


def categorize_by_tools(tools_used: list[str]) -> str:
    """根据使用的工具推断类别。"""
    tool_set = set(tools_used)
    if tool_set & {"write", "edit", "read"} and not tool_set & {"web_search", "search"}:
        return "coding"
    if tool_set & {"web_search", "search"}:
        return "tool_use"
    if tool_set & {"bash", "glob", "grep"}:
        return "tool_use"
    return "general"


def infer_graders(tools_used: list[str], expected_answer: str) -> list[dict]:
    """根据任务特征推断合适的 graders。"""
    graders = []
    tool_set = set(tools_used)

    # 工具使用验证
    if tools_used:
        graders.append({
            "type": "tool_calls",
            "required_tools": [{"tool": t} for t in tools_used[:2]],
            "weight": 0.3,
        })

    # LLM rubric 评估答案质量
    graders.append({
        "type": "llm_rubric",
        "name": "answer_quality",
        "assertions": [
            f"回答应包含: {expected_answer[:80]}",
        ],
        "weight": 0.5,
    })

    # 转录约束
    graders.append({
        "type": "transcript",
        "max_turns": 10,
        "weight": 0.2,
    })

    return graders


def migrate_agent_eval(jsonl_path: Path, output_dir: Path) -> int:
    """迁移 agent_eval.jsonl。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)

            task_id = data["trace_id"]
            question = data["question"]
            expected = data.get("expected_answer", "")
            tools = data.get("tools_used", [])
            category = categorize_by_tools(tools)

            task = {
                "id": task_id,
                "description": question[:80],
                "category": category,
                "difficulty": "medium",
                "eval_type": "capability",
                "tags": tools[:3],
                "input": {
                    "prompt": question,
                    "tools": tools,
                },
                "graders": infer_graders(tools, expected),
            }
            if expected:
                task["reference_solution"] = expected

            # 按类别分目录
            cat_dir = output_dir / category
            cat_dir.mkdir(parents=True, exist_ok=True)
            file_path = cat_dir / f"{task_id}.yaml"
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(task, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            count += 1

    return count


def migrate_humaneval(jsonl_path: Path, output_dir: Path) -> int:
    """迁移 humaneval.jsonl 到 coding 类别。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)

            task_id = data["trace_id"]
            question = data["question"]
            expected = data.get("expected_answer", "")
            test_cases = data.get("test_cases", [])

            # 提取函数名用于测试
            graders = []
            if test_cases:
                # 去掉 assert 前缀，保留表达式
                assertions = []
                for tc in test_cases:
                    tc = tc.strip()
                    if tc.startswith("assert "):
                        assertions.append(tc[7:])
                    else:
                        assertions.append(tc)
                graders.append({
                    "type": "code_execution",
                    "test_files": assertions,
                    "weight": 0.7,
                })

            graders.append({
                "type": "llm_rubric",
                "name": "code_quality",
                "assertions": [
                    "代码语法正确",
                    "实现逻辑清晰",
                ],
                "weight": 0.3,
            })

            task = {
                "id": task_id,
                "description": question.split("\n")[0][:80],
                "category": "coding",
                "difficulty": "medium",
                "eval_type": "capability",
                "tags": ["humaneval", "code-generation"],
                "input": {
                    "prompt": question,
                    "tools": ["code_executor"],
                },
                "graders": graders,
            }
            if expected:
                task["reference_solution"] = expected

            file_path = output_dir / f"{task_id}.yaml"
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(task, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            count += 1

    return count


def main():
    project_root = Path(__file__).parent.parent
    tasks_dir = project_root / "agentnexus" / "eval_tasks"
    evals_dir = project_root / "tests" / "evals"

    # 迁移 agent_eval.jsonl
    agent_count = migrate_agent_eval(
        evals_dir / "agent_eval.jsonl",
        tasks_dir,
    )
    print(f"Migrated {agent_count} agent eval tasks")

    # 迁移 humaneval.jsonl
    humaneval_count = migrate_humaneval(
        evals_dir / "humaneval.jsonl",
        tasks_dir / "coding",
    )
    print(f"Migrated {humaneval_count} humaneval tasks")

    # 更新所有 suite 文件
    for suite_file in tasks_dir.rglob("_suite.yaml"):
        with open(suite_file, encoding="utf-8") as f:
            suite = yaml.safe_load(f)
        category = suite_file.parent.name
        # 收集该类别下所有 task ID
        task_ids = []
        for task_file in suite_file.parent.glob("*.yaml"):
            if task_file.name.startswith("_"):
                continue
            with open(task_file, encoding="utf-8") as f:
                task_data = yaml.safe_load(f)
            task_ids.append(task_data["id"])
        suite["task_ids"] = sorted(task_ids)
        with open(suite_file, "w", encoding="utf-8") as f:
            yaml.dump(suite, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f"Updated suite {category}: {len(task_ids)} tasks")

    # 验证
    from agentnexus.evaluation.dataset import EvalDataset
    ds = EvalDataset(tasks_dir)
    errors = ds.validate()
    stats = ds.stats()
    print(f"\nValidation: {len(errors)} errors")
    print(f"Stats: {stats}")
    if errors:
        for e in errors[:5]:
            print(f"  ERROR: {e}")

    total = stats.get("total", 0)
    print(f"\nTotal: {total} tasks migrated")


if __name__ == "__main__":
    main()
