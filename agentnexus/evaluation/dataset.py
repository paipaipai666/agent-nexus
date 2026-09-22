"""评估数据集管理 — 统一的 task 数据集加载、验证、过滤。

对应 Anthropic 方法论:
  - "Write unambiguous tasks with reference solutions"
  - "Build balanced problem sets"
  - "Start with what you already test manually"
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from agentnexus.evaluation.task import (
    EvalSuite,
    EvalTask,
    load_suite_from_yaml,
    load_task_from_yaml,
)

logger = logging.getLogger(__name__)

# 默认数据集目录
DEFAULT_TASKS_DIR = Path(__file__).parent.parent / "eval_tasks"


class EvalDataset:
    """统一的评估数据集管理。"""

    def __init__(self, tasks_dir: str | Path | None = None):
        self._tasks_dir = Path(tasks_dir) if tasks_dir else DEFAULT_TASKS_DIR

    @property
    def tasks_dir(self) -> Path:
        return self._tasks_dir

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_task(self, task_id: str) -> EvalTask | None:
        """按 ID 加载单个 task。"""
        for yaml_file in self._tasks_dir.rglob("*.yaml"):
            if yaml_file.name.startswith("_"):
                continue
            try:
                task = load_task_from_yaml(yaml_file)
                if task.id == task_id:
                    return task
            except Exception:
                continue
        return None

    def load_all_tasks(self) -> list[EvalTask]:
        """加载所有 task。"""
        tasks: list[EvalTask] = []
        if not self._tasks_dir.exists():
            return tasks
        for yaml_file in sorted(self._tasks_dir.rglob("*.yaml")):
            if yaml_file.name.startswith("_"):
                continue
            try:
                task = load_task_from_yaml(yaml_file)
                tasks.append(task)
            except Exception as e:
                logger.warning("Failed to load task from %s: %s", yaml_file, e)
        return tasks

    def load_suite(self, suite_name: str) -> EvalSuite | None:
        """按名称加载 suite。"""
        suite_path = self._tasks_dir / suite_name / "_suite.yaml"
        if not suite_path.exists():
            # 尝试在根目录查找
            suite_path = self._tasks_dir / f"{suite_name}_suite.yaml"
        if not suite_path.exists():
            return None

        try:
            suite = load_suite_from_yaml(suite_path)
        except Exception as e:
            logger.error("Failed to load suite '%s': %s", suite_name, e)
            return None

        # 加载 task_ids 引用的 task
        if suite.task_ids and not suite.tasks:
            all_tasks = self.load_all_tasks()
            task_map = {t.id: t for t in all_tasks}
            suite.tasks = [task_map[tid] for tid in suite.task_ids if tid in task_map]

        return suite

    def list_suites(self) -> list[dict[str, Any]]:
        """列出所有 suite。"""
        suites: list[dict[str, Any]] = []
        if not self._tasks_dir.exists():
            return suites

        # 查找 _suite.yaml 文件
        for suite_file in sorted(self._tasks_dir.rglob("_suite.yaml")):
            try:
                suite = load_suite_from_yaml(suite_file)
                suites.append({
                    "name": suite.name,
                    "eval_type": suite.eval_type,
                    "description": suite.description,
                    "task_count": len(suite.task_ids) or len(suite.tasks),
                    "path": str(suite_file),
                })
            except Exception:
                continue

        # 查找 {name}_suite.yaml 文件
        for suite_file in sorted(self._tasks_dir.glob("*_suite.yaml")):
            try:
                suite = load_suite_from_yaml(suite_file)
                suites.append({
                    "name": suite.name,
                    "eval_type": suite.eval_type,
                    "description": suite.description,
                    "task_count": len(suite.task_ids) or len(suite.tasks),
                    "path": str(suite_file),
                })
            except Exception:
                continue

        return suites

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter_tasks(
        self,
        category: str | None = None,
        difficulty: str | None = None,
        eval_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[EvalTask]:
        """按条件过滤 task。"""
        tasks = self.load_all_tasks()
        if category:
            tasks = [t for t in tasks if t.category == category]
        if difficulty:
            tasks = [t for t in tasks if t.difficulty == difficulty]
        if eval_type:
            tasks = [t for t in tasks if t.eval_type == eval_type]
        if tags:
            tasks = [t for t in tasks if all(tag in t.tags for tag in tags)]
        return tasks

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """验证所有 task 的完整性。返回错误列表。"""
        errors: list[str] = []
        tasks = self.load_all_tasks()
        seen_ids: set[str] = set()

        for task in tasks:
            # 检查 ID 唯一性
            if task.id in seen_ids:
                errors.append(f"Duplicate task ID: {task.id}")
            seen_ids.add(task.id)

            # 检查 task 自身有效性
            task_errors = task.validate()
            errors.extend(task_errors)

        return errors

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """数据集统计信息。"""
        tasks = self.load_all_tasks()
        if not tasks:
            return {"total": 0}

        by_category: dict[str, int] = {}
        by_difficulty: dict[str, int] = {}
        by_eval_type: dict[str, int] = {}

        for task in tasks:
            by_category[task.category] = by_category.get(task.category, 0) + 1
            by_difficulty[task.difficulty] = by_difficulty.get(task.difficulty, 0) + 1
            by_eval_type[task.eval_type] = by_eval_type.get(task.eval_type, 0) + 1

        return {
            "total": len(tasks),
            "by_category": by_category,
            "by_difficulty": by_difficulty,
            "by_eval_type": by_eval_type,
        }

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_task(self, task: EvalTask, directory: str | None = None) -> Path:
        """保存 task 到 YAML 文件。"""
        save_dir = Path(directory) if directory else self._tasks_dir / task.category
        save_dir.mkdir(parents=True, exist_ok=True)

        file_path = save_dir / f"{task.id}.yaml"
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.dump(task.to_dict(), f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        return file_path

    def save_suite(self, suite: EvalSuite, directory: str | None = None) -> Path:
        """保存 suite 到 YAML 文件。"""
        save_dir = Path(directory) if directory else self._tasks_dir / suite.name
        save_dir.mkdir(parents=True, exist_ok=True)

        file_path = save_dir / "_suite.yaml"
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.dump(suite.to_dict(), f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        return file_path


