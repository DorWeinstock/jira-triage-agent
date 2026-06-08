"""Tests for Jenkins investigation node in supervisor graph."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_state(**kwargs):
    """Create a minimal AgentState-like dict."""
    base = {
        "ticket_id": "TEST-123",
        "ticket_summary": "Test issue",
        "ticket_description": "Pod crash loop",
        "ticket_labels": [],
        "ticket_priority": "High",
        "ticket_status": "Open",
        "ticket_components": [],
        "target_cluster": "local",
        "namespace": "production",
        "affected_resources": {"deployments": ["my-deploy"], "services": []},
        "cluster_findings": {},
        "root_cause": None,
        "recommended_action": None,
        "confidence_level": None,
        "remediation_count": 0,
        "remediation_history": [],
        "remediation_result": {},
        "remediation_attempted": False,
        "issue_resolved": False,
        "verification_evidence": [],
        "similar_tickets": [],
        "past_resolutions": [],
        "messages": [],
        "jenkins_urls": [],
        "jenkins_findings": {},
        "symptoms": None,
        "error_messages": [],
        "preventive_measures": [],
        "remediation_plan": None,
        "remediation_loop_count": 0,
        "max_remediation_loops": 3,
        "new_issues_detected": False,
        "previous_remediation_result": {},
        "new_issues": [],
        "resumed_from_checkpoint": False,
        "thread_id": None,
        "remediation_skipped_due_to_lock": False,
        "locked_by_ticket": None,
        "hitl_diagnosis_approved": None,
        "hitl_remediation_approved": None,
        "hitl_rejection_reason": None,
        "action_risk_level": None,
        "hitl_requested_at": None,
    }
    base.update(kwargs)
    return base


@pytest.fixture
def mock_jira_tools():
    tools = MagicMock()
    tools.get_ticket = AsyncMock(return_value={
        "content": "Summary: Test\n**Description:** Pod crash\nPriority: High\nStatus: Open"
    })
    tools.search_tickets = AsyncMock(return_value={"content": "[]"})
    tools.add_comment = AsyncMock(return_value={"id": "1"})
    tools.close = AsyncMock()
    tools.endpoint = "http://fake:8080/mcp/jira"
    tools.move_to_in_progress = AsyncMock(return_value={"success": True})
    tools.move_to_in_review = AsyncMock(return_value={"success": True})
    return tools


@pytest.fixture
def mock_k8s_tools():
    tools = MagicMock()
    tools.endpoint = "http://fake:8080/mcp/k8s"
    tools.close = AsyncMock()
    return tools


@pytest.fixture
def mock_jenkins_tools():
    tools = MagicMock()
    tools.get_build_info = AsyncMock(return_value="BUILD INFO:\n  Result: FAILURE")
    tools.get_console_log = AsyncMock(return_value="CONSOLE LOG:\n[ERROR] NPE")
    tools.get_parent_build_info = AsyncMock(return_value="No upstream trigger found")
    tools.close = AsyncMock()
    tools.endpoint = "http://fake:8080/mcp/jenkins"
    return tools


class TestJenkinsSupervisorWiring:
    def test_graph_has_investigate_jenkins_node(
        self, mock_jira_tools, mock_k8s_tools, mock_jenkins_tools
    ):
        """Graph should contain investigate_jenkins node when jenkins_tools provided."""
        with patch("src.supervisor.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                k8s_mcp_endpoint="http://fake:8080/mcp/k8s",
                max_remediation_attempts=2,
                remediation_retry_delay=0,
                hitl_enabled=False,
            )
            from src.supervisor import create_conditional_supervisor_graph
            graph = create_conditional_supervisor_graph(
                mock_jira_tools, mock_k8s_tools,
                jenkins_tools=mock_jenkins_tools,
            )
        # The compiled graph should have investigate_jenkins node
        node_names = list(graph.get_graph().nodes.keys())
        assert "investigate_jenkins" in node_names

    def test_graph_works_without_jenkins_tools(
        self, mock_jira_tools, mock_k8s_tools
    ):
        """Graph should compile and work without jenkins_tools (backward compat)."""
        with patch("src.supervisor.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                k8s_mcp_endpoint="http://fake:8080/mcp/k8s",
                max_remediation_attempts=2,
                remediation_retry_delay=0,
                hitl_enabled=False,
            )
            from src.supervisor import create_conditional_supervisor_graph
            graph = create_conditional_supervisor_graph(
                mock_jira_tools, mock_k8s_tools,
                jenkins_tools=None,
            )
        node_names = list(graph.get_graph().nodes.keys())
        assert "investigate_jenkins" not in node_names
        # Original nodes should still be present
        assert "read_ticket" in node_names
        assert "investigate_cluster" in node_names
        assert "diagnose" in node_names

    def test_investigate_jenkins_routes_to_investigate_cluster(
        self, mock_jira_tools, mock_k8s_tools, mock_jenkins_tools
    ):
        """investigate_jenkins should have an edge to investigate_cluster."""
        with patch("src.supervisor.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                k8s_mcp_endpoint="http://fake:8080/mcp/k8s",
                max_remediation_attempts=2,
                remediation_retry_delay=0,
                hitl_enabled=False,
            )
            from src.supervisor import create_conditional_supervisor_graph
            graph = create_conditional_supervisor_graph(
                mock_jira_tools, mock_k8s_tools,
                jenkins_tools=mock_jenkins_tools,
            )
        # Check the graph structure: investigate_jenkins -> investigate_cluster
        graph_dict = graph.get_graph()
        # Find edges from investigate_jenkins
        jenkins_edges = [
            e for e in graph_dict.edges
            if e.source == "investigate_jenkins"
        ]
        assert len(jenkins_edges) > 0
        assert any(e.target == "investigate_cluster" for e in jenkins_edges)

    def test_read_ticket_routes_to_investigate_jenkins_when_present(
        self, mock_jira_tools, mock_k8s_tools, mock_jenkins_tools
    ):
        """When jenkins_tools provided, read_ticket should route to investigate_jenkins
        instead of directly to investigate_cluster."""
        with patch("src.supervisor.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                k8s_mcp_endpoint="http://fake:8080/mcp/k8s",
                max_remediation_attempts=2,
                remediation_retry_delay=0,
                hitl_enabled=False,
            )
            from src.supervisor import create_conditional_supervisor_graph
            graph = create_conditional_supervisor_graph(
                mock_jira_tools, mock_k8s_tools,
                jenkins_tools=mock_jenkins_tools,
            )
        graph_dict = graph.get_graph()
        # Find conditional edges from read_ticket
        read_ticket_edges = [
            e for e in graph_dict.edges
            if e.source == "read_ticket"
        ]
        # One of the targets should be investigate_jenkins (not direct investigate_cluster)
        targets = {e.target for e in read_ticket_edges}
        assert "investigate_jenkins" in targets
