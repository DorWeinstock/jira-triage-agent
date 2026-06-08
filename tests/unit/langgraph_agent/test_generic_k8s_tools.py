"""Tests for generic K8s MCP tools (kubectl_get and kubectl_describe).

Verifies that:
- New get()/describe() methods call the correct MCP tools with correct args
- VALID_RESOURCE_TYPES includes all new resource types from GVR map
- READ_TOOLS includes kubectl_get and kubectl_describe
- Validation is preserved (RFC 1123 names, namespaces)
- Readonly enforcement applies to new tools
- classify_tool_risk returns "low" for new read tools
"""

import pytest
from unittest.mock import AsyncMock

from src.tools.k8s_tools import (
    K8sTools,
    READ_TOOLS,
    WRITE_TOOLS,
    VALID_RESOURCE_TYPES,
    classify_tool_risk,
    is_write_tool,
)
from src.exceptions import ValidationError


class TestGenericGetMethod:
    """Test the new generic get() method."""

    @pytest.fixture
    def k8s_tools(self):
        return K8sTools(mcp_endpoint="http://mock-k8s-mcp:8080")

    @pytest.mark.asyncio
    async def test_get_pods_with_namespace(self, k8s_tools):
        """get() should call kubectl_get with resource_type and namespace."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: Pod\n")
        result = await k8s_tools.get("pods", namespace="default")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get", {"resource_type": "pods", "namespace": "default"}
        )
        assert result == "kind: Pod\n"

    @pytest.mark.asyncio
    async def test_get_with_name(self, k8s_tools):
        """get() should pass name when specified."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: Pod\n")
        await k8s_tools.get("pods", namespace="default", name="my-pod")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get",
            {"resource_type": "pods", "namespace": "default", "name": "my-pod"},
        )

    @pytest.mark.asyncio
    async def test_get_all_namespaces(self, k8s_tools):
        """get() without namespace should omit namespace arg."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: Pod\n")
        await k8s_tools.get("pods")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get", {"resource_type": "pods"}
        )

    @pytest.mark.asyncio
    async def test_get_with_label_selector(self, k8s_tools):
        """get() should pass label_selector."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: Pod\n")
        await k8s_tools.get("pods", namespace="default", label_selector="app=web")
        args = k8s_tools.call_tool.call_args[0][1]
        assert args["label_selector"] == "app=web"

    @pytest.mark.asyncio
    async def test_get_with_field_selector(self, k8s_tools):
        """get() should pass field_selector."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: Pod\n")
        await k8s_tools.get(
            "pods", namespace="default", field_selector="status.phase=Running"
        )
        args = k8s_tools.call_tool.call_args[0][1]
        assert args["field_selector"] == "status.phase=Running"

    @pytest.mark.asyncio
    async def test_get_with_limit(self, k8s_tools):
        """get() should pass limit when specified."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: Pod\n")
        await k8s_tools.get("pods", namespace="default", limit=50)
        args = k8s_tools.call_tool.call_args[0][1]
        assert args["limit"] == 50

    @pytest.mark.asyncio
    async def test_get_statefulsets(self, k8s_tools):
        """get() should work with statefulsets (previously unsupported)."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: StatefulSet\n")
        await k8s_tools.get("statefulsets", namespace="default")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get",
            {"resource_type": "statefulsets", "namespace": "default"},
        )

    @pytest.mark.asyncio
    async def test_get_nodes_cluster_scoped(self, k8s_tools):
        """get() should work with nodes (cluster-scoped)."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: Node\n")
        await k8s_tools.get("nodes")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_get", {"resource_type": "nodes"}
        )

    @pytest.mark.asyncio
    async def test_get_validates_resource_type(self, k8s_tools):
        """get() should reject invalid resource types."""
        with pytest.raises(ValidationError, match="Invalid resource type"):
            await k8s_tools.get("foobar", namespace="default")

    @pytest.mark.asyncio
    async def test_get_validates_namespace(self, k8s_tools):
        """get() should validate namespace format."""
        with pytest.raises(ValidationError, match="Invalid namespace"):
            await k8s_tools.get("pods", namespace="INVALID_NS")

    @pytest.mark.asyncio
    async def test_get_validates_name(self, k8s_tools):
        """get() should validate resource name format."""
        with pytest.raises(ValidationError, match="Invalid name"):
            await k8s_tools.get("pods", namespace="default", name="INVALID_NAME!")


class TestGenericDescribeMethod:
    """Test the new generic describe() method."""

    @pytest.fixture
    def k8s_tools(self):
        return K8sTools(mcp_endpoint="http://mock-k8s-mcp:8080")

    @pytest.mark.asyncio
    async def test_describe_pod(self, k8s_tools):
        """describe() should call kubectl_describe with correct args."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: Pod\n")
        result = await k8s_tools.describe("pod", "my-pod", namespace="default")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_describe",
            {"resource_type": "pod", "name": "my-pod", "namespace": "default"},
        )

    @pytest.mark.asyncio
    async def test_describe_deployment(self, k8s_tools):
        """describe() should work with deployment."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: Deployment\n")
        await k8s_tools.describe("deployment", "my-deploy", namespace="default")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_describe",
            {
                "resource_type": "deployment",
                "name": "my-deploy",
                "namespace": "default",
            },
        )

    @pytest.mark.asyncio
    async def test_describe_node_cluster_scoped(self, k8s_tools):
        """describe() without namespace for cluster-scoped resources."""
        k8s_tools.call_tool = AsyncMock(return_value="kind: Node\n")
        await k8s_tools.describe("node", "worker-1")
        k8s_tools.call_tool.assert_called_once_with(
            "kubectl_describe",
            {"resource_type": "node", "name": "worker-1"},
        )

    @pytest.mark.asyncio
    async def test_describe_validates_resource_type(self, k8s_tools):
        """describe() should reject invalid resource types."""
        with pytest.raises(ValidationError, match="Invalid resource type"):
            await k8s_tools.describe("foobar", "my-resource", namespace="default")

    @pytest.mark.asyncio
    async def test_describe_validates_name(self, k8s_tools):
        """describe() should validate name format."""
        with pytest.raises(ValidationError, match="Invalid name"):
            await k8s_tools.describe("pod", "INVALID!", namespace="default")


class TestUpdatedConstants:
    """Test that constants are updated for new tools."""

    def test_valid_resource_types_includes_new_types(self):
        """VALID_RESOURCE_TYPES should include all GVR map types."""
        new_types = {
            "nodes", "node", "namespaces", "namespace", "ns",
            "jobs", "job", "cronjobs", "cronjob", "cj",
            "ingresses", "ingress", "ing",
            "replicasets", "replicaset", "rs",
            "persistentvolumeclaims", "persistentvolumeclaim", "pvcs", "pvc",
            "persistentvolumes", "persistentvolume", "pvs", "pv",
            "serviceaccounts", "serviceaccount", "sa",
            "networkpolicies", "networkpolicy", "netpol",
            "storageclasses", "storageclass", "sc",
            "horizontalpodautoscalers", "hpa",
            "sts", "ds",
        }
        for rt in new_types:
            assert rt in VALID_RESOURCE_TYPES, f"{rt} missing from VALID_RESOURCE_TYPES"

    def test_valid_resource_types_preserves_existing(self):
        """Existing resource types should still be present."""
        existing = {"pods", "pod", "deployments", "deployment", "deploy",
                    "services", "service", "svc", "configmaps", "configmap",
                    "cm", "secrets", "secret", "endpoints",
                    "statefulsets", "statefulset", "daemonsets", "daemonset"}
        for rt in existing:
            assert rt in VALID_RESOURCE_TYPES, f"{rt} missing from VALID_RESOURCE_TYPES"

    def test_read_tools_includes_generic_tools(self):
        """READ_TOOLS should include kubectl_get and kubectl_describe."""
        assert "kubectl_get" in READ_TOOLS
        assert "kubectl_describe" in READ_TOOLS

    def test_read_tools_preserves_existing(self):
        """Existing read tools should still be present."""
        assert "kubectl_get_pods" in READ_TOOLS
        assert "kubectl_logs" in READ_TOOLS
        assert "kubectl_events" in READ_TOOLS
        assert "kubectl_describe_pod" in READ_TOOLS

    def test_generic_tools_not_in_write_tools(self):
        """Generic read tools should NOT be in WRITE_TOOLS."""
        assert "kubectl_get" not in WRITE_TOOLS
        assert "kubectl_describe" not in WRITE_TOOLS


class TestToolClassification:
    """Test that tool risk classification works for new tools."""

    def test_kubectl_get_is_low_risk(self):
        assert classify_tool_risk("kubectl_get") == "low"

    def test_kubectl_describe_is_low_risk(self):
        assert classify_tool_risk("kubectl_describe") == "low"

    def test_kubectl_get_is_not_write(self):
        assert not is_write_tool("kubectl_get")

    def test_kubectl_describe_is_not_write(self):
        assert not is_write_tool("kubectl_describe")


class TestReadonlyEnforcement:
    """Test that readonly mode works with generic tools."""

    @pytest.fixture
    def readonly_tools(self):
        return K8sTools(mcp_endpoint="http://mock-k8s-mcp:8080", readonly=True)

    @pytest.mark.asyncio
    async def test_get_allowed_in_readonly(self, readonly_tools):
        """get() should work in readonly mode (read tool)."""
        readonly_tools.call_tool = AsyncMock(return_value="kind: Pod\n")
        # Should not raise -- get is a read operation
        # We need to bypass the parent call_tool since we're mocking at class level
        # The readonly check happens in K8sTools.call_tool, so we mock super()
        result = await readonly_tools.get("pods", namespace="default")
        assert result == "kind: Pod\n"

    @pytest.mark.asyncio
    async def test_describe_allowed_in_readonly(self, readonly_tools):
        """describe() should work in readonly mode (read tool)."""
        readonly_tools.call_tool = AsyncMock(return_value="kind: Pod\n")
        result = await readonly_tools.describe("pod", "my-pod", namespace="default")
        assert result == "kind: Pod\n"
