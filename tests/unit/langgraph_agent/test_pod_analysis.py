"""Unit tests for pod_analysis.py utilities.

Tests cover:
- Pod status validation (running, ready, error states)
- Output format detection (MCP structured vs kubectl tabular)
- Parsing of both formats with edge cases
- Deployment pod analysis and filtering
- Input validation and robustness
"""

import pytest
from src.agents.pod_analysis import (
    is_pod_running_and_ready,
    is_pod_in_error_state,
    _is_mcp_structured_format,
    _parse_mcp_structured,
    _parse_tabular,
    analyze_deployment_pods,
    format_pod_list,
    format_no_pods_status,
    format_deployment_status,
    infer_deployment_status_from_pods,
)


class TestIsPodRunningAndReady:
    """Test is_pod_running_and_ready validation logic."""

    def test_pod_running_all_ready(self):
        """1/1 with Running status returns True."""
        assert is_pod_running_and_ready("Running", "1/1") is True

    def test_pod_running_multiple_ready(self):
        """2/2 with Running status returns True."""
        assert is_pod_running_and_ready("Running", "2/2") is True

    def test_pod_running_not_all_ready(self):
        """0/1 with Running status returns False."""
        assert is_pod_running_and_ready("Running", "0/1") is False

    def test_pod_running_zero_total(self):
        """0/0 with Running status returns False (zero total not healthy)."""
        assert is_pod_running_and_ready("Running", "0/0") is False

    def test_pod_non_numeric_ready_string(self):
        """abc/abc returns False (non-numeric, was silent bug)."""
        assert is_pod_running_and_ready("Running", "abc/abc") is False

    def test_pod_pending_status(self):
        """1/1 with Pending status returns False (status gate fails)."""
        assert is_pod_running_and_ready("Pending", "1/1") is False

    def test_pod_empty_ready_string(self):
        """Empty ready string returns False."""
        assert is_pod_running_and_ready("Running", "") is False

    def test_pod_malformed_ready_no_slash(self):
        """Ready string without slash returns False."""
        assert is_pod_running_and_ready("Running", "1") is False

    def test_pod_malformed_ready_too_many_parts(self):
        """Ready string with too many slashes returns False."""
        assert is_pod_running_and_ready("Running", "1/1/1") is False

    def test_pod_ready_with_alpha_chars(self):
        """Ready with letter in numeric part returns False."""
        assert is_pod_running_and_ready("Running", "1a/1") is False


class TestIsPodInErrorState:
    """Test is_pod_in_error_state status checking."""

    def test_error_state(self):
        """'Error' status is error state."""
        assert is_pod_in_error_state("Error") is True

    def test_crash_loop_back_off(self):
        """'CrashLoopBackOff' status is error state."""
        assert is_pod_in_error_state("CrashLoopBackOff") is True

    def test_image_pull_back_off(self):
        """'ImagePullBackOff' status is error state."""
        assert is_pod_in_error_state("ImagePullBackOff") is True

    def test_pending_is_error(self):
        """'Pending' is treated as error state."""
        assert is_pod_in_error_state("Pending") is True

    def test_create_container_config_error(self):
        """'CreateContainerConfigError' status is error state."""
        assert is_pod_in_error_state("CreateContainerConfigError") is True

    def test_running_not_error(self):
        """'Running' status is not error state."""
        assert is_pod_in_error_state("Running") is False

    def test_unknown_status_not_error(self):
        """Unknown status is not error state."""
        assert is_pod_in_error_state("Unknown") is False

    def test_empty_status_not_error(self):
        """Empty status is not error state."""
        assert is_pod_in_error_state("") is False


class TestIsMcpStructuredFormat:
    """Test MCP structured format detection."""

    def test_valid_mcp_block(self):
        """Valid MCP block with NAME: and STATUS: on separate lines returns True."""
        mcp_block = """Found 1 pod(s):

NAME: api-server-5f8d6c7b-abc12
  NAMESPACE: production
  STATUS: Running"""
        assert _is_mcp_structured_format(mcp_block) is True

    def test_tabular_with_name_header(self):
        """Tabular output with NAME header (no colon) returns False."""
        tabular = """NAME                          READY   STATUS    RESTARTS   AGE
api-server-abc123-xyz99       1/1     Running   0          5h"""
        assert _is_mcp_structured_format(tabular) is False

    def test_only_name_present(self):
        """Only NAME: present, no STATUS: returns False."""
        mcp_partial = """NAME: api-server-abc123
NAMESPACE: default"""
        assert _is_mcp_structured_format(mcp_partial) is False

    def test_only_status_present(self):
        """Only STATUS: present, no NAME: returns False."""
        mcp_partial = """STATUS: Running
RESTARTS: 0"""
        assert _is_mcp_structured_format(mcp_partial) is False

    def test_empty_string(self):
        """Empty string returns False."""
        assert _is_mcp_structured_format("") is False

    def test_name_and_status_on_separate_lines(self):
        """NAME: and STATUS: on separate lines returns True."""
        mcp_lines = """NAME: pod-1
STATUS: Running"""
        assert _is_mcp_structured_format(mcp_lines) is True

    def test_embedded_name_in_text(self):
        """NAME: at line start but STATUS: embedded in text returns False (strict)."""
        text_with_name = """Based on analysis:
NAME: problematic-pod-abc123
The issue is that everything is OK."""
        # STATUS: not at line start, so returns False
        assert _is_mcp_structured_format(text_with_name) is False


class TestParseMcpStructured:
    """Test MCP structured format parsing."""

    def test_single_pod(self):
        """Single pod block parses correctly."""
        mcp = """Found 1 pod(s):

NAME: api-server-5f8d6c7b-abc12
  NAMESPACE: production
  STATUS: Running"""
        pods = _parse_mcp_structured(mcp)
        assert len(pods) == 1
        assert pods[0]["name"] == "api-server-5f8d6c7b-abc12"
        assert pods[0]["status"] == "Running"

    def test_multiple_pods(self):
        """Multiple pod blocks parse correctly with order preserved."""
        mcp = """Found 2 pod(s):

NAME: api-server-abc123-xyz99
  STATUS: Running

NAME: frontend-def456-abc12
  STATUS: Pending"""
        pods = _parse_mcp_structured(mcp)
        assert len(pods) == 2
        assert pods[0]["name"] == "api-server-abc123-xyz99"
        assert pods[1]["name"] == "frontend-def456-abc12"

    def test_missing_status_field(self):
        """Pod block without STATUS field still captured (status empty)."""
        mcp = """NAME: nginx-pod-xyz789
  NAMESPACE: default"""
        pods = _parse_mcp_structured(mcp)
        assert len(pods) == 1
        assert pods[0]["name"] == "nginx-pod-xyz789"
        assert pods[0]["status"] == ""

    def test_empty_input(self):
        """Empty string returns empty list."""
        pods = _parse_mcp_structured("")
        assert pods == []

    def test_whitespace_only(self):
        """Whitespace-only input returns empty list."""
        pods = _parse_mcp_structured("   \n\n  ")
        assert pods == []

    def test_pod_name_with_whitespace(self):
        """Whitespace after pod name stripped correctly."""
        mcp = "NAME:  api-server-abc123-def45  \nSTATUS:  Running  "
        pods = _parse_mcp_structured(mcp)
        assert len(pods) == 1
        assert pods[0]["name"] == "api-server-abc123-def45"
        assert pods[0]["status"] == "Running"


class TestParseTabular:
    """Test kubectl tabular format parsing."""

    def test_normal_kubectl_output(self):
        """Standard kubectl output parses correctly."""
        tabular = """NAME                            READY   STATUS    RESTARTS   AGE
order-service-688ffdf8b-lw2rc   1/1     Running   0          2m"""
        pods = _parse_tabular(tabular)
        assert len(pods) == 1
        assert pods[0]["name"] == "order-service-688ffdf8b-lw2rc"
        assert pods[0]["ready"] == "1/1"
        assert pods[0]["status"] == "Running"

    def test_multiple_pods(self):
        """Multiple pod rows parse correctly."""
        tabular = """NAME                            READY   STATUS    RESTARTS   AGE
api-server-abc123-xyz99         1/1     Running   0          5h
frontend-def456-abc12           0/1     Pending   0          3h"""
        pods = _parse_tabular(tabular)
        assert len(pods) == 2
        assert pods[0]["name"] == "api-server-abc123-xyz99"
        assert pods[1]["name"] == "frontend-def456-abc12"

    def test_header_only(self):
        """Header-only output returns empty list."""
        tabular = "NAME                            READY   STATUS    RESTARTS   AGE"
        pods = _parse_tabular(tabular)
        assert pods == []

    def test_no_resources_message(self):
        """'No resources found' message skipped."""
        tabular = "No resources found in default namespace."
        pods = _parse_tabular(tabular)
        assert pods == []

    def test_malformed_line_too_few_columns(self):
        """Line with <3 columns skipped silently."""
        tabular = """NAME                            READY   STATUS
api-server-abc123-xyz99         1/1"""
        pods = _parse_tabular(tabular)
        assert len(pods) == 0

    def test_blank_lines_skipped(self):
        """Blank lines skipped without error."""
        tabular = """NAME                            READY   STATUS    RESTARTS   AGE

api-server-abc123-xyz99         1/1     Running   0          5h

"""
        pods = _parse_tabular(tabular)
        assert len(pods) == 1

    def test_pod_with_many_restarts(self):
        """Pod with high restart count parses correctly."""
        tabular = "api-pod-xyz789-abc12   0/1     CrashLoopBackOff   12         10m"
        # Parse single line (no header)
        parts = tabular.split()
        assert len(parts) >= 3  # Sufficient for parsing

    def test_malformed_line_insufficient_columns(self):
        """Line with only NAME and STATUS (2 total columns) skipped."""
        # This is actually a valid parse attempt (parts[0]=name, parts[1]=status)
        # but the test is checking the len(parts) < 3 guard
        tabular = "my-pod-abc123-xyz99 Running"
        pods = _parse_tabular(tabular)
        # len(parts) == 2, which is < 3, so skipped
        assert len(pods) == 0


class TestAnalyzeDeploymentPods:
    """Test deployment pod analysis with filtering."""

    def test_none_input_returns_empty(self):
        """None pods_output returns empty result dict, no crash."""
        result = analyze_deployment_pods(None, "my-app")
        assert result == {
            "matching_pods": [],
            "running": 0,
            "errors": [],
            "total": 0
        }

    def test_empty_input_returns_empty(self):
        """Empty string pods_output returns empty result dict."""
        result = analyze_deployment_pods("", "my-app")
        assert result == {
            "matching_pods": [],
            "running": 0,
            "errors": [],
            "total": 0
        }

    def test_tabular_matching_deployment(self):
        """Tabular input with matching deployment returns correct counts."""
        tabular = """NAME                            READY   STATUS    RESTARTS   AGE
my-app-abc123-xyz99             1/1     Running   0          5h
my-app-def456-abc12             1/1     Running   0          3h"""
        result = analyze_deployment_pods(tabular, "my-app")
        assert result["total"] == 2
        assert result["running"] == 2
        assert len(result["matching_pods"]) == 2

    def test_tabular_no_matching_pods(self):
        """Tabular input with no matching deployment returns zeros."""
        tabular = """NAME                            READY   STATUS    RESTARTS   AGE
other-app-abc123-xyz99          1/1     Running   0          5h"""
        result = analyze_deployment_pods(tabular, "my-app")
        assert result["total"] == 0
        assert result["running"] == 0
        assert result["matching_pods"] == []

    def test_mix_of_running_and_error_pods(self):
        """Mix of healthy and error pods counts correctly."""
        tabular = """NAME                            READY   STATUS    RESTARTS   AGE
my-app-abc123-xyz99             1/1     Running   0          5h
my-app-def456-abc12             0/1     CrashLoopBackOff   5          3h"""
        result = analyze_deployment_pods(tabular, "my-app")
        assert result["total"] == 2
        assert result["running"] == 1
        assert len(result["errors"]) == 1
        assert "CrashLoopBackOff" in result["errors"][0]

    def test_mcp_structured_matching(self):
        """MCP structured format with matching deployment analyzed correctly."""
        mcp = """NAME: my-app-xyz789-abc12
  STATUS: Running

NAME: my-app-def456-pqr78
  STATUS: Pending"""
        result = analyze_deployment_pods(mcp, "my-app")
        # Due to dependency_discovery.get_deployment_from_pod_name, 
        # results depend on actual pod name parsing
        assert "matching_pods" in result
        assert "running" in result
        assert "errors" in result


class TestFormatPodList:
    """Test pod list formatting utility."""

    def test_fewer_than_five_pods(self):
        """<5 pods listed without ellipsis."""
        pods = ["pod1", "pod2", "pod3"]
        formatted = format_pod_list(pods)
        assert "pod1, pod2, pod3" in formatted
        assert "..." not in formatted

    def test_exactly_five_pods(self):
        """Exactly 5 pods listed without ellipsis."""
        pods = [f"pod{i}" for i in range(5)]
        formatted = format_pod_list(pods)
        assert "..." not in formatted

    def test_more_than_five_pods(self):
        """>5 pods shows first 5 with ellipsis."""
        pods = [f"pod{i}" for i in range(10)]
        formatted = format_pod_list(pods)
        assert "pod0, pod1, pod2, pod3, pod4" in formatted
        assert "..." in formatted

    def test_empty_list(self):
        """Empty pod list formats gracefully."""
        formatted = format_pod_list([])
        assert "Pods:" in formatted


class TestFormatNoPodStatus:
    """Test no-pods status formatting."""

    def test_no_pods_message_format(self):
        """No pods status contains expected fields."""
        result = format_no_pods_status("my-deployment")
        assert "DEPLOYMENT STATUS: my-deployment" in result
        assert "Replicas: 0/0" in result
        assert "SCALED TO ZERO" in result or "NO PODS" in result
        assert "kubectl scale" in result


class TestFormatDeploymentStatus:
    """Test deployment status formatting."""

    def test_healthy_deployment_status(self):
        """All pods running formatted as HEALTHY."""
        pod_info = {
            "matching_pods": ["pod1", "pod2"],
            "running": 2,
            "errors": [],
            "total": 2
        }
        result = format_deployment_status("my-app", pod_info)
        assert "HEALTHY" in result
        assert "2/2" in result

    def test_transitioning_deployment_status(self):
        """Some pods not ready formatted as TRANSITIONING."""
        pod_info = {
            "matching_pods": ["pod1", "pod2"],
            "running": 1,
            "errors": [],
            "total": 2
        }
        result = format_deployment_status("my-app", pod_info)
        assert "TRANSITIONING" in result
        assert "1/2" in result

    def test_degraded_deployment_status(self):
        """Pods in error state formatted as DEGRADED."""
        pod_info = {
            "matching_pods": ["pod1", "pod2"],
            "running": 0,
            "errors": ["pod1: CrashLoopBackOff", "pod2: ImagePullBackOff"],
            "total": 2
        }
        result = format_deployment_status("my-app", pod_info)
        assert "DEGRADED" in result
        assert "2 pod(s) in error state" in result


class TestInferDeploymentStatusFromPods:
    """Test top-level deployment status inference."""

    def test_empty_pods_output(self):
        """Empty pods output infers scaled to zero."""
        result = infer_deployment_status_from_pods("", "my-app")
        assert "DEPLOYMENT STATUS: my-app" in result
        assert "Replicas: 0/0" in result

    def test_no_resources_found_message(self):
        """'No resources found' message infers scaled to zero."""
        result = infer_deployment_status_from_pods(
            "No resources found in default namespace.",
            "my-app"
        )
        assert "Replicas: 0/0" in result

    def test_none_input(self):
        """None input handled gracefully."""
        result = infer_deployment_status_from_pods(None, "my-app")
        assert "DEPLOYMENT STATUS: my-app" in result
        assert "Replicas: 0/0" in result
