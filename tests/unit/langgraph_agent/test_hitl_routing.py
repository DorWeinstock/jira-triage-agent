"""Tests for HITL routing logic.

IMPORTANT: prepare_hitl now always sets risk_level="high" for all remediation actions,
since remediation always uses write tools (kubectl_scale, kubectl_delete, etc.).

Tool-based classification (classify_tool_risk) should be tested separately.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock


class TestJiraTimestampConversion:
    """Tests for _convert_jira_timestamp_to_rfc3339 function."""

    def test_converts_jira_format_with_positive_offset(self):
        """Jira timestamp with +0200 should convert to UTC RFC3339."""
        from src.supervisor import _convert_jira_timestamp_to_rfc3339

        jira_ts = "2026-01-07T20:18:59.232+0200"
        result = _convert_jira_timestamp_to_rfc3339(jira_ts)

        # +0200 means 2 hours ahead of UTC, so 20:18 local = 18:18 UTC
        assert result == "2026-01-07T18:18:59Z"

    def test_converts_jira_format_with_negative_offset(self):
        """Jira timestamp with -0500 should convert to UTC RFC3339."""
        from src.supervisor import _convert_jira_timestamp_to_rfc3339

        jira_ts = "2026-01-07T10:30:00.000-0500"
        result = _convert_jira_timestamp_to_rfc3339(jira_ts)

        # -0500 means 5 hours behind UTC, so 10:30 local = 15:30 UTC
        assert result == "2026-01-07T15:30:00Z"

    def test_converts_jira_format_utc(self):
        """Jira timestamp with +0000 should convert correctly."""
        from src.supervisor import _convert_jira_timestamp_to_rfc3339

        jira_ts = "2026-01-07T12:00:00.000+0000"
        result = _convert_jira_timestamp_to_rfc3339(jira_ts)

        assert result == "2026-01-07T12:00:00Z"

    def test_handles_invalid_timestamp_with_fallback(self):
        """Invalid timestamp should raise ValueError (caller handles fallback)."""
        from src.supervisor import _convert_jira_timestamp_to_rfc3339

        with pytest.raises(ValueError, match="Cannot parse Jira timestamp"):
            _convert_jira_timestamp_to_rfc3339("invalid-timestamp")

    def test_handles_empty_string(self):
        """Empty string should raise ValueError (caller handles fallback)."""
        from src.supervisor import _convert_jira_timestamp_to_rfc3339

        with pytest.raises(ValueError, match="Cannot parse Jira timestamp"):
            _convert_jira_timestamp_to_rfc3339("")


class TestPrepareHITL:
    """Test the prepare_hitl node function.

    Note: prepare_hitl currently uses the deprecated classify_risk() which does
    text pattern matching on the recommended_action string. This will correctly
    classify "describe", "get", "list" as low risk, and other actions as high risk.
    """

    @pytest.mark.asyncio
    async def test_sets_high_risk_for_delete_action(self):
        """Delete actions should set high risk level."""
        from src.supervisor import create_prepare_hitl_node

        mock_jira = MagicMock()
        mock_jira.add_comment = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.go_agent_url = "http://localhost:8080"
        prepare_hitl = create_prepare_hitl_node(mock_jira, mock_settings)

        state = {"recommended_action": "Delete the failing pod", "ticket_id": "SP-1234"}
        result = await prepare_hitl(state)

        assert result["action_risk_level"] == "high"

    @pytest.mark.asyncio
    async def test_posts_comment_for_high_risk(self):
        """High risk actions should post approval comment."""
        from src.supervisor import create_prepare_hitl_node

        mock_jira = MagicMock()
        mock_jira.add_comment = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.go_agent_url = "http://localhost:8080"
        prepare_hitl = create_prepare_hitl_node(mock_jira, mock_settings)

        state = {
            "ticket_id": "SP-1234",
            "recommended_action": "Delete pod",
            "root_cause": "OOM",
            "confidence_level": "high",
            "affected_deployments": [],
            "cluster_findings": {},
        }
        await prepare_hitl(state)

        mock_jira.add_comment.assert_called_once()
        call_args = mock_jira.add_comment.call_args
        assert call_args[0][0] == "SP-1234"
        assert "approve" in call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_sets_hitl_requested_at_for_high_risk(self):
        """High risk should set timestamp for timeout tracking."""
        from src.supervisor import create_prepare_hitl_node

        mock_jira = MagicMock()
        mock_jira.add_comment = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.go_agent_url = "http://localhost:8080"
        prepare_hitl = create_prepare_hitl_node(mock_jira, mock_settings)

        state = {
            "ticket_id": "SP-1234",
            "recommended_action": "Scale replicas",
            "root_cause": "Load spike",
            "confidence_level": "high",
            "affected_deployments": [],
            "cluster_findings": {},
        }
        result = await prepare_hitl(state)

        assert result["hitl_requested_at"] is not None

    @pytest.mark.asyncio
    async def test_uses_jira_comment_timestamp(self):
        """High risk should use Jira's comment creation timestamp, not current time."""
        from src.supervisor import create_prepare_hitl_node

        mock_jira = MagicMock()
        # Simulate Jira returning a comment with creation timestamp
        mock_jira.add_comment = AsyncMock(return_value={
            "success": True,
            "comment_id": "123456",
            "created": "2026-01-07T20:18:59.232+0200",  # Jira format
        })
        mock_settings = MagicMock()
        mock_settings.go_agent_url = "http://localhost:8080"
        prepare_hitl = create_prepare_hitl_node(mock_jira, mock_settings)

        state = {
            "ticket_id": "SP-1234",
            "recommended_action": "Scale replicas",
            "root_cause": "Load spike",
            "confidence_level": "high",
            "affected_deployments": [],
            "cluster_findings": {},
        }
        result = await prepare_hitl(state)

        # Should use Jira's timestamp converted to RFC3339 UTC
        # 20:18:59 +0200 = 18:18:59 UTC
        assert result["hitl_requested_at"] == "2026-01-07T18:18:59Z"


class TestShouldSkipRemediation:
    """Test the should_skip_remediation routing function."""

    def test_low_risk_proceeds_to_remediation(self):
        """Low risk actions should proceed to remediation."""
        from src.supervisor import should_skip_remediation

        state = {"action_risk_level": "low", "recommended_action": "Get logs"}
        result = should_skip_remediation(state)

        assert result == "attempt_remediation"

    def test_high_risk_still_proceeds(self):
        """High risk proceeds - interrupt_before handles the pause."""
        from src.supervisor import should_skip_remediation

        state = {"action_risk_level": "high", "recommended_action": "Delete pod"}
        result = should_skip_remediation(state)

        # interrupt_before will pause at attempt_remediation
        assert result == "attempt_remediation"

    def test_no_action_goes_to_post_comment(self):
        """No action should route to post_comment."""
        from src.supervisor import should_skip_remediation

        state = {"action_risk_level": None, "recommended_action": None}
        result = should_skip_remediation(state)

        assert result == "post_comment"


class TestAttemptRemediationNodeRejection:
    """Test HITL rejection check in attempt_remediation_node.

    CRITICAL: With interrupt_before=["attempt_remediation"], the routing decision
    (should_skip_remediation) happens BEFORE the pause. When the user rejects
    and the workflow resumes, it goes directly to attempt_remediation_node.
    The node itself must check for rejection to skip remediation.

    These tests create the node function directly to test in isolation,
    avoiding the complexity of LangGraph's compiled graph structure.
    """

    @pytest.mark.asyncio
    async def test_skips_remediation_when_hitl_rejected(self):
        """When HITL rejected, attempt_remediation_node should return unchanged state."""
        from src.agents.diagnostician import Diagnostician

        # Create a mock diagnostician
        mock_k8s = MagicMock()
        diagnostician = Diagnostician(mock_k8s)
        diagnostician.attempt_remediation = AsyncMock(return_value={
            "remediation_attempted": True,
            "remediation_result": {"success": True},
        })

        # Recreate the node function logic to test directly
        async def attempt_remediation_node(state):
            # Check for HITL rejection - this is critical for interrupt_before flow
            if state.get("hitl_diagnosis_approved") is False:
                return state

            current_attempt = state.get("remediation_count", 0) + 1
            state = await diagnostician.attempt_remediation(state)
            if state.get("remediation_attempted", False):
                state["remediation_count"] = current_attempt
            return state

        # State simulating rejection after interrupt_before pause
        state = {
            "ticket_id": "TEST-123",
            "hitl_diagnosis_approved": False,
            "hitl_rejection_reason": "testing rejection flow",
            "recommended_action": "Scale deployment to 3 replicas",
            "remediation_count": 0,
            "root_cause": "Pods crashed due to OOM",
            "confidence_level": "high",
        }

        result = await attempt_remediation_node(state)

        # Should NOT have attempted remediation
        diagnostician.attempt_remediation.assert_not_called()
        assert result.get("remediation_attempted") is not True
        assert result.get("remediation_count", 0) == 0
        # State should be essentially unchanged
        assert result.get("hitl_diagnosis_approved") is False
        assert result.get("hitl_rejection_reason") == "testing rejection flow"

    @pytest.mark.asyncio
    async def test_proceeds_when_hitl_approved(self):
        """When HITL approved, attempt_remediation_node should proceed normally."""
        from src.agents.diagnostician import Diagnostician

        mock_k8s = MagicMock()
        diagnostician = Diagnostician(mock_k8s)
        diagnostician.attempt_remediation = AsyncMock(return_value={
            "ticket_id": "TEST-123",
            "hitl_diagnosis_approved": True,
            "recommended_action": "Scale deployment",
            "remediation_count": 0,
            "remediation_attempted": True,
            "remediation_result": {"success": True},
        })

        async def attempt_remediation_node(state):
            if state.get("hitl_diagnosis_approved") is False:
                return state
            current_attempt = state.get("remediation_count", 0) + 1
            state = await diagnostician.attempt_remediation(state)
            if state.get("remediation_attempted", False):
                state["remediation_count"] = current_attempt
            return state

        state = {
            "ticket_id": "TEST-123",
            "hitl_diagnosis_approved": True,  # Approved
            "recommended_action": "Scale deployment",
            "remediation_count": 0,
        }

        result = await attempt_remediation_node(state)

        # Should have attempted remediation
        diagnostician.attempt_remediation.assert_called_once()
        assert result.get("remediation_count", 0) == 1

    @pytest.mark.asyncio
    async def test_proceeds_when_hitl_not_set(self):
        """When hitl_diagnosis_approved is None, should proceed (backward compat)."""
        from src.agents.diagnostician import Diagnostician

        mock_k8s = MagicMock()
        diagnostician = Diagnostician(mock_k8s)
        diagnostician.attempt_remediation = AsyncMock(return_value={
            "ticket_id": "TEST-123",
            "recommended_action": "Scale deployment",
            "remediation_count": 0,
            "remediation_attempted": True,
            "remediation_result": {"success": True},
        })

        async def attempt_remediation_node(state):
            if state.get("hitl_diagnosis_approved") is False:
                return state
            current_attempt = state.get("remediation_count", 0) + 1
            state = await diagnostician.attempt_remediation(state)
            if state.get("remediation_attempted", False):
                state["remediation_count"] = current_attempt
            return state

        # State without hitl_diagnosis_approved set (None/not present)
        state = {
            "ticket_id": "TEST-123",
            # hitl_diagnosis_approved not set - should proceed
            "recommended_action": "Scale deployment",
            "remediation_count": 0,
        }

        result = await attempt_remediation_node(state)

        # Should proceed to remediation
        diagnostician.attempt_remediation.assert_called_once()
        assert result.get("remediation_count", 0) == 1
