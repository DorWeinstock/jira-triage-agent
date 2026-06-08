"""Tests for JenkinsTools MCP client."""

import pytest
from unittest.mock import AsyncMock, patch

from src.tools.jenkins_tools import JenkinsTools
from src.exceptions import ValidationError, ToolError


@pytest.fixture
def jenkins_tools():
    """Create JenkinsTools with mock endpoint."""
    with patch("src.tools.base_mcp_client.get_settings") as mock_settings:
        mock_settings.return_value.mcp_connection_timeout = 30.0
        mock_settings.return_value.mcp_sse_read_timeout = 600.0
        tools = JenkinsTools(endpoint="http://fake:8080/mcp/jenkins")
    return tools


class TestJenkinsToolsGetBuildInfo:
    @pytest.mark.asyncio
    async def test_success(self, jenkins_tools):
        jenkins_tools.call_tool = AsyncMock(return_value="BUILD INFO:\n  Result: FAILURE")
        result = await jenkins_tools.get_build_info("https://jenkins.example.com/job/my-job/123/")
        assert result == "BUILD INFO:\n  Result: FAILURE"
        jenkins_tools.call_tool.assert_called_once_with(
            "jenkins_get_build_info",
            {"jenkins_url": "https://jenkins.example.com/job/my-job/123/"},
        )

    @pytest.mark.asyncio
    async def test_empty_url_raises(self, jenkins_tools):
        with pytest.raises(ValidationError):
            await jenkins_tools.get_build_info("")

    @pytest.mark.asyncio
    async def test_whitespace_url_raises(self, jenkins_tools):
        with pytest.raises(ValidationError):
            await jenkins_tools.get_build_info("   ")


class TestJenkinsToolsGetConsoleLog:
    @pytest.mark.asyncio
    async def test_success(self, jenkins_tools):
        jenkins_tools.call_tool = AsyncMock(return_value="CONSOLE LOG:\nBuild output...")
        result = await jenkins_tools.get_console_log("https://jenkins.example.com/job/my-job/123/")
        assert "Build output" in result
        jenkins_tools.call_tool.assert_called_once_with(
            "jenkins_get_console_log",
            {"jenkins_url": "https://jenkins.example.com/job/my-job/123/", "max_bytes": 100000},
        )

    @pytest.mark.asyncio
    async def test_custom_max_bytes(self, jenkins_tools):
        jenkins_tools.call_tool = AsyncMock(return_value="log")
        await jenkins_tools.get_console_log("https://jenkins.example.com/job/my-job/123/", max_bytes=50000)
        jenkins_tools.call_tool.assert_called_once_with(
            "jenkins_get_console_log",
            {"jenkins_url": "https://jenkins.example.com/job/my-job/123/", "max_bytes": 50000},
        )

    @pytest.mark.asyncio
    async def test_default_max_bytes(self, jenkins_tools):
        jenkins_tools.call_tool = AsyncMock(return_value="log")
        await jenkins_tools.get_console_log("https://jenkins.example.com/job/my-job/123/")
        args = jenkins_tools.call_tool.call_args[0][1]
        assert args["max_bytes"] == 100000

    @pytest.mark.asyncio
    async def test_empty_url_raises(self, jenkins_tools):
        with pytest.raises(ValidationError):
            await jenkins_tools.get_console_log("")


class TestJenkinsToolsGetParentBuildInfo:
    @pytest.mark.asyncio
    async def test_success(self, jenkins_tools):
        jenkins_tools.call_tool = AsyncMock(return_value="UPSTREAM CAUSE:\n  Triggered by: parent-job #456")
        result = await jenkins_tools.get_parent_build_info("https://jenkins.example.com/job/my-job/123/")
        assert "parent-job" in result

    @pytest.mark.asyncio
    async def test_empty_url_raises(self, jenkins_tools):
        with pytest.raises(ValidationError):
            await jenkins_tools.get_parent_build_info("")


class TestJenkinsToolsErrorPropagation:
    @pytest.mark.asyncio
    async def test_tool_error_propagated(self, jenkins_tools):
        jenkins_tools.call_tool = AsyncMock(side_effect=ToolError("connection failed"))
        with pytest.raises(ToolError, match="connection failed"):
            await jenkins_tools.get_build_info("https://jenkins.example.com/job/my-job/123/")
