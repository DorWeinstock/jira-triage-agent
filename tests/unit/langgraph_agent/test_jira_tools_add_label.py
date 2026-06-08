"""Tests for JiraTools.add_label, add_comment fallback, and explicit rest_base_url."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.jira_tools import JiraTools
from src.exceptions import ValidationError


@pytest.fixture
def jira_tools():
    return JiraTools(mcp_endpoint="http://fake:8080/mcp/jira")


# ---------------------------------------------------------------------------
# add_label — JSON response path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_label_success_json(jira_tools):
    """add_label returns success=True when MCP responds with JSON status=success."""
    response = json.dumps({"status": "success", "ticket_id": "TEST-1", "label": "foo"})

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        result = await jira_tools.add_label("TEST-1", "foo")

    assert result["success"] is True
    assert result["content"] == response


@pytest.mark.asyncio
async def test_add_label_failure_json(jira_tools):
    """add_label returns success=False when MCP responds with JSON status=error."""
    response = json.dumps({"status": "error", "message": "label already exists"})

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        result = await jira_tools.add_label("TEST-1", "foo")

    assert result["success"] is False


# ---------------------------------------------------------------------------
# add_label — legacy plain-text fallback path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_label_legacy_text_success_logs_warning(jira_tools, caplog):
    """add_label falls back to string detection for non-JSON and logs a warning."""
    response = "Label added successfully to TEST-1"

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        with caplog.at_level(logging.WARNING, logger="src.tools.jira_tools"):
            result = await jira_tools.add_label("TEST-1", "foo")

    assert result["success"] is True
    assert "falling back to string detection" in caplog.text


@pytest.mark.asyncio
async def test_add_label_legacy_text_failure_logs_warning(jira_tools, caplog):
    """add_label fallback returns success=False when 'successfully' absent."""
    response = "Error: something went wrong"

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        with caplog.at_level(logging.WARNING, logger="src.tools.jira_tools"):
            result = await jira_tools.add_label("TEST-1", "foo")

    assert result["success"] is False
    assert "falling back to string detection" in caplog.text


# ---------------------------------------------------------------------------
# add_label — edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_label_none_response(jira_tools):
    """add_label returns success=False when MCP returns None."""
    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=None)):
        result = await jira_tools.add_label("TEST-1", "foo")

    assert result["success"] is False


@pytest.mark.asyncio
async def test_add_label_empty_label_raises(jira_tools):
    """add_label raises ValidationError for blank label."""
    with pytest.raises(ValidationError):
        await jira_tools.add_label("TEST-1", "   ")


@pytest.mark.asyncio
async def test_add_label_empty_ticket_raises(jira_tools):
    """add_label raises ValidationError for empty ticket ID."""
    with pytest.raises(ValidationError):
        await jira_tools.add_label("", "some-label")


# ---------------------------------------------------------------------------
# add_comment — fallback warning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_comment_non_json_logs_warning(jira_tools, caplog):
    """add_comment logs a warning when MCP returns non-JSON and falls back."""
    response = "Comment added successfully"

    with patch.object(jira_tools, "call_tool", new=AsyncMock(return_value=response)):
        with caplog.at_level(logging.WARNING, logger="src.tools.jira_tools"):
            result = await jira_tools.add_comment("TEST-1", "hello world")

    assert "falling back to string detection" in caplog.text
    assert result["success"] is True


# ---------------------------------------------------------------------------
# _transition_base_url — explicit rest_base_url takes priority
# ---------------------------------------------------------------------------

def test_transition_base_url_explicit_takes_priority():
    """rest_base_url param overrides MCP endpoint derivation."""
    tools = JiraTools(
        mcp_endpoint="http://fake:8080/mcp/jira",
        rest_base_url="http://real-host:9090",
    )
    assert tools._transition_base_url() == "http://real-host:9090"


def test_transition_base_url_explicit_trailing_slash_stripped():
    """Trailing slash on explicit rest_base_url is stripped."""
    tools = JiraTools(
        mcp_endpoint="http://fake:8080/mcp/jira",
        rest_base_url="http://real-host:9090/",
    )
    assert tools._transition_base_url() == "http://real-host:9090"


def test_transition_base_url_falls_back_to_derivation():
    """Without rest_base_url, derivation from MCP endpoint still works."""
    tools = JiraTools(mcp_endpoint="http://host:8080/mcp/jira")
    assert tools._transition_base_url() == "http://host:8080"
