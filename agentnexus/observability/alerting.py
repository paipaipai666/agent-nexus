"""告警管道 — 统一告警管理器、规则引擎和通知通道

基于文章《Harness的可观测性》第九章第四层：可视化与告警层。
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    DRIFT = "drift"                      # 漂移检测
    COST_EXCEED = "cost_exceed"          # 成本超限
    TOOL_FAILURE_SPIKE = "tool_failure_spike"  # 工具失败率飙升
    SLOW_HOOK = "slow_hook"              # Hook 执行过慢
    MCP_DEGRADED = "mcp_degraded"        # MCP 服务器降级
    CONSECUTIVE_FAILURE = "consecutive_failure"  # 连续任务失败
    HUMAN_TAKEOVER = "human_takeover"    # 人工接管


@dataclass
class Alert:
    """一条告警"""
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    trace_id: str = ""


# ── 告警规则接口 ──────────────────────────────────────────────────

class AlertRule(ABC):
    """告警规则基类"""

    @abstractmethod
    def check(self, metrics: dict[str, Any]) -> Alert | None:
        """检查指标，返回告警或 None"""
        ...


# ── 告警通道接口 ──────────────────────────────────────────────────

class AlertChannel(ABC):
    """告警通知通道基类"""

    @abstractmethod
    def send(self, alert: Alert) -> None:
        """发送告警通知"""
        ...


# ── 内置规则 ──────────────────────────────────────────────────────

class CostExceedRule(AlertRule):
    """Token 成本超过阈值"""

    def __init__(self, threshold_cny: float = 10.0):
        self.threshold_cny = threshold_cny

    def check(self, metrics: dict[str, Any]) -> Alert | None:
        cost = metrics.get("total_cost_cny", 0)
        if cost > self.threshold_cny:
            return Alert(
                alert_type=AlertType.COST_EXCEED,
                severity=AlertSeverity.WARNING,
                message=f"Token 成本 {cost:.4f} CNY 超过阈值 {self.threshold_cny} CNY",
                details={"cost": cost, "threshold": self.threshold_cny},
            )
        return None


class ToolFailureSpikeRule(AlertRule):
    """工具失败率超过阈值"""

    def __init__(self, threshold: float = 0.3):
        self.threshold = threshold

    def check(self, metrics: dict[str, Any]) -> Alert | None:
        failure_rate = metrics.get("tool_failure_rate", 0)
        total = metrics.get("tool_total_count", 0)
        if total >= 5 and failure_rate > self.threshold:
            return Alert(
                alert_type=AlertType.TOOL_FAILURE_SPIKE,
                severity=AlertSeverity.WARNING,
                message=f"工具失败率 {failure_rate:.1%} 超过阈值 {self.threshold:.0%} ({total} 次调用)",
                details={"failure_rate": failure_rate, "total_calls": total},
            )
        return None


class TaskSuccessRateRule(AlertRule):
    """任务成功率低于阈值"""

    def __init__(self, threshold: float = 0.7):
        self.threshold = threshold

    def check(self, metrics: dict[str, Any]) -> Alert | None:
        success_rate = metrics.get("task_success_rate", 1.0)
        total = metrics.get("total_tasks", 0)
        if total >= 3 and success_rate < self.threshold:
            return Alert(
                alert_type=AlertType.CONSECUTIVE_FAILURE,
                severity=AlertSeverity.CRITICAL,
                message=f"任务成功率 {success_rate:.1%} 低于阈值 {self.threshold:.0%} ({total} 个任务)",
                details={"success_rate": success_rate, "total_tasks": total},
            )
        return None


class DriftCriticalRule(AlertRule):
    """检测到 critical 漂移信号"""

    def check(self, metrics: dict[str, Any]) -> Alert | None:
        drift_critical = metrics.get("drift_critical_count", 0)
        if drift_critical > 0:
            return Alert(
                alert_type=AlertType.DRIFT,
                severity=AlertSeverity.CRITICAL,
                message=f"检测到 {drift_critical} 个 critical 漂移信号",
                details={"critical_count": drift_critical},
            )
        return None


# ── 内置通道 ──────────────────────────────────────────────────────

class LogAlertChannel(AlertChannel):
    """写入日志文件"""

    def __init__(self, log_dir: str | None = None):
        self._log_dir = log_dir

    def send(self, alert: Alert) -> None:
        log_msg = f"[ALERT] [{alert.severity.value.upper()}] {alert.alert_type.value}: {alert.message}"
        if alert.severity == AlertSeverity.CRITICAL:
            logger.error(log_msg)
        elif alert.severity == AlertSeverity.WARNING:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        if self._log_dir:
            try:
                path = Path(self._log_dir)
                path.mkdir(parents=True, exist_ok=True)
                date_str = time.strftime("%Y-%m-%d")
                alert_file = path / f"alerts_{date_str}.jsonl"
                record = {
                    "alert_type": alert.alert_type.value,
                    "severity": alert.severity.value,
                    "message": alert.message,
                    "details": alert.details,
                    "timestamp": alert.timestamp,
                    "trace_id": alert.trace_id,
                }
                with open(alert_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as e:
                logger.debug("Alert log persistence failed (non-fatal): %s", e)


class ConsoleAlertChannel(AlertChannel):
    """打印到控制台（CLI 模式）"""

    def send(self, alert: Alert) -> None:
        severity_colors = {
            AlertSeverity.INFO: "blue",
            AlertSeverity.WARNING: "yellow",
            AlertSeverity.CRITICAL: "red bold",
        }
        color = severity_colors.get(alert.severity, "white")
        try:
            from rich.console import Console
            console = Console()
            console.print(f"[{color}][ALERT] {alert.severity.value.upper()}: {alert.message}[/{color}]")
        except Exception:
            print(f"[ALERT] {alert.severity.value.upper()}: {alert.message}")


# ── 告警管理器 ────────────────────────────────────────────────────

class AlertManager:
    """统一告警管理器"""

    def __init__(self):
        self._rules: list[AlertRule] = []
        self._channels: list[AlertChannel] = []
        self._history: list[Alert] = []
        self._max_history = 1000

    def add_rule(self, rule: AlertRule) -> None:
        self._rules.append(rule)

    def add_channel(self, channel: AlertChannel) -> None:
        self._channels.append(channel)

    def evaluate(self, metrics: dict[str, Any]) -> list[Alert]:
        """评估所有规则，返回触发的告警列表"""
        alerts: list[Alert] = []
        for rule in self._rules:
            try:
                alert = rule.check(metrics)
                if alert:
                    alerts.append(alert)
                    self.emit(alert)
            except Exception as e:
                logger.debug("Alert rule evaluation failed: %s", e)
        return alerts

    def emit(self, alert: Alert) -> None:
        """发送一条告警到所有通道"""
        self._history.append(alert)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        for channel in self._channels:
            try:
                channel.send(alert)
            except Exception as e:
                logger.debug("Alert channel send failed: %s", e)

    @property
    def history(self) -> list[Alert]:
        return list(self._history)

    def get_history(self, days: int = 7, severity: str | None = None) -> list[Alert]:
        """获取告警历史"""
        cutoff = time.time() - days * 86400
        result = [a for a in self._history if a.timestamp >= cutoff]
        if severity:
            result = [a for a in result if a.severity.value == severity]
        return result


# ── 全局实例 ──────────────────────────────────────────────────────

_global_alert_manager: AlertManager | None = None


def get_alert_manager() -> AlertManager:
    """返回全局告警管理器单例"""
    global _global_alert_manager
    if _global_alert_manager is None:
        _global_alert_manager = AlertManager()
    return _global_alert_manager


def setup_default_alerts(traces_dir: str | None = None) -> AlertManager:
    """设置默认的告警规则和通道"""
    manager = get_alert_manager()

    # 添加默认规则
    manager.add_rule(CostExceedRule(threshold_cny=10.0))
    manager.add_rule(ToolFailureSpikeRule(threshold=0.3))
    manager.add_rule(TaskSuccessRateRule(threshold=0.7))
    manager.add_rule(DriftCriticalRule())

    # 添加默认通道
    manager.add_channel(ConsoleAlertChannel())
    if traces_dir:
        manager.add_channel(LogAlertChannel(log_dir=str(Path(traces_dir).parent / "alerts")))

    return manager
