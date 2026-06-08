"""Unit tests for kubectl command extraction from recommended actions."""

import pytest
from unittest.mock import MagicMock

from src.agents.jira_agent import JiraAgent


class TestExtractCommandBlock:
    """Test _extract_command_block edge cases."""

    @pytest.fixture
    def agent(self):
        """Create JiraAgent with mock tools."""
        return JiraAgent(MagicMock())

    def test_simple_kubectl_command(self, agent):
        """Test extraction of simple single-line kubectl command."""
        action = "Scale deployment order-service from 0 to 2 replicas using: kubectl scale deployment order-service --replicas=2 -n production"
        result = agent._extract_command_block(action, "production")

        assert "{code:bash}" in result
        assert "kubectl scale deployment order-service" in result
        assert "--replicas=2" in result

    def test_multiline_kubectl_command(self, agent):
        """Test extraction of multiline kubectl command with continuation."""
        action = """Run the following command to restart the deployment:
kubectl rollout restart deployment/order-service -n production
This will trigger a rolling update."""
        result = agent._extract_command_block(action, "production")

        assert "{code:bash}" in result
        assert "kubectl rollout restart deployment/order-service" in result

    def test_no_kubectl_command(self, agent):
        """Test fallback when no kubectl command is found."""
        action = "Restart the pod to pick up new configuration"
        result = agent._extract_command_block(action, "staging")

        assert "{code:bash}" in result
        assert "# Command details were in the recommended action:" in result
        assert "Restart the pod" in result

    def test_empty_action(self, agent):
        """Test handling of empty recommended action - same as None."""
        result = agent._extract_command_block("", "production")

        assert "{code:bash}" in result
        assert "# No recommended action provided" in result

    def test_kubectl_with_flags(self, agent):
        """Test extraction preserves flags and options."""
        action = "kubectl delete pod my-pod -n default --force --grace-period=0"
        result = agent._extract_command_block(action, "default")

        assert "kubectl delete pod my-pod" in result
        assert "--force" in result
        assert "--grace-period=0" in result

    def test_kubectl_lowercase(self, agent):
        """Test case-insensitive kubectl detection."""
        action = "Use KUBECTL to check: kubectl get pods"
        result = agent._extract_command_block(action, "default")

        assert "kubectl get pods" in result

    def test_long_action_truncated(self, agent):
        """Test that very long actions are truncated in fallback."""
        action = "x" * 300
        result = agent._extract_command_block(action, "default")

        assert "# Command details were in the recommended action:" in result
        assert "..." in result
        # Should truncate to ~200 chars
        assert len(result) < 350

    def test_kubectl_with_newlines(self, agent):
        """Test extraction stops at empty lines."""
        action = """Apply the fix:
kubectl apply -f deployment.yaml

Then verify with kubectl get pods"""
        result = agent._extract_command_block(action, "default")

        # Should extract first kubectl command, not both
        assert "kubectl apply -f deployment.yaml" in result

    def test_multiple_kubectl_commands(self, agent):
        """Test extraction handles multiple kubectl commands (takes first)."""
        action = """First run kubectl get pods -n prod
Then run kubectl describe pod my-pod"""
        result = agent._extract_command_block(action, "prod")

        # Should include the first kubectl command found
        assert "kubectl get pods" in result

    def test_none_action(self, agent):
        """Test handling of None as action - returns helpful message."""
        result = agent._extract_command_block(None, "default")

        assert "{code:bash}" in result
        assert "# No recommended action provided" in result
