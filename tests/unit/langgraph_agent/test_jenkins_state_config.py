"""Tests for Jenkins state and config extensions."""

import os
import pytest
from unittest.mock import patch

from src.state import AgentState
from src.config import Settings


class TestJenkinsStateFields:
    def test_state_jenkins_urls_default_empty(self):
        """AgentState should define jenkins_urls with default empty list."""
        # Verify the field is defined in the class annotations
        assert "jenkins_urls" in AgentState.__annotations__

    def test_state_jenkins_findings_default_empty(self):
        """AgentState should define jenkins_findings with default empty dict."""
        assert "jenkins_findings" in AgentState.__annotations__

    def test_state_dict_access_jenkins_urls(self):
        """jenkins_urls can be accessed via dict-style (TypedDict pattern)."""
        state: AgentState = {
            "ticket_id": "TEST-1",
            "messages": [],
            "jenkins_urls": ["https://jenkins.example.com/job/test/1/"],
            "jenkins_findings": {},
        }
        assert len(state["jenkins_urls"]) == 1
        assert "jenkins.example.com" in state["jenkins_urls"][0]

    def test_state_dict_access_jenkins_findings(self):
        """jenkins_findings can be accessed via dict-style (TypedDict pattern)."""
        state: AgentState = {
            "ticket_id": "TEST-1",
            "messages": [],
            "jenkins_urls": [],
            "jenkins_findings": {"failure_type": "test_failure", "root_cause": "NPE"},
        }
        assert state["jenkins_findings"]["failure_type"] == "test_failure"

    def test_state_get_default_for_jenkins_urls(self):
        """state.get() should work for jenkins_urls with default."""
        state: AgentState = {"ticket_id": "TEST-1", "messages": []}
        assert state.get("jenkins_urls", []) == []

    def test_state_get_default_for_jenkins_findings(self):
        """state.get() should work for jenkins_findings with default."""
        state: AgentState = {"ticket_id": "TEST-1", "messages": []}
        assert state.get("jenkins_findings", {}) == {}


class TestJenkinsConfigFields:
    def test_config_jenkins_mcp_endpoint_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
        assert settings.jenkins_mcp_endpoint == ""  # Empty default = disabled

    def test_config_jenkins_mcp_endpoint_env_override(self):
        with patch.dict(
            os.environ,
            {"JENKINS_MCP_ENDPOINT": "http://custom:9090/mcp/jenkins"},
            clear=True,
        ):
            settings = Settings()
        assert settings.jenkins_mcp_endpoint == "http://custom:9090/mcp/jenkins"

    def test_config_jenkins_console_log_max_bytes_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
        assert settings.jenkins_console_log_max_bytes == 100000
