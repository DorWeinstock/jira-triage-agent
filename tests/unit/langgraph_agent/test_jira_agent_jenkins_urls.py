"""Tests for Jenkins URL extraction in JiraAgent.read_ticket()."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.jira_agent import JiraAgent


def _make_state(**kwargs):
    """Create a minimal AgentState-like dict."""
    base = {
        "ticket_id": "TEST-123",
        "messages": [],
        "jenkins_urls": [],
        "jenkins_findings": {},
    }
    base.update(kwargs)
    return base


def _make_ticket_data(description, comments_text=None):
    """Create a ticket_data dict mimicking MCP response with description."""
    content_parts = [
        f"Summary: Test ticket",
        f"**Description:** {description}",
        f"Priority: High",
        f"Status: Open",
    ]
    if comments_text:
        content_parts.append(f"**Comments:**\n{comments_text}")
    return {"content": "\n".join(content_parts)}


@pytest.fixture
def jira_agent():
    """Create JiraAgent with fully mocked tools and LLM."""
    with patch("src.agents.jira_agent.create_extraction_llm") as mock_llm_factory:
        mock_llm = MagicMock()
        # LLM returns a valid extraction response
        extraction_json = json.dumps({
            "summary": "Test ticket",
            "namespace": "production",
            "affected_resources": {
                "deployments": ["my-deploy"],
                "services": [],
                "pods": [],
                "configmaps": [],
                "secrets": [],
                "statefulsets": [],
                "daemonsets": [],
            },
            "symptoms": "pod crash",
            "error_messages": [],
        })
        mock_llm.ainvoke = AsyncMock(
            return_value=MagicMock(content=extraction_json)
        )
        mock_llm_factory.return_value = mock_llm

        mock_tools = MagicMock()
        mock_tools.get_ticket = AsyncMock()
        mock_tools.search_tickets = AsyncMock(return_value={"content": "[]"})
        # Mock history search LLM calls (search_tickets returns empty)
        agent = JiraAgent(mock_tools)
    return agent, mock_tools


class TestJiraAgentJenkinsURLExtraction:
    @pytest.mark.asyncio
    async def test_read_ticket_extracts_jenkins_urls(self, jira_agent):
        agent, mock_tools = jira_agent
        mock_tools.get_ticket.return_value = _make_ticket_data(
            "Build failed: https://jenkins.example.com/job/my-job/123/"
        )

        state = _make_state()
        result = await agent.read_ticket(state)
        assert len(result.get("jenkins_urls", [])) == 1
        assert "jenkins.example.com" in result["jenkins_urls"][0]

    @pytest.mark.asyncio
    async def test_read_ticket_no_jenkins_urls(self, jira_agent):
        agent, mock_tools = jira_agent
        mock_tools.get_ticket.return_value = _make_ticket_data(
            "Pod is crashing with OOMKilled"
        )

        state = _make_state()
        result = await agent.read_ticket(state)
        assert result.get("jenkins_urls", []) == []

    @pytest.mark.asyncio
    async def test_read_ticket_multiple_jenkins_urls(self, jira_agent):
        agent, mock_tools = jira_agent
        desc = (
            "Build 1: https://jenkins.example.com/job/job-a/1/ "
            "Build 2: https://jenkins.example.com/job/job-b/2/ "
            "Build 3: https://jenkins.example.com/job/job-c/3/"
        )
        mock_tools.get_ticket.return_value = _make_ticket_data(desc)

        state = _make_state()
        result = await agent.read_ticket(state)
        assert len(result.get("jenkins_urls", [])) == 3

    @pytest.mark.asyncio
    async def test_read_ticket_jenkins_urls_from_content(self, jira_agent):
        """Jenkins URL in the full content (e.g., comments) is also extracted."""
        agent, mock_tools = jira_agent
        # Description has no Jenkins URL, but raw content does
        ticket_data = {
            "content": (
                "Summary: Test ticket\n"
                "**Description:** No build link here\n"
                "Priority: High\n"
                "Status: Open\n"
                "**Comments:**\n"
                "See build: https://jenkins.example.com/job/ci/42/"
            )
        }
        mock_tools.get_ticket.return_value = ticket_data

        state = _make_state()
        result = await agent.read_ticket(state)
        assert len(result.get("jenkins_urls", [])) == 1
        assert "ci" in result["jenkins_urls"][0]
