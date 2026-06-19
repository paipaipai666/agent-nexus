"""Tests for agentnexus.observability.fault_attribution."""
import pytest

from agentnexus.observability.fault_attribution import (
    FaultAttributionReport,
    FaultRecord,
    FaultSeverity,
    FaultType,
    classify_tool_fault,
)


# ── FaultAttributionReport properties ────────────────────────────


class TestFaultAttributionReport:
    def test_fault_rate_with_no_faults(self):
        # Arrange
        report = FaultAttributionReport(trace_id="t1", total_tool_calls=10, fault_count=0)
        # Act & Assert
        assert report.fault_rate == 0.0

    def test_fault_rate_calculation(self):
        # Arrange
        report = FaultAttributionReport(trace_id="t1", total_tool_calls=10, fault_count=3)
        # Act & Assert
        assert report.fault_rate == pytest.approx(0.3)

    def test_fault_rate_zero_tool_calls_defaults_to_one(self):
        # Arrange — avoids division by zero via max(1, total_tool_calls)
        report = FaultAttributionReport(trace_id="t1", total_tool_calls=0, fault_count=1)
        # Act & Assert
        assert report.fault_rate == pytest.approx(1.0)

    def test_has_critical_true_when_critical_fault_present(self):
        # Arrange
        report = FaultAttributionReport(
            trace_id="t1",
            faults=[
                FaultRecord(
                    fault_type=FaultType.TOOL_SERVICE,
                    tool_name="svc",
                    detail="down",
                    severity=FaultSeverity.CRITICAL,
                ),
            ],
        )
        # Act & Assert
        assert report.has_critical is True

    def test_has_critical_false_when_no_critical_fault(self):
        # Arrange
        report = FaultAttributionReport(
            trace_id="t1",
            faults=[
                FaultRecord(
                    fault_type=FaultType.PARAM_GENERATION,
                    tool_name="svc",
                    detail="bad param",
                    severity=FaultSeverity.MEDIUM,
                ),
            ],
        )
        # Act & Assert
        assert report.has_critical is False

    def test_passed_true_when_no_critical_and_low_fault_rate(self):
        # Arrange
        report = FaultAttributionReport(
            trace_id="t1",
            total_tool_calls=10,
            fault_count=2,
            faults=[
                FaultRecord(
                    fault_type=FaultType.TOOL_SERVICE,
                    tool_name="svc",
                    detail="timeout",
                    severity=FaultSeverity.MEDIUM,
                ),
            ],
        )
        # Act & Assert
        assert report.passed is True

    def test_passed_false_when_critical_present(self):
        # Arrange
        report = FaultAttributionReport(
            trace_id="t1",
            total_tool_calls=10,
            fault_count=1,
            faults=[
                FaultRecord(
                    fault_type=FaultType.TOOL_SERVICE,
                    tool_name="svc",
                    detail="critical failure",
                    severity=FaultSeverity.CRITICAL,
                ),
            ],
        )
        # Act & Assert
        assert report.passed is False

    def test_passed_false_when_fault_rate_high(self):
        # Arrange — fault_rate=0.5 >= 0.3
        report = FaultAttributionReport(
            trace_id="t1",
            total_tool_calls=10,
            fault_count=5,
            faults=[
                FaultRecord(
                    fault_type=FaultType.TOOL_SERVICE,
                    tool_name="svc",
                    detail="err",
                    severity=FaultSeverity.MEDIUM,
                ),
            ],
        )
        # Act & Assert
        assert report.passed is False

    def test_by_type_groups_correctly(self):
        # Arrange
        report = FaultAttributionReport(
            trace_id="t1",
            faults=[
                FaultRecord(
                    fault_type=FaultType.TOOL_SERVICE,
                    tool_name="a",
                    detail="e1",
                ),
                FaultRecord(
                    fault_type=FaultType.TOOL_SERVICE,
                    tool_name="b",
                    detail="e2",
                ),
                FaultRecord(
                    fault_type=FaultType.PARAM_GENERATION,
                    tool_name="c",
                    detail="e3",
                ),
            ],
        )
        # Act
        by_type = report.by_type
        # Assert
        assert by_type["tool_service"] == 2
        assert by_type["param_generation"] == 1
        assert len(by_type) == 2

    def test_by_type_empty_when_no_faults(self):
        # Arrange
        report = FaultAttributionReport(trace_id="t1")
        # Act & Assert
        assert report.by_type == {}


# ── classify_tool_fault — permission boundary ────────────────────


class TestClassifyToolFaultPermission:
    def test_classifies_permission_error(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="exec_code",
            error_message="Permission denied: not allowed to execute",
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.PERMISSION_BOUNDARY
        assert record.severity == FaultSeverity.HIGH

    def test_classifies_blocked_keyword(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="delete_file",
            error_message="Operation blocked by HITL policy",
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.PERMISSION_BOUNDARY
        assert record.severity == FaultSeverity.HIGH

    def test_classifies_rbac_keyword(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="admin_tool",
            error_message="RBAC check failed",
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.PERMISSION_BOUNDARY


# ── classify_tool_fault — param generation ───────────────────────


class TestClassifyToolFaultParamGeneration:
    def test_classifies_validation_error(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="api_call",
            error_message="Validation error: missing param 'url'",
            params={"method": "GET"},
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.PARAM_GENERATION
        assert record.severity == FaultSeverity.MEDIUM

    def test_classifies_schema_error(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="api_call",
            error_message="Schema validation failed: invalid param type",
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.PARAM_GENERATION

    def test_classifies_json_error(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="parse",
            error_message="JSON decode error at position 42",
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.PARAM_GENERATION


# ── classify_tool_fault — tool service ───────────────────────────


class TestClassifyToolFaultToolService:
    def test_classifies_timeout_error(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="web_search",
            error_message="Connection timed out after 30s",
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.TOOL_SERVICE
        assert record.severity == FaultSeverity.MEDIUM

    def test_classifies_500_error(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="api_call",
            error_message="Server returned 500 Internal Server Error",
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.TOOL_SERVICE

    def test_classifies_unavailable_error(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="mcp_server",
            error_message="Service unavailable, connection refused",
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.TOOL_SERVICE


# ── classify_tool_fault — tool selection ──────────────────────────


class TestClassifyToolFaultSelection:
    def test_classifies_mismatched_intent_and_tool(self):
        # Arrange & Act — intent about "search", but tool is "calculator"
        record = classify_tool_fault(
            tool_name="calculator",
            error_message="unexpected input format",
            available_tools=["calculator", "web_search", "grep"],
            caller_intent="search the web for news",
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.TOOL_SELECTION
        assert record.severity == FaultSeverity.LOW

    def test_no_selection_fault_when_intent_matches_tool(self):
        # Arrange & Act — "search" appears in both intent and tool name
        record = classify_tool_fault(
            tool_name="web_search",
            error_message="unexpected input format",
            available_tools=["web_search", "calculator"],
            caller_intent="search the web",
        )
        # Assert
        # Should fall through to default TOOL_SERVICE since keywords don't match
        # and tool_selection requires no overlap
        assert record is not None
        # The tool name tokens overlap with intent, so no TOOL_SELECTION
        assert record.fault_type != FaultType.TOOL_SELECTION

    def test_no_selection_fault_without_caller_intent(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="calculator",
            error_message="unexpected error",
            available_tools=["calculator", "grep"],
        )
        # Assert
        assert record is not None
        # Without caller_intent, tool_selection branch is skipped
        assert record.fault_type != FaultType.TOOL_SELECTION

    def test_no_selection_fault_without_available_tools(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="calculator",
            error_message="unexpected error",
            caller_intent="search the web",
        )
        # Assert
        assert record is not None
        assert record.fault_type != FaultType.TOOL_SELECTION


# ── classify_tool_fault — edge cases ─────────────────────────────


class TestClassifyToolFaultEdgeCases:
    def test_returns_none_when_no_error_message(self):
        # Arrange & Act
        record = classify_tool_fault(tool_name="grep", error_message=None)
        # Assert
        assert record is None

    def test_defaults_to_tool_service_for_unknown_error(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="mystery_tool",
            error_message="something completely unexpected happened",
        )
        # Assert
        assert record is not None
        assert record.fault_type == FaultType.TOOL_SERVICE
        assert record.severity == FaultSeverity.MEDIUM

    def test_detail_truncated_to_500_chars(self):
        # Arrange
        long_error = "x" * 1000
        # Act
        record = classify_tool_fault(tool_name="svc", error_message=long_error)
        # Assert
        assert record is not None
        assert len(record.detail) == 500

    def test_evidence_error_truncated_to_200_chars(self):
        # Arrange
        long_error = "y" * 500
        # Act
        record = classify_tool_fault(tool_name="svc", error_message=long_error)
        # Assert
        assert record is not None
        assert len(record.evidence["error"]) == 200

    def test_stores_tool_name_in_record(self):
        # Arrange & Act
        record = classify_tool_fault(
            tool_name="my_custom_tool",
            error_message="timeout error",
        )
        # Assert
        assert record is not None
        assert record.tool_name == "my_custom_tool"

    def test_permission_takes_priority_over_param(self):
        # Arrange — error contains both "permission" and "validation"
        # Act
        record = classify_tool_fault(
            tool_name="svc",
            error_message="Permission denied: validation of RBAC token failed",
        )
        # Assert — permission check runs first
        assert record is not None
        assert record.fault_type == FaultType.PERMISSION_BOUNDARY
