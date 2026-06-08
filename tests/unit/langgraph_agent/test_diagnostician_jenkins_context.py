"""Tests for Jenkins context in diagnostician's _build_context()."""

import pytest
from unittest.mock import MagicMock, patch

from src.agents.diagnostician import Diagnostician


def _make_state(**kwargs):
    """Create a minimal AgentState-like dict for diagnostician."""
    base = {
        "ticket_id": "TEST-123",
        "ticket_summary": "Test issue",
        "ticket_description": "Pod crash loop",
        "ticket_labels": [],
        "ticket_priority": "High",
        "affected_resources": {"deployments": ["my-deploy"], "services": []},
        "cluster_findings": {
            "resources": {"pods": "pod-info"},
            "logs": "some logs",
            "events": ["event1"],
            "preliminary_findings": "found issue",
        },
        "similar_tickets": [],
        "past_resolutions": [],
        "remediation_count": 0,
        "namespace": "production",
        "jenkins_findings": {},
        "messages": [],
    }
    base.update(kwargs)
    return base


@pytest.fixture
def diagnostician():
    with patch("src.agents.diagnostician.create_diagnosis_llm"):
        diag = Diagnostician(remediation_agent=MagicMock())
    return diag


class TestDiagnosticianJenkinsContext:
    def test_build_context_includes_jenkins_when_present(self, diagnostician):
        """When jenkins_findings has data, _build_context should include Jenkins section."""
        state = _make_state(jenkins_findings={
            "failure_type": "test_failure",
            "root_cause": "Unit test X failed",
            "error_snippets": ["NPE at TestClass.java:42"],
            "console_log_summary": "Build failed due to test failure",
            "build_info": "BUILD INFO:\n  Result: FAILURE",
            "parent_build": "No upstream trigger found",
        })
        context = diagnostician._build_context(state)
        assert "Jenkins Failure Context" in context
        assert "test_failure" in context

    def test_build_context_no_jenkins_when_empty(self, diagnostician):
        """Empty jenkins_findings should not produce Jenkins section."""
        state = _make_state(jenkins_findings={})
        context = diagnostician._build_context(state)
        assert "Jenkins Failure Context" not in context

    def test_build_context_no_jenkins_when_absent(self, diagnostician):
        """Missing jenkins_findings key should not produce Jenkins section."""
        state = _make_state()
        del state["jenkins_findings"]
        context = diagnostician._build_context(state)
        assert "Jenkins Failure Context" not in context

    def test_build_context_jenkins_failure_type(self, diagnostician):
        """Failure type should appear in context."""
        state = _make_state(jenkins_findings={
            "failure_type": "compilation_error",
            "root_cause": "Missing import",
            "error_snippets": [],
            "console_log_summary": "Compilation failed",
            "build_info": "BUILD INFO",
            "parent_build": None,
        })
        context = diagnostician._build_context(state)
        assert "compilation_error" in context

    def test_build_context_jenkins_root_cause(self, diagnostician):
        """Root cause should appear in context."""
        state = _make_state(jenkins_findings={
            "failure_type": "infrastructure",
            "root_cause": "Docker daemon not responding",
            "error_snippets": ["Cannot connect to Docker daemon"],
            "console_log_summary": "Infrastructure failure",
            "build_info": "BUILD INFO",
            "parent_build": None,
        })
        context = diagnostician._build_context(state)
        assert "Docker daemon not responding" in context

    def test_build_context_jenkins_error_only(self, diagnostician):
        """When jenkins_findings has only error key, show error message."""
        state = _make_state(jenkins_findings={
            "error": "connection refused",
            "url": "https://jenkins.example.com/job/test/1/",
        })
        context = diagnostician._build_context(state)
        assert "Jenkins Failure Context" in context
        assert "connection refused" in context
