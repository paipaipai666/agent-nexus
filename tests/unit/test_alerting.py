"""Tests for agentnexus.observability.alerting."""
import time
from unittest.mock import MagicMock

from agentnexus.observability.alerting import (
    Alert,
    AlertChannel,
    AlertManager,
    AlertRule,
    AlertSeverity,
    AlertType,
    ConsoleAlertChannel,
    CostExceedRule,
    DriftCriticalRule,
    LogAlertChannel,
    TaskSuccessRateRule,
    ToolFailureSpikeRule,
    get_alert_manager,
    setup_default_alerts,
)

# ── Alert dataclass ──────────────────────────────────────────────


class TestAlert:
    def test_default_timestamp_is_set(self):
        # Arrange & Act
        alert = Alert(
            alert_type=AlertType.COST_EXCEED,
            severity=AlertSeverity.WARNING,
            message="test",
        )
        # Assert
        assert alert.timestamp > 0
        assert alert.details == {}
        assert alert.trace_id == ""

    def test_stores_all_fields(self):
        # Arrange & Act
        alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.CRITICAL,
            message="drift detected",
            details={"score": 0.1},
            timestamp=12345.0,
            trace_id="abc",
        )
        # Assert
        assert alert.alert_type == AlertType.DRIFT
        assert alert.severity == AlertSeverity.CRITICAL
        assert alert.message == "drift detected"
        assert alert.details == {"score": 0.1}
        assert alert.timestamp == 12345.0
        assert alert.trace_id == "abc"


# ── CostExceedRule ───────────────────────────────────────────────


class TestCostExceedRule:
    def test_triggers_alert_when_cost_exceeds_threshold(self):
        # Arrange
        rule = CostExceedRule(threshold_cny=10.0)
        metrics = {"total_cost_cny": 15.0}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is not None
        assert alert.alert_type == AlertType.COST_EXCEED
        assert alert.severity == AlertSeverity.WARNING
        assert "15" in alert.message

    def test_no_alert_when_cost_below_threshold(self):
        # Arrange
        rule = CostExceedRule(threshold_cny=10.0)
        metrics = {"total_cost_cny": 5.0}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is None

    def test_no_alert_when_cost_at_threshold(self):
        # Arrange — boundary: cost == threshold should NOT trigger (strict >)
        rule = CostExceedRule(threshold_cny=10.0)
        metrics = {"total_cost_cny": 10.0}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is None

    def test_no_alert_when_cost_metric_missing(self):
        # Arrange
        rule = CostExceedRule(threshold_cny=10.0)
        metrics = {}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is None


# ── ToolFailureSpikeRule ─────────────────────────────────────────


class TestToolFailureSpikeRule:
    def test_triggers_when_failure_rate_high_and_enough_calls(self):
        # Arrange
        rule = ToolFailureSpikeRule(threshold=0.3)
        metrics = {"tool_failure_rate": 0.5, "tool_total_count": 10}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is not None
        assert alert.alert_type == AlertType.TOOL_FAILURE_SPIKE

    def test_no_alert_when_failure_rate_below_threshold(self):
        # Arrange
        rule = ToolFailureSpikeRule(threshold=0.3)
        metrics = {"tool_failure_rate": 0.2, "tool_total_count": 10}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is None

    def test_no_alert_when_total_calls_below_minimum(self):
        # Arrange — even with high failure rate, need >= 5 calls
        rule = ToolFailureSpikeRule(threshold=0.3)
        metrics = {"tool_failure_rate": 0.9, "tool_total_count": 3}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is None

    def test_boundary_exactly_five_calls_and_rate_above_threshold(self):
        # Arrange
        rule = ToolFailureSpikeRule(threshold=0.3)
        metrics = {"tool_failure_rate": 0.31, "tool_total_count": 5}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is not None


# ── TaskSuccessRateRule ──────────────────────────────────────────


class TestTaskSuccessRateRule:
    def test_triggers_when_success_rate_low_and_enough_tasks(self):
        # Arrange
        rule = TaskSuccessRateRule(threshold=0.7)
        metrics = {"task_success_rate": 0.5, "total_tasks": 5}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is not None
        assert alert.alert_type == AlertType.CONSECUTIVE_FAILURE
        assert alert.severity == AlertSeverity.CRITICAL

    def test_no_alert_when_success_rate_above_threshold(self):
        # Arrange
        rule = TaskSuccessRateRule(threshold=0.7)
        metrics = {"task_success_rate": 0.9, "total_tasks": 10}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is None

    def test_no_alert_when_total_tasks_below_minimum(self):
        # Arrange
        rule = TaskSuccessRateRule(threshold=0.7)
        metrics = {"task_success_rate": 0.1, "total_tasks": 2}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is None

    def test_boundary_exactly_at_threshold_does_not_trigger(self):
        # Arrange — success_rate == threshold should NOT trigger (strict <)
        rule = TaskSuccessRateRule(threshold=0.7)
        metrics = {"task_success_rate": 0.7, "total_tasks": 5}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is None


# ── DriftCriticalRule ────────────────────────────────────────────


class TestDriftCriticalRule:
    def test_triggers_when_critical_drift_count_positive(self):
        # Arrange
        rule = DriftCriticalRule()
        metrics = {"drift_critical_count": 2}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is not None
        assert alert.alert_type == AlertType.DRIFT
        assert alert.severity == AlertSeverity.CRITICAL

    def test_no_alert_when_critical_drift_count_zero(self):
        # Arrange
        rule = DriftCriticalRule()
        metrics = {"drift_critical_count": 0}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is None

    def test_no_alert_when_metric_missing(self):
        # Arrange
        rule = DriftCriticalRule()
        metrics = {}
        # Act
        alert = rule.check(metrics)
        # Assert
        assert alert is None


# ── LogAlertChannel ──────────────────────────────────────────────


class TestLogAlertChannel:
    def test_logs_critical_alert_at_error_level(self, caplog):
        # Arrange
        channel = LogAlertChannel()
        alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.CRITICAL,
            message="critical drift",
        )
        # Act
        with caplog.at_level("ERROR", logger="agentnexus.observability.alerting"):
            channel.send(alert)
        # Assert
        assert any("CRITICAL" in r.message for r in caplog.records)

    def test_logs_warning_alert_at_warning_level(self, caplog):
        # Arrange
        channel = LogAlertChannel()
        alert = Alert(
            alert_type=AlertType.COST_EXCEED,
            severity=AlertSeverity.WARNING,
            message="cost exceeded",
        )
        # Act
        with caplog.at_level("WARNING", logger="agentnexus.observability.alerting"):
            channel.send(alert)
        # Assert
        assert any("WARNING" in r.message for r in caplog.records)

    def test_persists_to_jsonl_file(self, tmp_path):
        # Arrange
        channel = LogAlertChannel(log_dir=str(tmp_path))
        alert = Alert(
            alert_type=AlertType.COST_EXCEED,
            severity=AlertSeverity.WARNING,
            message="test alert",
            details={"cost": 15.0},
            timestamp=1000.0,
            trace_id="t1",
        )
        # Act
        channel.send(alert)
        # Assert
        date_str = time.strftime("%Y-%m-%d")
        alert_file = tmp_path / f"alerts_{date_str}.jsonl"
        assert alert_file.exists()
        import json

        records = [json.loads(line) for line in alert_file.read_text().strip().split("\n")]
        assert len(records) == 1
        assert records[0]["alert_type"] == "cost_exceed"
        assert records[0]["severity"] == "warning"
        assert records[0]["trace_id"] == "t1"


# ── AlertManager ─────────────────────────────────────────────────


class TestAlertManager:
    def test_evaluate_triggers_matching_rules(self):
        # Arrange
        manager = AlertManager()
        mock_rule = MagicMock(spec=AlertRule)
        mock_rule.check.return_value = Alert(
            alert_type=AlertType.COST_EXCEED,
            severity=AlertSeverity.WARNING,
            message="over budget",
        )
        manager.add_rule(mock_rule)
        # Act
        alerts = manager.evaluate({"total_cost_cny": 20.0})
        # Assert
        assert len(alerts) == 1
        mock_rule.check.assert_called_once_with({"total_cost_cny": 20.0})

    def test_evaluate_returns_empty_when_no_rules_trigger(self):
        # Arrange
        manager = AlertManager()
        mock_rule = MagicMock(spec=AlertRule)
        mock_rule.check.return_value = None
        manager.add_rule(mock_rule)
        # Act
        alerts = manager.evaluate({"total_cost_cny": 1.0})
        # Assert
        assert alerts == []

    def test_emit_sends_to_all_channels(self):
        # Arrange
        manager = AlertManager()
        ch1 = MagicMock(spec=AlertChannel)
        ch2 = MagicMock(spec=AlertChannel)
        manager.add_channel(ch1)
        manager.add_channel(ch2)
        alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.CRITICAL,
            message="test",
        )
        # Act
        manager.emit(alert)
        # Assert
        ch1.send.assert_called_once_with(alert)
        ch2.send.assert_called_once_with(alert)

    def test_emit_records_in_history(self):
        # Arrange
        manager = AlertManager()
        alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.CRITICAL,
            message="test",
        )
        # Act
        manager.emit(alert)
        # Assert
        assert len(manager.history) == 1
        assert manager.history[0] is alert

    def test_history_is_copy_not_reference(self):
        # Arrange
        manager = AlertManager()
        alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.CRITICAL,
            message="test",
        )
        manager.emit(alert)
        # Act
        h1 = manager.history
        h2 = manager.history
        # Assert
        assert h1 is not h2
        assert h1 == h2

    def test_history_trims_to_max(self):
        # Arrange
        manager = AlertManager()
        manager._max_history = 5
        for i in range(7):
            manager.emit(Alert(
                alert_type=AlertType.DRIFT,
                severity=AlertSeverity.INFO,
                message=f"alert-{i}",
                timestamp=float(i),
            ))
        # Act & Assert
        assert len(manager.history) == 5
        assert manager.history[0].message == "alert-2"
        assert manager.history[-1].message == "alert-6"

    def test_get_history_filters_by_days(self):
        # Arrange
        manager = AlertManager()
        old_alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.WARNING,
            message="old",
            timestamp=time.time() - 10 * 86400,
        )
        new_alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.WARNING,
            message="new",
            timestamp=time.time(),
        )
        manager._history = [old_alert, new_alert]
        # Act
        result = manager.get_history(days=7)
        # Assert
        assert len(result) == 1
        assert result[0].message == "new"

    def test_get_history_filters_by_severity(self):
        # Arrange
        manager = AlertManager()
        manager._history = [
            Alert(
                alert_type=AlertType.DRIFT,
                severity=AlertSeverity.WARNING,
                message="warn",
                timestamp=time.time(),
            ),
            Alert(
                alert_type=AlertType.DRIFT,
                severity=AlertSeverity.CRITICAL,
                message="crit",
                timestamp=time.time(),
            ),
        ]
        # Act
        result = manager.get_history(severity="critical")
        # Assert
        assert len(result) == 1
        assert result[0].message == "crit"

    def test_evaluate_continues_on_rule_exception(self):
        # Arrange
        manager = AlertManager()
        bad_rule = MagicMock(spec=AlertRule)
        bad_rule.check.side_effect = RuntimeError("boom")
        good_rule = MagicMock(spec=AlertRule)
        good_rule.check.return_value = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.WARNING,
            message="ok",
        )
        manager.add_rule(bad_rule)
        manager.add_rule(good_rule)
        # Act
        alerts = manager.evaluate({})
        # Assert
        assert len(alerts) == 1

    def test_emit_continues_on_channel_exception(self):
        # Arrange
        manager = AlertManager()
        bad_channel = MagicMock(spec=AlertChannel)
        bad_channel.send.side_effect = RuntimeError("channel down")
        good_channel = MagicMock(spec=AlertChannel)
        manager.add_channel(bad_channel)
        manager.add_channel(good_channel)
        alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.WARNING,
            message="test",
        )
        # Act
        manager.emit(alert)
        # Assert
        good_channel.send.assert_called_once_with(alert)


# ── ConsoleAlertChannel ──────────────────────────────────────────


class TestConsoleAlertChannel:
    def test_falls_back_to_print_when_rich_unavailable(self, monkeypatch):
        # Arrange
        channel = ConsoleAlertChannel()
        alert = Alert(
            alert_type=AlertType.COST_EXCEED,
            severity=AlertSeverity.WARNING,
            message="cost exceeded",
        )
        printed = []
        monkeypatch.setattr("builtins.print", lambda msg: printed.append(msg))
        # Mock rich import to fail
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "rich.console":
                raise ImportError("no rich")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        # Act
        channel.send(alert)
        # Assert
        assert len(printed) == 1
        assert "WARNING" in printed[0]
        assert "cost exceeded" in printed[0]


# ── setup_default_alerts ─────────────────────────────────────────


class TestSetupDefaultAlerts:
    def test_registers_default_rules_and_channels(self, monkeypatch):
        # Arrange — reset global singleton
        import agentnexus.observability.alerting as mod
        monkeypatch.setattr(mod, "_global_alert_manager", None)
        # Act
        manager = setup_default_alerts()
        # Assert
        assert len(manager._rules) == 4
        assert len(manager._channels) >= 1  # at least ConsoleAlertChannel

    def test_adds_log_channel_when_traces_dir_provided(self, monkeypatch, tmp_path):
        # Arrange
        import agentnexus.observability.alerting as mod
        monkeypatch.setattr(mod, "_global_alert_manager", None)
        traces_dir = str(tmp_path / "traces" / "2025-01-01.jsonl")
        # Act
        manager = setup_default_alerts(traces_dir=traces_dir)
        # Assert
        assert len(manager._channels) == 2
        assert any(isinstance(ch, LogAlertChannel) for ch in manager._channels)


# ── get_alert_manager singleton ──────────────────────────────────


class TestGetAlertManager:
    def test_returns_same_instance(self, monkeypatch):
        # Arrange
        import agentnexus.observability.alerting as mod
        monkeypatch.setattr(mod, "_global_alert_manager", None)
        # Act
        m1 = get_alert_manager()
        m2 = get_alert_manager()
        # Assert
        assert m1 is m2
