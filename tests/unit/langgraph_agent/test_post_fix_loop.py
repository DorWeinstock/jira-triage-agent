"""Tests for post-fix loop detection in verification service.

This feature allows the system to detect NEW issues that emerge after
the original issue is resolved, enabling a loop: diagnose -> remediate -> verify -> (if new issues) -> diagnose again.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.verification_service import VerificationService


class TestPostFixLoopDetection:
    """Tests for detecting new issues after original fix passes."""

    @pytest.fixture
    def mock_k8s_investigator(self):
        """Create a mock K8s investigator."""
        investigator = MagicMock()
        investigator.run_verification_only = AsyncMock(return_value={
            "cluster_findings": {
                "resources": {
                    "pods": "pods healthy",
                    "deployment": "deployment healthy",
                },
                "events": [],
            }
        })
        return investigator

    @pytest.fixture
    def verification_service(self, mock_k8s_investigator):
        """Create verification service with mocked dependencies."""
        with patch("src.services.verification_service.create_extraction_llm") as mock_llm:
            mock_llm_instance = MagicMock()
            mock_llm.return_value = mock_llm_instance
            service = VerificationService(mock_k8s_investigator)
            service.llm = mock_llm_instance
            return service

    @pytest.mark.asyncio
    async def test_check_once_returns_new_issues_detected_flag(self, verification_service):
        """Test that _check_once returns new_issues_detected flag when new issues found."""
        # Mock LLM response indicating original resolved BUT new issues detected
        mock_response = MagicMock()
        mock_response.content = '''{"resolved": true, "confidence": "high", "evidence": ["Original issue resolved"], "reasoning": "Original issue is fixed", "new_issues_detected": true, "new_issues": ["New pod pending in namespace"]}'''

        verification_service.llm.ainvoke = AsyncMock(return_value=mock_response)

        state = {
            "ticket_summary": "Test issue",
            "ticket_description": "Test description",
            "root_cause": "Test root cause",
            "remediation_result": {"action_taken": "Restarted pod"},
            "cluster_findings": {},
        }

        result = await verification_service._check_once(state)

        # Should indicate both original resolved AND new issues found
        assert result["resolved"] is True
        assert result.get("new_issues_detected") is True
        assert "new_issues" in result

    @pytest.mark.asyncio
    async def test_check_once_no_new_issues(self, verification_service):
        """Test that _check_once returns no new issues when everything is clean."""
        mock_response = MagicMock()
        mock_response.content = '''{"resolved": true, "confidence": "high", "evidence": ["All issues resolved"], "reasoning": "Everything is healthy"}'''

        verification_service.llm.ainvoke = AsyncMock(return_value=mock_response)

        state = {
            "ticket_summary": "Test issue",
            "remediation_result": {"action_taken": "Restarted pod"},
            "cluster_findings": {},
        }

        result = await verification_service._check_once(state)

        assert result["resolved"] is True
        assert result.get("new_issues_detected") is False

    @pytest.mark.asyncio
    async def test_verify_fix_sets_new_issues_in_state(self, verification_service):
        """Test that verify_fix propagates new issues to state."""
        # Mock _check_once to return resolved with new issues
        async def mock_check_once(state):
            return {
                "resolved": True,
                "new_issues_detected": True,
                "new_issues": ["New issue found in deployment"],
                "evidence": ["Original fixed"],
            }

        verification_service._check_once = mock_check_once

        state = {
            "ticket_summary": "Test",
            "remediation_result": {"action_taken": "Test action"},
            "cluster_findings": {},
        }

        result_state = await verification_service.verify_fix(state)

        # Issue should be resolved but new issues should be tracked
        assert result_state["issue_resolved"] is True
        assert result_state.get("new_issues_detected") is True


class TestStateTracking:
    """Tests for state fields tracking post-fix loop."""

    def test_state_has_post_fix_loop_fields(self):
        """Test that AgentState has required fields for post-fix loop tracking."""
        from src.state import AgentState
        from src.supervisor import initialize_state

        # AgentState requires initialization to have the fields
        state = AgentState()
        state = initialize_state(state)

        # Required fields should exist with defaults after initialization
        assert state.get("remediation_loop_count") is not None
        assert state.get("max_remediation_loops") is not None
        assert state.get("new_issues_detected") is not None

    def test_state_initial_values(self):
        """Test initial values for post-fix loop fields."""
        from src.state import AgentState
        from src.supervisor import initialize_state

        state = AgentState()
        state = initialize_state(state)

        assert state.get("remediation_loop_count") == 0
        assert state.get("max_remediation_loops") == 3
        assert state.get("new_issues_detected") is False


class TestPostCommentWithFullHistory:
    """Tests for post_comment including full remediation history."""

    @pytest.mark.asyncio
    async def test_post_comment_includes_all_remediation_cycles(self):
        """Test that post_comment shows all remediation cycles in comment."""
        from src.agents.jira_agent import JiraAgent

        jira_tools = MagicMock()
        # Properly set up async mocks
        jira_tools.add_comment = AsyncMock(return_value={"id": "123"})
        jira_tools.get_comments = AsyncMock(return_value={"results": []})
        jira_tools.add_label = AsyncMock(return_value={"success": True})

        agent = JiraAgent(jira_tools)

        state = {
            "ticket_id": "TEST-1",
            "ticket_summary": "Test issue",
            "root_cause": "Test root cause",
            "remediation_history": [
                {"attempt": 1, "action": "Restart pod", "success": True},
                {"attempt": 2, "action": "Scale deployment", "success": True},
            ],
            "remediation_count": 2,
            "remediation_attempted": True,  # Required to show remediation section
            "issue_resolved": True,
            "cluster_findings": {},
        }

        result = await agent.post_comment(state)

        # The comment should include all remediation attempts
        # Check that add_comment was called
        assert jira_tools.add_comment.called

        # Get the call args - it's called with keyword arguments
        call_kwargs = jira_tools.add_comment.call_args.kwargs
        comment_content = call_kwargs.get("comment", "")

        assert "Restart pod" in comment_content
        assert "Scale deployment" in comment_content
