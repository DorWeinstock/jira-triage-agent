"""Tests for kubectl_events field_selector construction.

Verifies that involvedObject.kind is included in field_selector when
resource_type is specified, preventing ambiguous results across different
resource types sharing the same name.
"""

import pytest
from unittest.mock import AsyncMock

from src.tools.k8s_tools import K8sTools, RESOURCE_KIND_MAP
from src.exceptions import ValidationError


class TestKubectlEventsFieldSelector:
    """Test kubectl_events field_selector construction."""

    @pytest.fixture
    def k8s_tools(self):
        return K8sTools(mcp_endpoint="http://mock-k8s-mcp:8080")

    @pytest.mark.asyncio
    async def test_events_with_name_and_resource_type(self, k8s_tools):
        """events() should include involvedObject.kind when resource_type specified."""
        k8s_tools.call_tool = AsyncMock(return_value="events: []")
        
        await k8s_tools.kubectl_events(
            namespace="default",
            resource_type="pods",
            name="my-pod"
        )
        
        # Verify call_tool was called with correct field_selector
        call_args = k8s_tools.call_tool.call_args[0]
        assert call_args[0] == "kubectl_events"
        args_dict = call_args[1]
        
        # Should contain both name and kind
        assert "involvedObject.name=my-pod" in args_dict["field_selector"]
        assert "involvedObject.kind=Pod" in args_dict["field_selector"]
        assert "," in args_dict["field_selector"]  # Both selectors joined

    @pytest.mark.asyncio
    async def test_events_with_name_only(self, k8s_tools):
        """events() with name only should omit involvedObject.kind."""
        k8s_tools.call_tool = AsyncMock(return_value="events: []")
        
        await k8s_tools.kubectl_events(
            namespace="default",
            name="my-resource"
        )
        
        call_args = k8s_tools.call_tool.call_args[0]
        args_dict = call_args[1]
        
        # Should only have name selector, no kind
        assert args_dict["field_selector"] == "involvedObject.name=my-resource"
        assert "kind" not in args_dict["field_selector"]

    @pytest.mark.asyncio
    async def test_events_statefulsets_kind_mapping(self, k8s_tools):
        """events() should map 'statefulsets' to 'StatefulSet' kind."""
        k8s_tools.call_tool = AsyncMock(return_value="events: []")
        
        await k8s_tools.kubectl_events(
            namespace="default",
            resource_type="statefulsets",
            name="my-sts"
        )
        
        call_args = k8s_tools.call_tool.call_args[0]
        args_dict = call_args[1]
        
        # Should correctly map to StatefulSet (not Statefulset)
        assert "involvedObject.kind=StatefulSet" in args_dict["field_selector"]

    @pytest.mark.asyncio
    async def test_events_deployment_kind_mapping(self, k8s_tools):
        """events() should map 'deployments' to 'Deployment' kind."""
        k8s_tools.call_tool = AsyncMock(return_value="events: []")
        
        await k8s_tools.kubectl_events(
            namespace="default",
            resource_type="deployments",
            name="my-deployment"
        )
        
        call_args = k8s_tools.call_tool.call_args[0]
        args_dict = call_args[1]
        
        # Should correctly map to Deployment
        assert "involvedObject.kind=Deployment" in args_dict["field_selector"]

    @pytest.mark.asyncio
    async def test_events_no_name_no_selector(self, k8s_tools):
        """events() without name should not set field_selector."""
        k8s_tools.call_tool = AsyncMock(return_value="events: []")
        
        await k8s_tools.kubectl_events(namespace="default")
        
        call_args = k8s_tools.call_tool.call_args[0]
        args_dict = call_args[1]
        
        # Should have namespace but no field_selector
        assert "field_selector" not in args_dict
        assert args_dict["namespace"] == "default"

    @pytest.mark.asyncio
    async def test_events_all_resource_kinds_in_map(self, k8s_tools):
        """Verify RESOURCE_KIND_MAP covers all common resource types."""
        expected_types = [
            "pods", "deployments", "services", "configmaps", "secrets",
            "nodes", "namespaces", "statefulsets", "daemonsets", "replicasets",
            "jobs", "cronjobs", "ingresses", "endpoints",
        ]
        
        for resource_type in expected_types:
            assert resource_type in RESOURCE_KIND_MAP, f"{resource_type} missing from RESOURCE_KIND_MAP"
            kind = RESOURCE_KIND_MAP[resource_type]
            assert kind and len(kind) > 0, f"{resource_type} maps to empty kind"

    def test_events_validates_namespace_sync(self, k8s_tools):
        """events() should validate namespace."""
        # Empty namespace validation happens before MCP call
        with pytest.raises(ValidationError) as exc_info:
            k8s_tools._validate_namespace("")
        
        assert "cannot be empty" in str(exc_info.value).lower()

    def test_events_validates_resource_type_sync(self, k8s_tools):
        """events() should validate resource_type if provided."""
        # Invalid resource_type validation happens before MCP call
        with pytest.raises(ValidationError) as exc_info:
            k8s_tools._validate_resource_type("invalid-type")
        
        assert "invalid resource type" in str(exc_info.value).lower()

    def test_events_validates_name_sync(self, k8s_tools):
        """events() should validate name if provided."""
        # Empty name validation happens before MCP call
        with pytest.raises(ValidationError) as exc_info:
            k8s_tools._validate_resource_name("")
        
        assert "cannot be empty" in str(exc_info.value).lower()
