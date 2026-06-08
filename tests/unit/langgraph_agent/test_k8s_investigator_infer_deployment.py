"""Tests for K8sInvestigator's deployment status inference from pods."""

import pytest
from unittest.mock import MagicMock

from src.agents.k8s_investigator import K8sInvestigator


class TestInferDeploymentStatusFromPods:
    """Test _infer_deployment_status_from_pods method."""

    @pytest.fixture
    def investigator(self):
        """Create K8sInvestigator with mocked tools."""
        mock_tools = MagicMock()
        return K8sInvestigator(mock_tools)

    def test_no_pods_output_returns_scaled_to_zero(self, investigator):
        """When pods output is empty, infer deployment has 0 replicas."""
        result = investigator._infer_deployment_status_from_pods("", "my-app")

        assert "DEPLOYMENT STATUS: my-app" in result
        assert "Replicas: 0/0" in result
        assert "SCALED TO ZERO" in result
        assert "kubectl scale" in result

    def test_no_resources_found_returns_scaled_to_zero(self, investigator):
        """When 'No resources found' message, infer deployment has 0 replicas."""
        result = investigator._infer_deployment_status_from_pods(
            "No resources found in production namespace.",
            "test-deployment"
        )

        assert "DEPLOYMENT STATUS: test-deployment" in result
        assert "Replicas: 0/0" in result
        assert "NO PODS EXIST" in result

    def test_no_matching_pods_returns_scaled_to_zero(self, investigator):
        """When pods exist but none match deployment, infer 0 replicas."""
        pods_output = """NAME                                READY   STATUS    RESTARTS   AGE
other-app-abc123-xyz99              1/1     Running   0          5h
different-service-def456-abc12      1/1     Running   0          3h"""

        result = investigator._infer_deployment_status_from_pods(
            pods_output, "my-deployment"
        )

        assert "DEPLOYMENT STATUS: my-deployment" in result
        assert "Replicas: 0/0" in result
        assert "NO PODS FOUND" in result

    def test_all_pods_running_returns_healthy(self, investigator):
        """When all deployment pods are running, return HEALTHY status."""
        pods_output = """NAME                                READY   STATUS    RESTARTS   AGE
my-app-abc123-xyz99                 1/1     Running   0          5h
my-app-def456-abc12                 1/1     Running   0          3h"""

        result = investigator._infer_deployment_status_from_pods(
            pods_output, "my-app"
        )

        assert "DEPLOYMENT STATUS: my-app" in result
        assert "Replicas: 2/2 ready" in result
        assert "HEALTHY" in result
        assert "my-app-abc123-xyz99" in result

    def test_some_pods_in_error_returns_degraded(self, investigator):
        """When some pods are in error state, return DEGRADED status."""
        pods_output = """NAME                                READY   STATUS             RESTARTS   AGE
my-app-abc123-xyz99                 1/1     Running            0          5h
my-app-def456-abc12                 0/1     CrashLoopBackOff   5          3h"""

        result = investigator._infer_deployment_status_from_pods(
            pods_output, "my-app"
        )

        assert "DEPLOYMENT STATUS: my-app" in result
        assert "Replicas: 1/2 ready" in result
        assert "DEGRADED" in result
        assert "CrashLoopBackOff" in result

    def test_pods_not_ready_returns_transitioning(self, investigator):
        """When pods exist but not ready, return TRANSITIONING status."""
        pods_output = """NAME                                READY   STATUS    RESTARTS   AGE
my-app-abc123-xyz99                 0/1     Running   0          1m
my-app-def456-abc12                 0/1     Running   0          1m"""

        result = investigator._infer_deployment_status_from_pods(
            pods_output, "my-app"
        )

        assert "DEPLOYMENT STATUS: my-app" in result
        assert "TRANSITIONING" in result
        assert "2 pod(s) not ready" in result

    def test_case_insensitive_matching(self, investigator):
        """Deployment name matching should be case-insensitive."""
        pods_output = """NAME                                READY   STATUS    RESTARTS   AGE
My-App-abc123-xyz99                 1/1     Running   0          5h"""

        result = investigator._infer_deployment_status_from_pods(
            pods_output, "my-app"
        )

        assert "DEPLOYMENT STATUS: my-app" in result
        assert "Replicas: 1/1 ready" in result
        assert "HEALTHY" in result

    def test_multiple_error_states(self, investigator):
        """Handle multiple different error states correctly."""
        pods_output = """NAME                                READY   STATUS              RESTARTS   AGE
api-server-abc123-xyz99             0/1     Error               0          5h
api-server-def456-abc12             0/1     ImagePullBackOff    0          3h
api-server-ghi789-def34             0/1     Pending             0          1h"""

        result = investigator._infer_deployment_status_from_pods(
            pods_output, "api-server"
        )

        assert "DEPLOYMENT STATUS: api-server" in result
        assert "Replicas: 0/3 ready" in result
        assert "DEGRADED" in result
        assert "3 pod(s) in error state" in result

    def test_limit_pods_displayed(self, investigator):
        """When many pods, only show first 5 in status."""
        # Generate 10 pods
        pod_lines = ["NAME                                READY   STATUS    RESTARTS   AGE"]
        for i in range(10):
            pod_lines.append(f"big-app-hash{i:02d}-pod{i:02d}               1/1     Running   0          {i}h")
        pods_output = "\n".join(pod_lines)

        result = investigator._infer_deployment_status_from_pods(
            pods_output, "big-app"
        )

        assert "DEPLOYMENT STATUS: big-app" in result
        assert "Replicas: 10/10 ready" in result
        assert "..." in result  # Should indicate more pods exist
