"""Tests for JenkinsInvestigator agent."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.jenkins_investigator import JenkinsInvestigator
from src.exceptions import ToolError


def _make_state(**kwargs):
    """Create a minimal AgentState-like dict."""
    base = {
        "ticket_id": "TEST-123",
        "jenkins_urls": [],
        "jenkins_findings": {},
        "messages": [],
    }
    base.update(kwargs)
    return base


@pytest.fixture
def mock_tools():
    tools = MagicMock()
    tools.get_build_info = AsyncMock(return_value="BUILD INFO:\n  Result: FAILURE\n  Duration: 45s")
    tools.get_console_log = AsyncMock(return_value="CONSOLE LOG:\n[ERROR] NullPointerException")
    tools.get_parent_build_info = AsyncMock(return_value="UPSTREAM CAUSE:\n  Triggered by: parent-job #456")
    return tools


@pytest.fixture
def investigator(mock_tools):
    with patch("src.agents.jenkins_investigator.create_extraction_llm") as mock_llm_factory:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(
            content=json.dumps({
                "failure_type": "test_failure",
                "root_cause": "Unit test X failed",
                "error_snippets": ["NullPointerException at TestClass.java:42"],
                "console_log_summary": "Build failed due to test failure",
            })
        ))
        mock_llm_factory.return_value = mock_llm
        inv = JenkinsInvestigator(mock_tools)
    return inv


class TestJenkinsInvestigator:
    @pytest.mark.asyncio
    async def test_run_no_jenkins_urls_noop(self, investigator, mock_tools):
        state = _make_state(jenkins_urls=[])
        result = await investigator.run(state)
        assert result["jenkins_findings"] == {}
        mock_tools.get_build_info.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_success_classifies_failure(self, investigator, mock_tools):
        state = _make_state(jenkins_urls=["https://jenkins.example.com/job/my-job/123/"])
        result = await investigator.run(state)
        findings = result["jenkins_findings"]
        assert findings["failure_type"] == "test_failure"
        assert findings["root_cause"] == "Unit test X failed"
        assert "NullPointerException" in findings["error_snippets"][0]
        assert findings["console_log_summary"] is not None
        # raw_console_log should be cleared after classification to save state size
        assert findings["raw_console_log"] is None
        mock_tools.get_build_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_first_url_only(self, investigator, mock_tools):
        urls = [
            "https://jenkins.example.com/job/job-a/1/",
            "https://jenkins.example.com/job/job-b/2/",
            "https://jenkins.example.com/job/job-c/3/",
        ]
        state = _make_state(jenkins_urls=urls)
        await investigator.run(state)
        # Only the first URL should be used
        mock_tools.get_build_info.assert_called_once()
        call_args = mock_tools.get_build_info.call_args[0][0]
        assert "job-a" in call_args

    @pytest.mark.asyncio
    async def test_run_tool_error_graceful(self, investigator, mock_tools):
        mock_tools.get_build_info = AsyncMock(side_effect=ToolError("connection refused"))
        state = _make_state(jenkins_urls=["https://jenkins.example.com/job/my-job/123/"])
        result = await investigator.run(state)
        findings = result["jenkins_findings"]
        assert "error" in findings
        assert "connection refused" in findings["error"]

    @pytest.mark.asyncio
    async def test_run_llm_classification_fails_graceful(self, investigator, mock_tools):
        investigator.llm.ainvoke = AsyncMock(return_value=MagicMock(content="not json"))
        state = _make_state(jenkins_urls=["https://jenkins.example.com/job/my-job/123/"])
        result = await investigator.run(state)
        findings = result["jenkins_findings"]
        # Should still have build_info from tools
        assert findings.get("build_info") is not None
        # Classification should fall back
        assert findings.get("failure_type") == "unknown"

    @pytest.mark.asyncio
    async def test_run_console_log_error_continues(self, investigator, mock_tools):
        mock_tools.get_console_log = AsyncMock(side_effect=ToolError("timeout"))
        state = _make_state(jenkins_urls=["https://jenkins.example.com/job/my-job/123/"])
        result = await investigator.run(state)
        findings = result["jenkins_findings"]
        # Build info should still be present
        assert findings.get("build_info") is not None
        # Console log should be None (error)
        assert findings.get("raw_console_log") is None

    @pytest.mark.asyncio
    async def test_run_parent_build_not_found(self, investigator, mock_tools):
        mock_tools.get_parent_build_info = AsyncMock(return_value="No upstream trigger found")
        state = _make_state(jenkins_urls=["https://jenkins.example.com/job/my-job/123/"])
        result = await investigator.run(state)
        findings = result["jenkins_findings"]
        assert findings["parent_build"] == "No upstream trigger found"

    @pytest.mark.asyncio
    async def test_jenkins_findings_keys(self, investigator, mock_tools):
        state = _make_state(jenkins_urls=["https://jenkins.example.com/job/my-job/123/"])
        result = await investigator.run(state)
        findings = result["jenkins_findings"]
        expected_keys = {"url", "build_info", "failure_type", "root_cause",
                        "error_snippets", "console_log_summary", "parent_build",
                        "raw_console_log"}
        assert expected_keys.issubset(set(findings.keys()))
