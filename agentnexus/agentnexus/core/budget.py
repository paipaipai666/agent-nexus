"""Token Budget tracker — real-time cost-aware execution control.

4-level budget state machine:
  GREEN  (>50%) → normal execution
  YELLOW (20-50%) → compress context, skip optional LLM calls
  RED    (5-20%) → use shorter prompts, skip CoT
  BREAK  (<5%) → force early termination with partial result
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum


class BudgetState(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    BREAK = "break"


@dataclass
class BudgetTracker:
    total: int                     # initial budget in tokens
    remaining: int                 # current remaining
    by_agent: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_retry: int = 0
    consumed_input: int = 0
    consumed_output: int = 0

    @classmethod
    def from_task(cls, task: str, complexity: str = "") -> BudgetTracker:
        """Allocate budget based on task complexity.

        Uses explicit complexity from planner if available, else heuristic fallback.
        """
        base = 20000  # 20K tokens base budget
        if complexity == "complex":
            base = int(base * 1.8)
        elif complexity == "medium":
            base = int(base * 1.3)
        elif complexity == "simple":
            base = int(base * 0.8)
        else:
            # Heuristic fallback if no planner complexity available
            if len(task) > 200:
                base = int(base * 1.5)
            if "报告" in task or "分析" in task:
                base = int(base * 1.3)
        return cls(total=base, remaining=base)

    def consume(self, agent: str, input_tokens: int = 0, output_tokens: int = 0,
                is_retry: bool = False):
        """Record token consumption from an LLM call."""
        used = input_tokens + output_tokens
        self.remaining = max(0, self.remaining - used)
        self.consumed_input += input_tokens
        self.consumed_output += output_tokens
        self.by_agent[agent] += used
        if is_retry:
            self.by_retry += used

    @property
    def state(self) -> BudgetState:
        """Current budget state based on remaining percentage."""
        pct = self.remaining / self.total if self.total > 0 else 0
        if pct > 0.5:
            return BudgetState.GREEN
        if pct > 0.2:
            return BudgetState.YELLOW
        if pct > 0.05:
            return BudgetState.RED
        return BudgetState.BREAK

    @property
    def used(self) -> int:
        return self.total - self.remaining

    @property
    def used_pct(self) -> float:
        return round(self.used / self.total * 100, 1) if self.total else 0.0

    def summary(self) -> str:
        return (
            f"Budget: {self.used}/{self.total} ({self.used_pct}%) "
            f"| State: {self.state.value.upper()} "
            f"| Retry: {self.by_retry}"
        )
