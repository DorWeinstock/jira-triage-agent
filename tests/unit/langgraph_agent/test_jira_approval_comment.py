"""Unit tests for HITL approval comment formatting."""

import pytest
from unittest.mock import MagicMock

from src.agents.jira_agent import JiraAgent
from src.state import AgentState


class TestJiraApprovalComment:
    """Test Jira comment formatting when HITL is approved and issue resolved."""

    @pytest.fixture
    def mock_jira_tools(self):
        """Create mock Jira tools."""
        tools = MagicMock()
        return tools

    @pytest.fixture
    def approved_state(self):
        """Create a state representing an approved HITL with resolved issue."""
        return AgentState(
            ticket_id="PROJ-123",
            root_cause="Deployment scaled to 0 replicas - no pods are running to serve traffic.",
            recommended_action="Scale deployment order-service from 0 to 2 replicas using: kubectl scale deployment order-service --replicas=2 -n production",
            confidence_level="high",
            action_risk_level="low",
            hitl_remediation_approved=True,
            hitl_requested_at="2026-01-07T14:32:00Z",
            affected_deployments=["order-service"],
            affected_services=["order-service"],
            namespace="production",
            issue_category="deployment_scaling",
            issue_resolved=True,
            remediation_count=1,
            remediation_attempted=True,
            remediation_history=[
                {"action": "Scaled deployment to 2 replicas", "success": True}
            ],
            verification_evidence=[
                "Deployment has 2/2 replicas ready",
                "All pods are in Running state",
                "Service endpoints are populated"
            ],
            similar_tickets=[
                {"key": "SP-3842", "summary": "order-service down", "is_resolved": True}
            ],
        )

    def test_approval_triggers_special_format(self, mock_jira_tools, approved_state):
        """Test that hitl_remediation_approved=True + issue_resolved=True triggers approval format."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(approved_state)

        # Should have approval banner, not standard banners
        assert "Human Approved Fix Applied Successfully" in comment
        assert "REMEDIATION REJECTED" not in comment
        assert "Manual Investigation Required" not in comment

    def test_approval_comment_has_status_banner(self, mock_jira_tools, approved_state):
        """Test approval comment has the green status banner."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(approved_state)

        assert "{panel:bgColor=#d4edda" in comment
        assert "RESOLVED" in comment
        assert "Human Approved" in comment

    def test_approval_comment_has_approval_details(self, mock_jira_tools, approved_state):
        """Test approval comment shows approval timestamp."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(approved_state)

        assert "Approval Details" in comment
        assert "2026-01-07T14:32:00Z" in comment
        assert "{panel:title=Approval Details|bgColor=#e8f5e9" in comment
        assert "Remediation applied and verified successful" in comment

    def test_approval_comment_has_applied_fix(self, mock_jira_tools, approved_state):
        """Test approval comment shows the applied fix."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(approved_state)

        assert "Applied Fix" in comment
        assert "{panel:title=Applied Fix" in comment
        assert "bgColor=#e3f2fd" in comment
        assert "deployment/order-service" in comment
        assert "production" in comment
        assert "high" in comment  # confidence
        assert "low" in comment  # risk level

    def test_approval_comment_has_remediation_history(self, mock_jira_tools, approved_state):
        """Test approval comment shows remediation actions performed."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(approved_state)

        assert "Actions Performed" in comment
        assert "Scaled deployment to 2 replicas" in comment

    def test_approval_comment_has_diagnosis(self, mock_jira_tools, approved_state):
        """Test approval comment includes diagnosis details."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(approved_state)

        assert "{panel:title=Diagnosis" in comment
        assert "Deployment scaled to 0 replicas" in comment
        assert "deployment_scaling" in comment

    def test_approval_comment_has_verification(self, mock_jira_tools, approved_state):
        """Test approval comment includes verification evidence."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(approved_state)

        assert "Verification" in comment
        assert "{panel:title=Verification|bgColor=#e8f5e9" in comment
        assert "2/2 replicas ready" in comment
        assert "All pods are in Running state" in comment
        assert "Service endpoints are populated" in comment

    def test_approval_comment_has_similar_issues(self, mock_jira_tools, approved_state):
        """Test approval comment includes similar past issues."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(approved_state)

        assert "Similar Past Issues" in comment
        assert "SP-3842" in comment
        assert "order-service down" in comment

    def test_approval_comment_excludes_next_steps_panel(self, mock_jira_tools, approved_state):
        """Test approval comment does not have Next Steps panel (removed for brevity)."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(approved_state)

        assert "{panel:title=Next Steps" not in comment

    def test_approval_comment_excludes_metadata_block(self, mock_jira_tools, approved_state):
        """Test approval comment does not have AI_AGENT_METADATA block (removed for brevity)."""
        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(approved_state)

        assert "AI_AGENT_METADATA_V1" not in comment

    def test_approval_without_verification_evidence(self, mock_jira_tools):
        """Test approval comment handles empty verification evidence."""
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Configuration issue fixed",
            recommended_action="Applied config fix",
            confidence_level="high",
            hitl_remediation_approved=True,
            issue_resolved=True,
            affected_deployments=["config-service"],
            namespace="staging",
            verification_evidence=[],
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        assert "Verification" in comment
        assert "Verification completed successfully" in comment

    def test_approval_without_remediation_history(self, mock_jira_tools):
        """Test approval comment handles empty remediation history."""
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Issue fixed",
            recommended_action="Fixed it",
            confidence_level="medium",
            hitl_remediation_approved=True,
            issue_resolved=True,
            affected_deployments=["my-service"],
            namespace="production",
            remediation_history=[],
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        assert "Applied Fix" in comment
        # Should not have "Actions Performed" table when no history
        assert "||#||Action||Result||" not in comment

    def test_approval_without_similar_tickets(self, mock_jira_tools):
        """Test approval comment handles empty similar tickets."""
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Unknown error fixed",
            recommended_action="Applied fix",
            confidence_level="low",
            hitl_remediation_approved=True,
            issue_resolved=True,
            similar_tickets=[],
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        assert "Similar Past Issues" in comment
        assert "No similar resolved issues found" in comment

    def test_approved_but_not_resolved_uses_standard_format(self, mock_jira_tools):
        """Test that approved but not resolved state uses standard format."""
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Issue identified",
            recommended_action="Fix pending",
            confidence_level="high",
            hitl_remediation_approved=True,  # Approved
            issue_resolved=False,  # But not resolved
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        # Should use standard format, not approval format
        assert "Human Approved Fix Applied Successfully" not in comment
        assert "NEEDS ATTENTION" in comment

    def test_not_approved_resolved_uses_standard_format(self, mock_jira_tools):
        """Test that non-HITL resolved state uses standard format (auto-resolved)."""
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Auto-fixed issue",
            recommended_action="Auto-applied",
            confidence_level="high",
            hitl_diagnosis_approved=None,  # No HITL involved
            issue_resolved=True,  # Resolved automatically
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        # Should use standard resolved format, not HITL approval format
        assert "Human Approved Fix Applied Successfully" not in comment
        assert "RESOLVED - Issue Fixed Automatically" in comment

    def test_service_only_target(self, mock_jira_tools):
        """Test target shows service when no deployments."""
        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Service misconfigured",
            recommended_action="Restart service",
            hitl_remediation_approved=True,
            issue_resolved=True,
            affected_deployments=[],
            affected_services=["my-service"],
            namespace="production",
        )

        agent = JiraAgent(mock_jira_tools)
        comment = agent._format_comment(state)

        assert "service/my-service" in comment
