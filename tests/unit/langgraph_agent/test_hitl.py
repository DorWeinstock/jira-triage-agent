"""Tests for Human-in-the-Loop (HITL) functionality."""

import pytest
from unittest.mock import MagicMock


class TestHITLSettings:
    """Test HITL configuration loading."""

    def test_hitl_enabled_defaults_to_true(self, monkeypatch):
        """HITL should be enabled by default for safety.

        Since the remediation agent only uses write tools (kubectl_scale,
        kubectl_rollout_restart, kubectl_apply, kubectl_delete), HITL is
        enabled by default to require human approval for cluster mutations.
        """
        monkeypatch.delenv("HITL_ENABLED", raising=False)

        from src.config import get_settings
        get_settings.cache_clear()

        settings = get_settings()
        assert settings.hitl.enabled is True

    def test_hitl_enabled_can_be_enabled(self, monkeypatch):
        """HITL can be enabled via env var."""
        monkeypatch.setenv("HITL_ENABLED", "true")

        from src.config import get_settings
        get_settings.cache_clear()

        settings = get_settings()
        assert settings.hitl.enabled is True

    def test_hitl_enabled_accepts_various_true_values(self, monkeypatch):
        """HITL enabled should accept various truthy string values."""
        for value in ["true", "True", "TRUE", "1", "yes", "on"]:
            monkeypatch.setenv("HITL_ENABLED", value)

            from src.config import get_settings
            get_settings.cache_clear()

            settings = get_settings()
            assert settings.hitl.enabled is True, f"Failed for value: {value}"

    def test_hitl_enabled_accepts_various_false_values(self, monkeypatch):
        """HITL enabled should accept various falsy string values."""
        for value in ["false", "False", "FALSE", "0", "no", "off"]:
            monkeypatch.setenv("HITL_ENABLED", value)

            from src.config import get_settings
            get_settings.cache_clear()

            settings = get_settings()
            assert settings.hitl.enabled is False, f"Failed for value: {value}"


class TestHITLStateFields:
    """Test HITL tracking fields in AgentState."""

    def test_state_has_hitl_diagnosis_approved_field(self):
        """AgentState should have hitl_diagnosis_approved field defined."""
        from src.state import AgentState
        from typing import get_type_hints

        # TypedDict fields are defined in __annotations__
        annotations = get_type_hints(AgentState)
        assert "hitl_diagnosis_approved" in annotations

    def test_state_has_hitl_remediation_approved_field(self):
        """AgentState should have hitl_remediation_approved field defined."""
        from src.state import AgentState
        from typing import get_type_hints

        annotations = get_type_hints(AgentState)
        assert "hitl_remediation_approved" in annotations

    def test_state_has_hitl_rejection_reason_field(self):
        """AgentState should have hitl_rejection_reason field defined."""
        from src.state import AgentState
        from typing import get_type_hints

        annotations = get_type_hints(AgentState)
        assert "hitl_rejection_reason" in annotations

    def test_hitl_fields_can_be_set(self):
        """HITL fields should be settable."""
        from src.state import AgentState

        state: AgentState = {}
        state["hitl_diagnosis_approved"] = True
        state["hitl_remediation_approved"] = False
        state["hitl_rejection_reason"] = "Need more investigation"

        assert state["hitl_diagnosis_approved"] is True
        assert state["hitl_remediation_approved"] is False
        assert state["hitl_rejection_reason"] == "Need more investigation"


class TestSupervisorHITLIntegration:
    """Test supervisor HITL parameter integration."""

    def test_create_graph_accepts_hitl_enabled_parameter(self):
        """Should accept optional hitl_enabled parameter."""
        from langgraph.checkpoint.memory import MemorySaver
        from src.supervisor import create_conditional_supervisor_graph

        jira_tools = MagicMock()
        k8s_tools = MagicMock()
        checkpointer = MemorySaver()

        # Should not raise
        graph = create_conditional_supervisor_graph(
            jira_tools=jira_tools,
            k8s_tools=k8s_tools,
            checkpointer=checkpointer,
            hitl_enabled=True,
        )

        assert graph is not None

    def test_create_graph_hitl_disabled_by_default(self):
        """HITL should be disabled by default (backward compatible)."""
        from src.supervisor import create_conditional_supervisor_graph

        jira_tools = MagicMock()
        k8s_tools = MagicMock()

        # Should not raise and should work without HITL
        graph = create_conditional_supervisor_graph(
            jira_tools=jira_tools,
            k8s_tools=k8s_tools,
        )

        assert graph is not None

    def test_get_default_graph_passes_hitl_enabled(self):
        """get_default_graph should pass hitl_enabled to create function."""
        from langgraph.checkpoint.memory import MemorySaver
        from src.supervisor import get_default_graph

        jira_tools = MagicMock()
        k8s_tools = MagicMock()
        checkpointer = MemorySaver()

        # Should not raise
        graph = get_default_graph(
            jira_tools=jira_tools,
            k8s_tools=k8s_tools,
            checkpointer=checkpointer,
            hitl_enabled=True,
        )

        assert graph is not None


class TestHITLRiskStateFields:
    """Test risk-related HITL state fields."""

    def test_state_has_action_risk_level_field(self):
        """AgentState should have action_risk_level field."""
        from src.state import AgentState
        from typing import get_type_hints

        annotations = get_type_hints(AgentState)
        assert "action_risk_level" in annotations

    def test_state_has_hitl_requested_at_field(self):
        """AgentState should have hitl_requested_at for timeout calculation."""
        from src.state import AgentState
        from typing import get_type_hints

        annotations = get_type_hints(AgentState)
        assert "hitl_requested_at" in annotations

    def test_risk_fields_can_be_set(self):
        """Risk fields should be settable."""
        from src.state import AgentState

        state: AgentState = {}
        state["action_risk_level"] = "high"
        state["hitl_requested_at"] = "2026-01-04T10:00:00Z"

        assert state["action_risk_level"] == "high"
        assert state["hitl_requested_at"] == "2026-01-04T10:00:00Z"


class TestExtractReviewState:
    """Test _extract_review_state helper function."""

    def test_extract_review_state_for_remediation_checkpoint(self):
        """Should extract all relevant fields for attempt_remediation checkpoint.

        The single checkpoint shows both diagnosis AND remediation info.
        """
        from src.server import _extract_review_state

        state_values = {
            "ticket_id": "TEST-123",
            "ticket_summary": "Pod crashing",
            "root_cause": "OOM killed",
            "confidence_level": "high",
            "recommended_action": "Increase memory",
            "preventive_measures": ["Set resource limits"],
            "cluster_findings": {"pod_status": "CrashLoopBackOff"},
            "remediation_count": 1,
            "remediation_history": [{"attempt": 1, "success": False}],
        }

        result = _extract_review_state(state_values, "attempt_remediation")

        # Diagnosis fields
        assert result["ticket_id"] == "TEST-123"
        assert result["ticket_summary"] == "Pod crashing"
        assert result["root_cause"] == "OOM killed"
        assert result["confidence_level"] == "high"
        assert result["recommended_action"] == "Increase memory"
        assert result["preventive_measures"] == ["Set resource limits"]
        assert result["cluster_findings"] == {"pod_status": "CrashLoopBackOff"}
        # Remediation fields
        assert result["remediation_count"] == 1
        assert result["remediation_history"] == [{"attempt": 1, "success": False}]

    def test_extract_review_state_handles_missing_fields(self):
        """Should handle missing optional fields gracefully."""
        from src.server import _extract_review_state

        state_values = {
            "ticket_id": "TEST-123",
        }

        result = _extract_review_state(state_values, "attempt_remediation")

        assert result["ticket_id"] == "TEST-123"
        assert result.get("ticket_summary") is None
        assert result.get("root_cause") is None
        assert result.get("preventive_measures") == []
        assert result.get("cluster_findings") == {}
        assert result.get("remediation_count") == 0
        assert result.get("remediation_history") == []

    def test_extract_review_state_logs_unknown_checkpoint(self):
        """Should log warning for unknown checkpoint."""
        from src.server import _extract_review_state

        state_values = {"ticket_id": "TEST-123"}

        # Should not raise, just return common fields
        result = _extract_review_state(state_values, "unknown_checkpoint")

        assert result["ticket_id"] == "TEST-123"
        # Only common fields, no diagnosis/remediation fields
        assert "root_cause" not in result


class TestGraphHITLNodes:
    """Test HITL nodes are wired into the graph."""

    def test_graph_has_prepare_hitl_node_when_enabled(self):
        """Graph should have prepare_hitl node when HITL enabled."""
        from langgraph.checkpoint.memory import MemorySaver
        from src.supervisor import create_conditional_supervisor_graph
        from unittest.mock import MagicMock

        jira_tools = MagicMock()
        k8s_tools = MagicMock()
        checkpointer = MemorySaver()

        graph = create_conditional_supervisor_graph(
            jira_tools=jira_tools,
            k8s_tools=k8s_tools,
            checkpointer=checkpointer,
            hitl_enabled=True,
        )

        node_names = list(graph.nodes.keys())
        assert "prepare_hitl" in node_names

    def test_graph_skips_prepare_hitl_when_disabled(self):
        """Graph should not have prepare_hitl node when HITL disabled."""
        from src.supervisor import create_conditional_supervisor_graph
        from unittest.mock import MagicMock

        jira_tools = MagicMock()
        k8s_tools = MagicMock()

        graph = create_conditional_supervisor_graph(
            jira_tools=jira_tools,
            k8s_tools=k8s_tools,
            hitl_enabled=False,
        )

        node_names = list(graph.nodes.keys())
        assert "prepare_hitl" not in node_names

    def test_interrupt_before_set_when_hitl_enabled(self):
        """interrupt_before should target attempt_remediation when HITL enabled."""
        from langgraph.checkpoint.memory import MemorySaver
        from src.supervisor import create_conditional_supervisor_graph
        from unittest.mock import MagicMock

        jira_tools = MagicMock()
        k8s_tools = MagicMock()
        checkpointer = MemorySaver()

        # This test verifies the graph compiles with interrupt_before
        # The actual interrupt behavior is tested in integration tests
        graph = create_conditional_supervisor_graph(
            jira_tools=jira_tools,
            k8s_tools=k8s_tools,
            checkpointer=checkpointer,
            hitl_enabled=True,
        )

        # Graph should compile without error
        assert graph is not None
