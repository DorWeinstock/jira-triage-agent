"""Tests for JiraTools.update_issue."""

import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.jira_tools import JiraTools
from src.exceptions import ValidationError


@pytest.fixture
def jira_tools():
    return JiraTools(mcp_endpoint="http://fake:8080/mcp/jira")


# ---------------------------------------------------------------------------
# update_issue — JSON response path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_issue_success_json(jira_tools):
    """update_issue returns success=True when MCP responds with JSON status=success."""
    response = json.dumps({"status": "success", "ticket_id": "TEST-1"})

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        result = await jira_tools.update_issue("TEST-1", description="new description")

    assert result["success"] is True
    assert result["content"] == response


@pytest.mark.asyncio
async def test_update_issue_failure_json(jira_tools):
    """update_issue returns success=False when MCP responds with JSON status=error."""
    response = json.dumps({"status": "error", "message": "ticket not found"})

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        result = await jira_tools.update_issue("TEST-1", description="new description")

    assert result["success"] is False


# ---------------------------------------------------------------------------
# update_issue — plain-text response path (matches the actual Go MCP tool,
# which returns "Ticket X updated", not JSON)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_issue_text_success_logs_warning(jira_tools, caplog):
    """update_issue falls back to string detection for non-JSON and logs a warning."""
    response = "Ticket TEST-1 updated"

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        with caplog.at_level(logging.WARNING, logger="src.tools.jira_tools"):
            result = await jira_tools.update_issue("TEST-1", description="new description")

    assert result["success"] is True
    assert "falling back to string detection" in caplog.text


@pytest.mark.asyncio
async def test_update_issue_text_failure_logs_warning(jira_tools, caplog):
    """update_issue fallback returns success=False when 'updated' absent."""
    response = "Error: something went wrong"

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        with caplog.at_level(logging.WARNING, logger="src.tools.jira_tools"):
            result = await jira_tools.update_issue("TEST-1", description="new description")

    assert result["success"] is False
    assert "falling back to string detection" in caplog.text


# ---------------------------------------------------------------------------
# update_issue — edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_issue_none_response(jira_tools):
    """update_issue returns success=False when MCP returns None."""
    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=None)):
        result = await jira_tools.update_issue("TEST-1", description="new description")

    assert result["success"] is False


@pytest.mark.asyncio
async def test_update_issue_empty_ticket_raises(jira_tools):
    """update_issue raises ValidationError for empty ticket ID."""
    with pytest.raises(ValidationError):
        await jira_tools.update_issue("", description="new description")


@pytest.mark.asyncio
async def test_update_issue_neither_field_raises(jira_tools):
    """update_issue raises ValidationError when neither summary nor description is given."""
    with pytest.raises(ValidationError):
        await jira_tools.update_issue("TEST-1")


@pytest.mark.asyncio
async def test_update_issue_sends_both_fields(jira_tools):
    """update_issue passes both summary and description through to call_tool when given."""
    mock_call = AsyncMock(return_value="Ticket TEST-1 updated")
    with patch.object(jira_tools, "call_tool", new=mock_call):
        await jira_tools.update_issue("test-1", summary="New title", description="New body")

    mock_call.assert_awaited_once_with(
        "update_issue",
        {"ticket_id": "TEST-1", "summary": "New title", "description": "New body"},
    )


@pytest.mark.asyncio
async def test_update_issue_summary_only(jira_tools):
    """update_issue omits description from call_tool params when not given."""
    mock_call = AsyncMock(return_value="Ticket TEST-1 updated")
    with patch.object(jira_tools, "call_tool", new=mock_call):
        await jira_tools.update_issue("TEST-1", summary="New title")

    mock_call.assert_awaited_once_with(
        "update_issue",
        {"ticket_id": "TEST-1", "summary": "New title"},
    )
