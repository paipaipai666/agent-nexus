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
    emit_drift_alerts,
    evaluate_stats,
    get_alert_manager,
    setup_default_alerts,
)


class TestAlert:
    def test_default_timestamp_is_set(self):
        alert = Alert(
            alert_type=AlertType.COST_EXCEED,
            severity=AlertSeverity.WARNING,
            message="test",
        )
        assert alert.timestamp > 0
        assert alert.details == {}
        assert alert.trace_id == ""

    def test_stores_all_fields(self):
        alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.CRITICAL,
            message="drift detected",
            details={"score": 0.1},
            timestamp=12345.0,
            trace_id="abc",
        )
        assert alert.alert_type == AlertType.DRIFT
        assert alert.severity == AlertSeverity.CRITICAL
        assert alert.message == "drift detected"
        assert alert.details == {"score": 0.1}
        assert alert.timestamp == 12345.0
        assert alert.trace_id == "abc"


class TestCostExceedRule:
    def test_triggers_alert_when_cost_exceeds_threshold(self):
        rule = CostExceedRule(threshold_cny=10.0)
        metrics = {"total_cost_cny": 15.0}
        alert = rule.check(metrics)
        assert alert is not None
        assert alert.alert_type == AlertType.COST_EXCEED
        assert alert.severity == AlertSeverity.WARNING
        assert "15" in alert.message

    def test_no_alert_when_cost_below_threshold(self):
        rule = CostExceedRule(threshold_cny=10.0)
        metrics = {"total_cost_cny": 5.0}
        alert = rule.check(metrics)
        assert alert is None

    def test_no_alert_when_cost_at_threshold(self):
        rule = CostExceedRule(threshold_cny=10.0)
        metrics = {"total_cost_cny": 10.0}
        alert = rule.check(metrics)
        assert alert is None

    def test_no_alert_when_cost_metric_missing(self):
        rule = CostExceedRule(threshold_cny=10.0)
        metrics = {}
        alert = rule.check(metrics)
        assert alert is None


class TestToolFailureSpikeRule:
    def test_triggers_when_failure_rate_high_and_enough_calls(self):
        rule = ToolFailureSpikeRule(threshold=0.3)
        metrics = {"tool_failure_rate": 0.5, "tool_total_count": 10}
        alert = rule.check(metrics)
        assert alert is not None
        assert alert.alert_type == AlertType.TOOL_FAILURE_SPIKE

    def test_no_alert_when_failure_rate_below_threshold(self):
        rule = ToolFailureSpikeRule(threshold=0.3)
        metrics = {"tool_failure_rate": 0.2, "tool_total_count": 10}
        alert = rule.check(metrics)
        assert alert is None

    def test_no_alert_when_total_calls_below_minimum(self):
        rule = ToolFailureSpikeRule(threshold=0.3)
        metrics = {"tool_failure_rate": 0.9, "tool_total_count": 3}
        alert = rule.check(metrics)
        assert alert is None

    def test_boundary_exactly_five_calls_and_rate_above_threshold(self):
        rule = ToolFailureSpikeRule(threshold=0.3)
        metrics = {"tool_failure_rate": 0.31, "tool_total_count": 5}
        alert = rule.check(metrics)
        assert alert is not None


class TestTaskSuccessRateRule:
    def test_triggers_when_success_rate_low_and_enough_tasks(self):
        rule = TaskSuccessRateRule(threshold=0.7)
        metrics = {"task_success_rate": 0.5, "total_tasks": 5}
        alert = rule.check(metrics)
        assert alert is not None
        assert alert.alert_type == AlertType.CONSECUTIVE_FAILURE
        assert alert.severity == AlertSeverity.CRITICAL

    def test_no_alert_when_success_rate_above_threshold(self):
        rule = TaskSuccessRateRule(threshold=0.7)
        metrics = {"task_success_rate": 0.9, "total_tasks": 10}
        alert = rule.check(metrics)
        assert alert is None

    def test_no_alert_when_total_tasks_below_minimum(self):
        rule = TaskSuccessRateRule(threshold=0.7)
        metrics = {"task_success_rate": 0.1, "total_tasks": 2}
        alert = rule.check(metrics)
        assert alert is None

    def test_boundary_exactly_at_threshold_does_not_trigger(self):
        rule = TaskSuccessRateRule(threshold=0.7)
        metrics = {"task_success_rate": 0.7, "total_tasks": 5}
        alert = rule.check(metrics)
        assert alert is None


class TestDriftCriticalRule:
    def test_triggers_when_critical_drift_count_positive(self):
        rule = DriftCriticalRule()
        metrics = {"drift_critical_count": 2}
        alert = rule.check(metrics)
        assert alert is not None
        assert alert.alert_type == AlertType.DRIFT
        assert alert.severity == AlertSeverity.CRITICAL

    def test_no_alert_when_critical_drift_count_zero(self):
        rule = DriftCriticalRule()
        metrics = {"drift_critical_count": 0}
        alert = rule.check(metrics)
        assert alert is None

    def test_no_alert_when_metric_missing(self):
        rule = DriftCriticalRule()
        metrics = {}
        alert = rule.check(metrics)
        assert alert is None


class TestLogAlertChannel:
    def test_logs_critical_alert_at_error_level(self, caplog):
        channel = LogAlertChannel()
        alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.CRITICAL,
            message="critical drift",
        )
        with caplog.at_level("ERROR", logger="agentnexus.observability.alerting"):
            channel.send(alert)
        assert any("CRITICAL" in r.message for r in caplog.records)

    def test_logs_warning_alert_at_warning_level(self, caplog):
        channel = LogAlertChannel()
        alert = Alert(
            alert_type=AlertType.COST_EXCEED,
            severity=AlertSeverity.WARNING,
            message="cost exceeded",
        )
        with caplog.at_level("WARNING", logger="agentnexus.observability.alerting"):
            channel.send(alert)
        assert any("WARNING" in r.message for r in caplog.records)

    def test_persists_to_jsonl_file(self, tmp_path):
        channel = LogAlertChannel(log_dir=str(tmp_path))
        alert = Alert(
            alert_type=AlertType.COST_EXCEED,
            severity=AlertSeverity.WARNING,
            message="test alert",
            details={"cost": 15.0},
            timestamp=1000.0,
            trace_id="t1",
        )
        channel.send(alert)
        date_str = time.strftime("%Y-%m-%d")
        alert_file = tmp_path / f"alerts_{date_str}.jsonl"
        assert alert_file.exists()
        import json

        records = [json.loads(line) for line in alert_file.read_text().strip().split("\n")]
        assert len(records) == 1
        assert records[0]["alert_type"] == "cost_exceed"
        assert records[0]["severity"] == "warning"
        assert records[0]["trace_id"] == "t1"


class TestAlertManager:
    def test_evaluate_triggers_matching_rules(self):
        manager = AlertManager()
        mock_rule = MagicMock(spec=AlertRule)
        mock_rule.check.return_value = Alert(
            alert_type=AlertType.COST_EXCEED,
            severity=AlertSeverity.WARNING,
            message="over budget",
        )
        manager.add_rule(mock_rule)
        alerts = manager.evaluate({"total_cost_cny": 20.0})
        assert len(alerts) == 1
        mock_rule.check.assert_called_once_with({"total_cost_cny": 20.0})

    def test_evaluate_returns_empty_when_no_rules_trigger(self):
        manager = AlertManager()
        mock_rule = MagicMock(spec=AlertRule)
        mock_rule.check.return_value = None
        manager.add_rule(mock_rule)
        alerts = manager.evaluate({"total_cost_cny": 1.0})
        assert alerts == []

    def test_emit_sends_to_all_channels(self):
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
        manager.emit(alert)
        ch1.send.assert_called_once_with(alert)
        ch2.send.assert_called_once_with(alert)

    def test_emit_records_in_history(self):
        manager = AlertManager()
        alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.CRITICAL,
            message="test",
        )
        manager.emit(alert)
        assert len(manager.history) == 1
        assert manager.history[0] is alert

    def test_history_is_copy_not_reference(self):
        manager = AlertManager()
        alert = Alert(
            alert_type=AlertType.DRIFT,
            severity=AlertSeverity.CRITICAL,
            message="test",
        )
        manager.emit(alert)
        h1 = manager.history
        h2 = manager.history
        assert h1 is not h2
        assert h1 == h2

    def test_history_trims_to_max(self):
        manager = AlertManager()
        manager._max_history = 5
        for i in range(7):
            manager.emit(Alert(
                alert_type=AlertType.DRIFT,
                severity=AlertSeverity.INFO,
                message=f"alert-{i}",
                timestamp=float(i),
            ))
        assert len(manager.history) == 5
        assert manager.history[0].message == "alert-2"
        assert manager.history[-1].message == "alert-6"

    def test_get_history_filters_by_days(self):
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
        result = manager.get_history(days=7)
        assert len(result) == 1
        assert result[0].message == "new"

    def test_get_history_filters_by_severity(self):
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
        result = manager.get_history(severity="critical")
        assert len(result) == 1
        assert result[0].message == "crit"

    def test_evaluate_continues_on_rule_exception(self):
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
        alerts = manager.evaluate({})
        assert len(alerts) == 1

    def test_emit_continues_on_channel_exception(self):
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
        manager.emit(alert)
        good_channel.send.assert_called_once_with(alert)


class TestConsoleAlertChannel:
    def test_falls_back_to_print_when_rich_unavailable(self, monkeypatch):
        channel = ConsoleAlertChannel()
        alert = Alert(
            alert_type=AlertType.COST_EXCEED,
            severity=AlertSeverity.WARNING,
            message="cost exceeded",
        )
        printed = []
        monkeypatch.setattr("builtins.print", lambda msg: printed.append(msg))
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "rich.console":
                raise ImportError("no rich")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        channel.send(alert)
        assert len(printed) == 1
        assert "WARNING" in printed[0]
        assert "cost exceeded" in printed[0]


class TestSetupDefaultAlerts:
    def test_registers_default_rules_and_channels(self, monkeypatch):
        import agentnexus.observability.alerting as mod
        monkeypatch.setattr(mod, "_global_alert_manager", None)
        manager = setup_default_alerts()
        assert len(manager._rules) == 4
        assert len(manager._channels) >= 1

    def test_adds_log_channel_when_traces_dir_provided(self, monkeypatch, tmp_path):
        import agentnexus.observability.alerting as mod
        monkeypatch.setattr(mod, "_global_alert_manager", None)
        traces_dir = str(tmp_path / "traces" / "2025-01-01.jsonl")
        manager = setup_default_alerts(traces_dir=traces_dir)
        assert len(manager._channels) == 2
        assert any(isinstance(ch, LogAlertChannel) for ch in manager._channels)


class TestGetAlertManager:
    def test_returns_same_instance(self, monkeypatch):
        import agentnexus.observability.alerting as mod
        monkeypatch.setattr(mod, "_global_alert_manager", None)
        m1 = get_alert_manager()
        m2 = get_alert_manager()
        assert m1 is m2


class TestEvaluateStats:
    def test_fires_cost_rule_from_stats(self, monkeypatch):
        import agentnexus.observability.alerting as mod

        monkeypatch.setattr(mod, "_global_alert_manager", None)
        manager = setup_default_alerts()
        stats = MagicMock()
        stats.total_cost_cny = 12.0
        stats.tool_failure_rate = 0.0
        stats.tool_total_count = 0
        stats.task_success_rate = 1.0
        stats.total_tasks = 0

        alerts = evaluate_stats(stats)

        assert any(a.alert_type == AlertType.COST_EXCEED for a in alerts)
        assert len(manager.history) >= 1

    def test_quiet_when_under_thresholds(self, monkeypatch):
        import agentnexus.observability.alerting as mod

        monkeypatch.setattr(mod, "_global_alert_manager", None)
        setup_default_alerts()
        stats = MagicMock()
        stats.total_cost_cny = 0.1
        stats.tool_failure_rate = 0.0
        stats.tool_total_count = 10
        stats.task_success_rate = 1.0
        stats.total_tasks = 5

        alerts = evaluate_stats(stats)

        assert alerts == []


class TestEmitDriftAlerts:
    def test_emits_only_critical_signals(self, monkeypatch):
        import agentnexus.observability.alerting as mod

        monkeypatch.setattr(mod, "_global_alert_manager", None)
        manager = setup_default_alerts()

        class _Sev:
            def __init__(self, value):
                self.value = value

        class _Type:
            def __init__(self, value):
                self.value = value

        class _Sig:
            def __init__(self, severity, detail, signal_type="goal_drift", step_index=3):
                self.severity = _Sev(severity)
                self.detail = detail
                self.signal_type = _Type(signal_type)
                self.step_index = step_index

        signals = [
            _Sig("warning", "soft drift"),
            _Sig("critical", "hard drift", signal_type="repeated_steps", step_index=6),
        ]

        emitted = emit_drift_alerts(signals, trace_id="t-1")

        assert len(emitted) == 1
        assert emitted[0].alert_type == AlertType.DRIFT
        assert emitted[0].severity == AlertSeverity.CRITICAL
        assert emitted[0].trace_id == "t-1"
        assert emitted[0].details["signal_type"] == "repeated_steps"
        assert manager.history[-1] is emitted[0]
