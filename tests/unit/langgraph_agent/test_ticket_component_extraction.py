"""Test that current ticket's Jira components are extracted and stored in state."""

import pytest
from unittest.mock import AsyncMock, Mock, patch


class TestTicketComponentExtraction:
    """Tests for extracting Jira components from current ticket."""

    @pytest.fixture
    def jira_agent(self):
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.jira_agent import JiraAgent

        mock_tools = Mock()
        mock_tools.get_ticket = AsyncMock()
        mock_tools.search_tickets = AsyncMock()
        agent = JiraAgent(mock_tools)
        return agent

    def test_parse_mcp_response_with_components(self, jira_agent):
        """Components line in MCP response should be parsed."""
        content = (
            "**Ticket Information**\n\n"
            "Key: SP-100\n"
            "Summary: order crash\n"
            "Status: Open\n"
            "Priority: High\n"
            "Components: order-service, payments\n"
            "\n**Description:**\nPods crashing"
        )

        result = jira_agent._parse_mcp_formatted_response(content)

        assert result["components"] == ["order-service", "payments"]

    def test_parse_mcp_response_no_components(self, jira_agent):
        """Missing Components line should return empty list."""
        content = (
            "**Ticket Information**\n\n"
            "Key: SP-101\n"
            "Summary: generic issue\n"
            "Status: Open\n"
            "Priority: Medium\n"
            "\n**Description:**\nSomething happened"
        )

        result = jira_agent._parse_mcp_formatted_response(content)

        assert result["components"] == []

    def test_parse_jira_api_response_with_components(self, jira_agent):
        """Jira API nested components should be extracted."""
        fields = {
            "summary": "order crash",
            "description": "Pods crashing",
            "labels": [],
            "priority": {"name": "High"},
            "status": {"name": "Open"},
            "components": [
                {"name": "order-service"},
                {"name": "payments"},
            ],
        }

        result = jira_agent._parse_jira_api_response(fields)

        assert result["components"] == ["order-service", "payments"]

    def test_parse_jira_api_response_no_components(self, jira_agent):
        """Missing components in Jira API should return empty list."""
        fields = {
            "summary": "generic issue",
            "description": "Something",
            "labels": [],
            "priority": {"name": "Medium"},
            "status": {"name": "Open"},
        }

        result = jira_agent._parse_jira_api_response(fields)

        assert result["components"] == []
