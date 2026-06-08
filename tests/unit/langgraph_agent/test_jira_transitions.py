"""Tests for Jira ticket status transition tool wrappers."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx

from src.tools.jira_tools import JiraTools


@pytest.fixture
def jira_tools():
    return JiraTools(mcp_endpoint="http://fake:8080/mcp/jira")


def _mock_response(status_code=200, json_data=None, json_raises=False, text=""):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.text = text
    if json_raises:
        resp.json.side_effect = ValueError("No JSON")
    else:
        resp.json.return_value = json_data or {}
    return resp


@pytest.mark.asyncio
async def test_move_to_in_progress_success(jira_tools):
    """move_to_in_progress calls REST endpoint and returns parsed result."""
    resp = _mock_response(200, {"status": "success", "ticket_id": "TEST-123", "new_status": "In Progress"})

    with patch("src.tools.jira_tools.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await jira_tools.move_to_in_progress("TEST-123")

    assert result["success"] is True
    mock_client.post.assert_called_once_with("http://fake:8080/api/transition/TEST-123/in-progress")


@pytest.mark.asyncio
async def test_move_to_in_review_success(jira_tools):
    """move_to_in_review calls REST endpoint and returns parsed result."""
    resp = _mock_response(200, {"status": "success", "ticket_id": "TEST-456", "new_status": "In Review"})

    with patch("src.tools.jira_tools.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await jira_tools.move_to_in_review("TEST-456")

    assert result["success"] is True
    mock_client.post.assert_called_once_with("http://fake:8080/api/transition/TEST-456/in-review")


@pytest.mark.asyncio
async def test_move_to_in_progress_invalid_ticket_id(jira_tools):
    """move_to_in_progress returns error dict for empty ticket ID (fire-and-forget)."""
    result = await jira_tools.move_to_in_progress("")
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_move_to_in_review_invalid_ticket_id(jira_tools):
    """move_to_in_review returns error dict for invalid ticket ID (fire-and-forget)."""
    result = await jira_tools.move_to_in_review("bad-format")
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_move_to_in_progress_http_error(jira_tools):
    """move_to_in_progress returns failure on HTTP error."""
    resp = _mock_response(502, {"status": "error", "error": "jira unreachable"})

    with patch("src.tools.jira_tools.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await jira_tools.move_to_in_progress("TEST-123")

    assert result["success"] is False
    assert result["error"] == "jira unreachable"


@pytest.mark.asyncio
async def test_move_to_in_review_connection_error(jira_tools):
    """move_to_in_review returns failure on connection error."""
    with patch("src.tools.jira_tools.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await jira_tools.move_to_in_review("TEST-456")

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_transition_base_url_derivation(jira_tools):
    """_transition_base_url strips /mcp/jira suffix."""
    assert jira_tools._transition_base_url() == "http://fake:8080"


def test_transition_base_url_trailing_slash():
    """_transition_base_url handles trailing slash on MCP endpoint."""
    tools = JiraTools(mcp_endpoint="http://host:8080/mcp/jira/")
    assert tools._transition_base_url() == "http://host:8080"


@pytest.mark.asyncio
async def test_do_transition_non_json_response(jira_tools):
    """_do_transition handles non-JSON response gracefully."""
    resp = _mock_response(502, json_raises=True, text="<html>Bad Gateway</html>")

    with patch("src.tools.jira_tools.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await jira_tools.move_to_in_progress("TEST-123")

    assert result["success"] is False
    assert "error" in result
