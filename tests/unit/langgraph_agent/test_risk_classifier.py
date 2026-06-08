"""Tests for action risk classification."""

import pytest


class TestClassifyToolRisk:
    """Test tool-based risk classification for K8s MCP tools.

    This is the RECOMMENDED approach for classifying risk.
    Tool names come from actual MCP tool definitions.
    """

    def test_kubectl_scale_is_high_risk(self):
        """kubectl_scale modifies cluster state (write tool)."""
        from src.tools.k8s_tools import classify_tool_risk

        assert classify_tool_risk("kubectl_scale") == "high"

    def test_kubectl_delete_is_high_risk(self):
        """kubectl_delete modifies cluster state (write tool)."""
        from src.tools.k8s_tools import classify_tool_risk

        assert classify_tool_risk("kubectl_delete") == "high"

    def test_kubectl_rollout_restart_is_high_risk(self):
        """kubectl_rollout_restart modifies cluster state (write tool)."""
        from src.tools.k8s_tools import classify_tool_risk

        assert classify_tool_risk("kubectl_rollout_restart") == "high"

    def test_kubectl_apply_is_high_risk(self):
        """kubectl_apply modifies cluster state (write tool)."""
        from src.tools.k8s_tools import classify_tool_risk

        assert classify_tool_risk("kubectl_apply") == "high"

    def test_kubectl_get_pods_is_low_risk(self):
        """kubectl_get_pods only reads cluster state (read tool)."""
        from src.tools.k8s_tools import classify_tool_risk

        assert classify_tool_risk("kubectl_get_pods") == "low"

    def test_kubectl_logs_is_low_risk(self):
        """kubectl_logs only reads cluster state (read tool)."""
        from src.tools.k8s_tools import classify_tool_risk

        assert classify_tool_risk("kubectl_logs") == "low"

    def test_kubectl_events_is_low_risk(self):
        """kubectl_events only reads cluster state (read tool)."""
        from src.tools.k8s_tools import classify_tool_risk

        assert classify_tool_risk("kubectl_events") == "low"

    def test_kubectl_describe_pod_is_low_risk(self):
        """kubectl_describe_pod only reads cluster state (read tool)."""
        from src.tools.k8s_tools import classify_tool_risk

        assert classify_tool_risk("kubectl_describe_pod") == "low"

    def test_unknown_tool_defaults_to_high(self):
        """Unknown tools default to high risk for safety."""
        from src.tools.k8s_tools import classify_tool_risk

        assert classify_tool_risk("unknown_tool") == "high"
        assert classify_tool_risk("kubectl_custom_action") == "high"

    def test_is_write_tool_for_write_operations(self):
        """is_write_tool should return True for write operations."""
        from src.tools.k8s_tools import is_write_tool

        assert is_write_tool("kubectl_scale") is True
        assert is_write_tool("kubectl_delete") is True
        assert is_write_tool("kubectl_rollout_restart") is True
        assert is_write_tool("kubectl_apply") is True

    def test_is_write_tool_for_read_operations(self):
        """is_write_tool should return False for read-only operations."""
        from src.tools.k8s_tools import is_write_tool

        assert is_write_tool("kubectl_get_pods") is False
        assert is_write_tool("kubectl_logs") is False
        assert is_write_tool("kubectl_events") is False
        assert is_write_tool("kubectl_describe_pod") is False
