"""Tests for JiraTools.remove_label."""

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
# remove_label — JSON response path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_label_success_json(jira_tools):
    """remove_label returns success=True when MCP responds with JSON status=success."""
    response = json.dumps({"status": "success", "ticket_id": "TEST-1", "label": "triage-in-progress"})

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        result = await jira_tools.remove_label("TEST-1", "triage-in-progress")

    assert result["success"] is True
    assert result["content"] == response


@pytest.mark.asyncio
async def test_remove_label_failure_json(jira_tools):
    """remove_label returns success=False when MCP responds with JSON status=error."""
    response = json.dumps({"status": "error", "message": "label not found"})

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        result = await jira_tools.remove_label("TEST-1", "triage-in-progress")

    assert result["success"] is False


# ---------------------------------------------------------------------------
# remove_label — plain-text response path (matches the actual Go MCP tool,
# which returns "Label 'X' removed successfully from Y", not JSON)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_label_text_success_logs_warning(jira_tools, caplog):
    """remove_label falls back to string detection for non-JSON and logs a warning."""
    response = "Label 'triage-in-progress' removed successfully from TEST-1"

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        with caplog.at_level(logging.WARNING, logger="src.tools.jira_tools"):
            result = await jira_tools.remove_label("TEST-1", "triage-in-progress")

    assert result["success"] is True
    assert "falling back to string detection" in caplog.text


@pytest.mark.asyncio
async def test_remove_label_text_failure_logs_warning(jira_tools, caplog):
    """remove_label fallback returns success=False when 'successfully' absent."""
    response = "Error: something went wrong"

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        with caplog.at_level(logging.WARNING, logger="src.tools.jira_tools"):
            result = await jira_tools.remove_label("TEST-1", "triage-in-progress")

    assert result["success"] is False
    assert "falling back to string detection" in caplog.text


# ---------------------------------------------------------------------------
# remove_label — edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_label_none_response(jira_tools):
    """remove_label returns success=False when MCP returns None."""
    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=None)):
        result = await jira_tools.remove_label("TEST-1", "triage-in-progress")

    assert result["success"] is False


@pytest.mark.asyncio
async def test_remove_label_empty_label_raises(jira_tools):
    """remove_label raises ValidationError for blank label."""
    with pytest.raises(ValidationError):
        await jira_tools.remove_label("TEST-1", "   ")


@pytest.mark.asyncio
async def test_remove_label_empty_ticket_raises(jira_tools):
    """remove_label raises ValidationError for empty ticket ID."""
    with pytest.raises(ValidationError):
        await jira_tools.remove_label("", "triage-in-progress")
