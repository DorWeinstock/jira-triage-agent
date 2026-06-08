"""Unit tests for HITL rejection comment formatting."""

import pytest
from unittest.mock import MagicMock

from src.agents.jira_agent import JiraAgent
from src.state import AgentState


class TestJiraRejectionComment:
    """Test Jira comment formatting when HITL is rejected."""

    @pytest.fixture
    def mock_jira_tools(self):
        """Create mock Jira tools."""
        tools = MagicMock()
        return tools

    @pytest.fixture
    def rejected_state(self):
        """Create a state representing a rejected HITL."""
        return AgentState(
            ticket_id="PROJ-123",
            root_cause="Deployment scaled to 0 replicas - no pods are running to serve traffic.",
            recommended_action="Scale deployment order-service from 0 to 2 replicas using: kubectl scale deployment order-service --replicas=2 -n production",
            confidence_level="high",
            action_risk_level="low",
            hitl_diagnosis_approved=False,
            hitl_rejection_reason="Not safe to scale during peak hours, will do manually tonight",
            hitl_requested_at="2026-01-07T14:32:00Z",
            affected_deployments=["order-service"],
            affected_services=["order-service"],
            namespace="production",
            issue_category="deployment_scaling",
            similar_tickets=[
                {"key": "SP-3842", "summary": "order-service down", "is_resolved": True}
            ],
        )

    def test_rejection_triggers_special_format(self, mock_jira_tools, rejected_state):
        """Test that hitl_diagnosis_approved=False triggers rejection format."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(rejected_state)

        # Should have rejection banner, not standard banners
        assert "REMEDIATION REJECTED" in comment
        assert "RESOLVED - Issue Fixed Automatically" not in comment
        assert "NEEDS ATTENTION - Manual Investigation Required" not in comment

    def test_rejection_comment_has_status_banner(self, mock_jira_tools, rejected_state):
        """Test rejection comment has the red status banner."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(rejected_state)

        assert "{panel:bgColor=#f8d7da" in comment
        assert "REMEDIATION REJECTED" in comment
        assert "declined by the operator" in comment

    def test_rejection_comment_has_rejection_details(self, mock_jira_tools, rejected_state):
        """Test rejection comment shows rejection reason and timestamp."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(rejected_state)

        assert "Rejection Details" in comment
        assert "Not safe to scale during peak hours" in comment
        assert "2026-01-07T14:32:00Z" in comment
        assert "{panel:title=Rejection Details|bgColor=#fff3cd" in comment

    def test_rejection_comment_has_proposed_fix(self, mock_jira_tools, rejected_state):
        """Test rejection comment shows the proposed fix that was rejected."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(rejected_state)

        assert "Proposed Fix (NOT APPLIED)" in comment
        assert "{panel:title=Proposed Fix" in comment
        assert "bgColor=#e7f3ff" in comment
        assert "deployment/order-service" in comment
        assert "production" in comment
        assert "high" in comment  # confidence
        assert "low" in comment  # risk level

    def test_rejection_comment_extracts_kubectl_command(self, mock_jira_tools, rejected_state):
        """Test that kubectl command is extracted and shown."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(rejected_state)

        assert "kubectl scale deployment order-service" in comment
        assert "{code:bash}" in comment

    def test_rejection_comment_has_diagnosis(self, mock_jira_tools, rejected_state):
        """Test rejection comment includes diagnosis details."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(rejected_state)

        assert "{panel:title=Diagnosis" in comment
        assert "Deployment scaled to 0 replicas" in comment
        assert "deployment_scaling" in comment

    def test_rejection_comment_has_similar_issues(self, mock_jira_tools, rejected_state):
        """Test rejection comment includes similar past issues."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(rejected_state)

        assert "Similar Past Issues" in comment
        assert "SP-3842" in comment
        assert "order-service down" in comment

    def test_rejection_comment_excludes_next_steps_panel(self, mock_jira_tools, rejected_state):
        """Test rejection comment does not have Next Steps panel (removed for brevity)."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(rejected_state)

        assert "{panel:title=Next Steps" not in comment

    def test_rejection_comment_excludes_metadata_block(self, mock_jira_tools, rejected_state):
        """Test rejection comment does not have AI_AGENT_METADATA block (removed for brevity)."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(rejected_state)

        assert "AI_AGENT_METADATA_V1" not in comment

    def test_rejection_without_kubectl_shows_action_summary(self, mock_jira_tools):
        """Test rejection comment handles actions without explicit kubectl command."""
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Configuration drift detected",
            recommended_action="Restart the pod to pick up new config",
            confidence_level="medium",
            hitl_diagnosis_approved=False,
            hitl_rejection_reason="Will restart during maintenance window",
            affected_deployments=["config-service"],
            namespace="staging",
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        assert "REMEDIATION REJECTED" in comment
        assert "Restart the pod" in comment
        assert "{code:bash}" in comment

    def test_rejection_without_similar_tickets(self, mock_jira_tools):
        """Test rejection comment handles empty similar tickets."""
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Unknown error",
            recommended_action="Investigate further",
            confidence_level="low",
            hitl_diagnosis_approved=False,
            hitl_rejection_reason="Need more investigation",
            similar_tickets=[],
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        assert "Similar Past Issues" in comment
        assert "No similar resolved issues found" in comment

    def test_non_rejection_uses_approval_format(self, mock_jira_tools):
        """Test that approved + resolved state uses approval format (not rejection)."""
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Deployment issue",
            recommended_action="Scale up",
            confidence_level="high",
            hitl_remediation_approved=True,  # Approved, not rejected
            issue_resolved=True,
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        assert "REMEDIATION REJECTED" not in comment
        # Should use HITL approval format
        assert "Human Approved Fix Applied Successfully" in comment

    def test_pending_approval_uses_standard_format(self, mock_jira_tools):
        """Test that pending (None) state uses standard format."""
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Deployment issue",
            recommended_action="Scale up",
            confidence_level="high",
            hitl_diagnosis_approved=None,  # Pending, not rejected
            issue_resolved=False,
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        assert "REMEDIATION REJECTED" not in comment
        assert "NEEDS ATTENTION" in comment

    def test_rejection_takes_precedence_over_resolution(self, mock_jira_tools):
        """Test that rejection format is used even if issue_resolved=True.

        Edge case: If an operator rejects but the issue was somehow resolved anyway
        (e.g., auto-healed), the rejection format should still be used to accurately
        reflect that the proposed fix was rejected.
        """
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Deployment scaled to 0",
            recommended_action="Scale up",
            confidence_level="high",
            hitl_diagnosis_approved=False,  # Rejected
            hitl_rejection_reason="Manual fix preferred",
            issue_resolved=True,  # Issue resolved anyway
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        # Rejection should take precedence
        assert "REMEDIATION REJECTED" in comment
        assert "Human Approved" not in comment
        assert "Issue Fixed Automatically" not in comment
