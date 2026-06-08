"""Tests for ConfigMap and Secret MCP tool support in K8sTools.

Verifies that:
- kubectl_get tool_map includes configmap and secret variants
- MCP tool calls use the correct tool names and arguments
- READ_TOOLS frozenset includes the new tools
"""

import pytest
from unittest.mock import AsyncMock, patch

from src.tools.k8s_tools import K8sTools, READ_TOOLS


class TestToolMapConfigMapVariants:
    """Test that configmap resource type variants map to kubectl_get_configmaps."""

    @pytest.fixture
    def k8s_tools(self):
        """Create K8sTools with mocked endpoint."""
        return K8sTools(mcp_endpoint="http://mock-k8s-mcp:8080")

    @pytest.mark.asyncio
    async def test_configmaps_maps_to_tool(self, k8s_tools):
        """'configmaps' should map to kubectl_get_configmaps."""
        k8s_tools.call_tool = AsyncMock(return_value="Found 1 configmap(s)")
        result = await k8s_tools.kubectl_get("configmaps", namespace="default")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get_configmaps", {"namespace": "default"}
        )
        assert result == "Found 1 configmap(s)"

    @pytest.mark.asyncio
    async def test_configmap_maps_to_tool(self, k8s_tools):
        """'configmap' (singular) should map to kubectl_get_configmaps."""
        k8s_tools.call_tool = AsyncMock(return_value="Found 1 configmap(s)")
        result = await k8s_tools.kubectl_get("configmap", namespace="default")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get_configmaps", {"namespace": "default"}
        )

    @pytest.mark.asyncio
    async def test_cm_maps_to_tool(self, k8s_tools):
        """'cm' (abbreviation) should map to kubectl_get_configmaps."""
        k8s_tools.call_tool = AsyncMock(return_value="Found 1 configmap(s)")
        result = await k8s_tools.kubectl_get("cm", namespace="default")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get_configmaps", {"namespace": "default"}
        )


class TestToolMapSecretVariants:
    """Test that secret resource type variants map to kubectl_get_secrets."""

    @pytest.fixture
    def k8s_tools(self):
        """Create K8sTools with mocked endpoint."""
        return K8sTools(mcp_endpoint="http://mock-k8s-mcp:8080")

    @pytest.mark.asyncio
    async def test_secrets_maps_to_tool(self, k8s_tools):
        """'secrets' should map to kubectl_get_secrets."""
        k8s_tools.call_tool = AsyncMock(return_value="Found 1 secret(s)")
        result = await k8s_tools.kubectl_get("secrets", namespace="default")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get_secrets", {"namespace": "default"}
        )
        assert result == "Found 1 secret(s)"

    @pytest.mark.asyncio
    async def test_secret_maps_to_tool(self, k8s_tools):
        """'secret' (singular) should map to kubectl_get_secrets."""
        k8s_tools.call_tool = AsyncMock(return_value="Found 1 secret(s)")
        result = await k8s_tools.kubectl_get("secret", namespace="default")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get_secrets", {"namespace": "default"}
        )


class TestConfigMapSecretMCPCalls:
    """Test that MCP tool calls pass correct arguments."""

    @pytest.fixture
    def k8s_tools(self):
        """Create K8sTools with mocked endpoint."""
        return K8sTools(mcp_endpoint="http://mock-k8s-mcp:8080")

    @pytest.mark.asyncio
    async def test_kubectl_get_configmaps_with_label_selector(self, k8s_tools):
        """kubectl_get for configmaps should pass label_selector."""
        k8s_tools.call_tool = AsyncMock(return_value="Found 2 configmap(s)")
        result = await k8s_tools.kubectl_get(
            "configmaps", namespace="production", label_selector="app=myapp"
        )
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get_configmaps",
            {"namespace": "production", "label_selector": "app=myapp"},
        )

    @pytest.mark.asyncio
    async def test_kubectl_get_secrets_with_label_selector(self, k8s_tools):
        """kubectl_get for secrets should pass label_selector."""
        k8s_tools.call_tool = AsyncMock(return_value="Found 1 secret(s)")
        result = await k8s_tools.kubectl_get(
            "secrets", namespace="staging", label_selector="env=staging"
        )
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get_secrets",
            {"namespace": "staging", "label_selector": "env=staging"},
        )


class TestReadToolsInclusion:
    """Test that READ_TOOLS frozenset includes configmap and secret tools."""

    def test_read_tools_includes_configmaps(self):
        """kubectl_get_configmaps should be in READ_TOOLS."""
        assert "kubectl_get_configmaps" in READ_TOOLS

    def test_read_tools_includes_secrets(self):
        """kubectl_get_secrets should be in READ_TOOLS."""
        assert "kubectl_get_secrets" in READ_TOOLS

    def test_read_tools_still_includes_existing(self):
        """Existing read tools should still be present."""
        assert "kubectl_get_pods" in READ_TOOLS
        assert "kubectl_logs" in READ_TOOLS
        assert "kubectl_events" in READ_TOOLS
        assert "kubectl_describe_pod" in READ_TOOLS
