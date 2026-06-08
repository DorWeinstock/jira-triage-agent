"""Unit tests for JiraAgent cluster detection logic."""

import pytest
from langgraph-agent.src.agents.jira_agent import JiraAgent


class TestClusterDetectionFromKeywords:
    """Test Phase 1: Keyword-based cluster detection."""

    @pytest.fixture
    def jira_agent(self, mocker):
        """Create JiraAgent with mocked dependencies."""
        mock_jira_tools = mocker.Mock()
        return JiraAgent(mock_jira_tools)

    def test_detect_hldc02_from_g2_label(self, jira_agent):
        """Should detect hldc02 from g2 label."""
        raw_fields = {
            "labels": ["g2", "urgent"],
            "summary": "Pod crash",
            "description": "Pod is crashing repeatedly",
        }
        cluster = jira_agent._detect_cluster_from_keywords(raw_fields)
        assert cluster == "hldc02"

    def test_detect_hldc02_from_hls2_summary(self, jira_agent):
        """Should detect hldc02 from hls2 in summary."""
        raw_fields = {
            "labels": [],
            "summary": "hls2 node failure",
            "description": "Node is down",
        }
        cluster = jira_agent._detect_cluster_from_keywords(raw_fields)
        assert cluster == "hldc02"

    def test_detect_hldc02_from_hldc02_description(self, jira_agent):
        """Should detect hldc02 from explicit cluster name."""
        raw_fields = {
            "labels": [],
            "summary": "Deployment issue",
            "description": "Deployment on hldc02 is failing",
        }
        cluster = jira_agent._detect_cluster_from_keywords(raw_fields)
        assert cluster == "hldc02"

    def test_detect_hldc03_from_g3_label(self, jira_agent):
        """Should detect hldc03 from g3 label."""
        raw_fields = {
            "labels": ["g3", "production"],
            "summary": "Service down",
            "description": "Service not responding",
        }
        cluster = jira_agent._detect_cluster_from_keywords(raw_fields)
        assert cluster == "hldc03"

    def test_detect_hldc03_from_hldc03_summary(self, jira_agent):
        """Should detect hldc03 from explicit cluster name."""
        raw_fields = {
            "labels": [],
            "summary": "hldc03 storage issue",
            "description": "PVC not binding",
        }
        cluster = jira_agent._detect_cluster_from_keywords(raw_fields)
        assert cluster == "hldc03"

    def test_no_cluster_detected_no_keywords(self, jira_agent):
        """Should return None when no cluster keywords found."""
        raw_fields = {
            "labels": ["bug", "high-priority"],
            "summary": "Application error",
            "description": "Application showing errors",
        }
        cluster = jira_agent._detect_cluster_from_keywords(raw_fields)
        assert cluster is None

    def test_case_insensitive_matching(self, jira_agent):
        """Should match keywords case-insensitively."""
        raw_fields = {
            "labels": ["G2", "URGENT"],
            "summary": "HLS2 Issue",
            "description": "Problem on HLDC02",
        }
        cluster = jira_agent._detect_cluster_from_keywords(raw_fields)
        assert cluster == "hldc02"

    def test_word_boundary_matching(self, jira_agent):
        """Should use word boundaries to avoid false matches."""
        # "legacy2" contains "g2" but shouldn't match
        raw_fields = {
            "labels": [],
            "summary": "legacy2 system update",
            "description": "Updating legacy2 configuration",
        }
        cluster = jira_agent._detect_cluster_from_keywords(raw_fields)
        # Should not match because g2 is not a word boundary match
        assert cluster is None


class TestClusterDiscoveryFromResources:
    """Test Phase 2: Resource discovery-based cluster detection."""

    @pytest.fixture
    def jira_agent(self, mocker):
        """Create JiraAgent with mocked dependencies."""
        mock_jira_tools = mocker.Mock()
        return JiraAgent(mock_jira_tools)

    @pytest.mark.asyncio
    async def test_discover_deployment_in_hldc02(self, jira_agent, mocker):
        """Should find deployment in hldc02 and return cluster name."""
        # Mock the K8s tools to simulate finding deployment in hldc02
        mock_k8s_tools_class = mocker.patch(
            "langgraph-agent.src.agents.jira_agent.ReadOnlyK8sTools"
        )
        mock_k8s_tools = mock_k8s_tools_class.return_value
        mock_k8s_tools.kubectl_get_all_namespaces.return_value = (
            "NAMESPACE   NAME          READY\n"
            "default     my-deployment 2/2\n"
        )

        affected_deployments = ["my-deployment"]
        cluster = await jira_agent._discover_cluster_from_resources(
            affected_deployments, []
        )

        assert cluster == "hldc02"
        # Verify it only tried hldc02 (didn't search hldc03)
        assert mock_k8s_tools_class.call_count == 1

    @pytest.mark.asyncio
    async def test_discover_deployment_in_hldc03_after_hldc02_search(
        self, jira_agent, mocker
    ):
        """Should fallback to hldc03 when not found in hldc02."""
        # Mock K8s tools to return empty from hldc02, deployment from hldc03
        mock_k8s_tools_class = mocker.patch(
            "langgraph-agent.src.agents.jira_agent.ReadOnlyK8sTools"
        )

        # First call (hldc02) returns empty, second call (hldc03) returns deployment
        mock_k8s_tools = mock_k8s_tools_class.return_value
        mock_k8s_tools.kubectl_get_all_namespaces.side_effect = [
            "No resources found",  # hldc02
            "NAMESPACE   NAME          READY\ndefault     my-deployment 2/2\n",  # hldc03
        ]

        affected_deployments = ["my-deployment"]
        cluster = await jira_agent._discover_cluster_from_resources(
            affected_deployments, []
        )

        assert cluster == "hldc03"
        assert mock_k8s_tools_class.call_count == 2

    @pytest.mark.asyncio
    async def test_no_cluster_found_deployment_missing(self, jira_agent, mocker):
        """Should return None when deployment not found in any cluster."""
        mock_k8s_tools_class = mocker.patch(
            "langgraph-agent.src.agents.jira_agent.ReadOnlyK8sTools"
        )
        mock_k8s_tools = mock_k8s_tools_class.return_value
        mock_k8s_tools.kubectl_get_all_namespaces.return_value = "No resources found"

        affected_deployments = ["nonexistent-deployment"]
        cluster = await jira_agent._discover_cluster_from_resources(
            affected_deployments, []
        )

        assert cluster is None
        # Should have tried both clusters
        assert mock_k8s_tools_class.call_count == 2

    @pytest.mark.asyncio
    async def test_no_deployments_provided(self, jira_agent):
        """Should return None immediately when no deployments to search."""
        cluster = await jira_agent._discover_cluster_from_resources([], [])
        assert cluster is None

    @pytest.mark.asyncio
    async def test_handles_k8s_tools_error_gracefully(self, jira_agent, mocker):
        """Should continue to next cluster if one fails."""
        mock_k8s_tools_class = mocker.patch(
            "langgraph-agent.src.agents.jira_agent.ReadOnlyK8sTools"
        )

        # First call (hldc02) raises error, second call (hldc03) succeeds
        mock_k8s_tools = mock_k8s_tools_class.return_value
        mock_k8s_tools.kubectl_get_all_namespaces.side_effect = [
            Exception("Connection timeout"),  # hldc02 fails
            "NAMESPACE   NAME          READY\ndefault     my-deployment 2/2\n",  # hldc03 succeeds
        ]

        affected_deployments = ["my-deployment"]
        cluster = await jira_agent._discover_cluster_from_resources(
            affected_deployments, []
        )

        assert cluster == "hldc03"
