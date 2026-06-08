"""Unit tests for Jira comment with lock skip info."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.jira_agent import JiraAgent
from src.state import AgentState


class TestJiraLockComment:
    """Test Jira comment includes lock skip information."""

    @pytest.fixture
    def mock_jira_tools(self):
        """Create mock Jira tools."""
        tools = MagicMock()
        tools.call_tool = AsyncMock(return_value={"id": "12345"})
        return tools

    @pytest.mark.asyncio
    async def test_post_comment_includes_lock_skip_warning(self, mock_jira_tools):
        """Test that lock skip is mentioned in Jira comment."""
        agent = JiraAgent(mock_jira_tools)

        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Missing ConfigMap",
            recommended_action="Create the ConfigMap",
            confidence_level="High",
            remediation_skipped_due_to_lock=True,
            locked_by_ticket="PROJ-456",
        )

        with patch.object(agent, "_format_comment") as mock_format:
            mock_format.return_value = "formatted comment"
            await agent.post_comment(state)

            # Verify _format_comment was called with state
            mock_format.assert_called_once()
            call_state = mock_format.call_args[0][0]
            assert call_state.get("remediation_skipped_due_to_lock") is True

    def test_format_comment_includes_lock_info(self, mock_jira_tools):
        """Test comment formatting includes lock skip info."""
        agent = JiraAgent(mock_jira_tools)

        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Missing ConfigMap",
            recommended_action="Create the ConfigMap",
            confidence_level="High",
            remediation_skipped_due_to_lock=True,
            locked_by_ticket="PROJ-456",
            preventive_measures=["Add monitoring"],
        )

        comment = agent._format_comment(state)

        assert "PROJ-456" in comment
        assert "lock" in comment.lower() or "concurrent" in comment.lower()

    def test_format_comment_uses_fallback_when_locked_by_none(self, mock_jira_tools):
        """Test fallback when locked_by_ticket is None."""
        agent = JiraAgent(mock_jira_tools)

        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Missing ConfigMap",
            recommended_action="Create the ConfigMap",
            confidence_level="High",
            remediation_skipped_due_to_lock=True,
            locked_by_ticket=None,  # Explicitly None
        )

        comment = agent._format_comment(state)

        assert "another ticket" in comment
        assert "lock" in comment.lower() or "concurrent" in comment.lower()

    def test_format_comment_no_lock_warning_when_not_skipped(self, mock_jira_tools):
        """Test no lock warning when remediation was not skipped."""
        agent = JiraAgent(mock_jira_tools)

        state = AgentState(
            ticket_id="PROJ-123",
            root_cause="Missing ConfigMap",
            recommended_action="Create the ConfigMap",
            confidence_level="High",
            remediation_skipped_due_to_lock=False,
        )

        comment = agent._format_comment(state)

        assert "Remediation Skipped Due to Lock" not in comment
