"""Tests for kubectl_apply YAML validation and dangerous kinds blocklist.

Verifies that:
1. Malformed YAML raises ValidationError
2. Empty YAML raises ValidationError
3. ClusterRoleBinding and ClusterRole kinds are blocked
4. Safe kinds (Pod, ConfigMap, etc.) are allowed
5. Multi-document YAML is checked for dangerous kinds
"""

import pytest
from unittest.mock import AsyncMock

from src.tools.k8s_tools import K8sTools, DANGEROUS_KINDS
from src.exceptions import ValidationError


class TestKubectlApplyValidation:
    """Test kubectl_apply YAML validation and dangerous kinds blocklist."""

    @pytest.fixture
    def k8s_tools(self):
        return K8sTools(mcp_endpoint="http://mock-k8s-mcp:8080")

    @pytest.mark.asyncio
    async def test_apply_malformed_yaml_raises_error(self, k8s_tools):
        """apply() should raise ValidationError for malformed YAML."""
        k8s_tools.call_tool = AsyncMock(return_value="applied")
        
        with pytest.raises(ValidationError) as exc_info:
            await k8s_tools.kubectl_apply(
                manifest="invalid: yaml: [bad",
                namespace="default"
            )
        
        assert "Invalid YAML manifest" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_apply_empty_yaml_raises_error(self, k8s_tools):
        """apply() should raise ValidationError for empty YAML."""
        k8s_tools.call_tool = AsyncMock(return_value="applied")
        
        with pytest.raises(ValidationError) as exc_info:
            await k8s_tools.kubectl_apply(
                manifest="",
                namespace="default"
            )
        
        assert "Manifest cannot be empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_apply_null_yaml_raises_error(self, k8s_tools):
        """apply() should raise ValidationError for YAML that parses to null/None."""
        k8s_tools.call_tool = AsyncMock(return_value="applied")
        
        with pytest.raises(ValidationError) as exc_info:
            await k8s_tools.kubectl_apply(
                manifest="null",
                namespace="default"
            )
        
        assert "Manifest is empty after parsing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_apply_clusterolebinding_blocked(self, k8s_tools):
        """apply() should block ClusterRoleBinding kind."""
        k8s_tools.call_tool = AsyncMock(return_value="applied")
        
        manifest = """
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: admin-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: admin
subjects:
- kind: User
  name: admin@example.com
"""
        
        with pytest.raises(ValidationError) as exc_info:
            await k8s_tools.kubectl_apply(manifest, namespace="default")
        
        error_msg = str(exc_info.value)
        assert "ClusterRoleBinding" in error_msg
        assert "Cannot apply resource kind" in error_msg

    @pytest.mark.asyncio
    async def test_apply_clusterrole_blocked(self, k8s_tools):
        """apply() should block ClusterRole kind."""
        k8s_tools.call_tool = AsyncMock(return_value="applied")
        
        manifest = """
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: admin
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["*"]
"""
        
        with pytest.raises(ValidationError) as exc_info:
            await k8s_tools.kubectl_apply(manifest, namespace="default")
        
        error_msg = str(exc_info.value)
        assert "ClusterRole" in error_msg
        assert "Cannot apply resource kind" in error_msg

    @pytest.mark.asyncio
    async def test_apply_pod_allowed(self, k8s_tools):
        """apply() should allow Pod kind."""
        k8s_tools.call_tool = AsyncMock(return_value="applied")
        
        manifest = """
apiVersion: v1
kind: Pod
metadata:
  name: test-pod
  namespace: default
spec:
  containers:
  - name: app
    image: nginx:latest
"""
        
        result = await k8s_tools.kubectl_apply(manifest, namespace="default")
        
        # Should call the MCP tool without raising
        assert k8s_tools.call_tool.called
        assert result == "applied"

    @pytest.mark.asyncio
    async def test_apply_configmap_allowed(self, k8s_tools):
        """apply() should allow ConfigMap kind."""
        k8s_tools.call_tool = AsyncMock(return_value="applied")
        
        manifest = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: default
data:
  config.yaml: |
    key: value
"""
        
        result = await k8s_tools.kubectl_apply(manifest, namespace="default")
        
        assert k8s_tools.call_tool.called
        assert result == "applied"

    @pytest.mark.asyncio
    async def test_apply_service_allowed(self, k8s_tools):
        """apply() should allow Service kind."""
        k8s_tools.call_tool = AsyncMock(return_value="applied")
        
        manifest = """
apiVersion: v1
kind: Service
metadata:
  name: app-svc
  namespace: default
spec:
  selector:
    app: myapp
  ports:
  - port: 80
    targetPort: 8080
"""
        
        result = await k8s_tools.kubectl_apply(manifest, namespace="default")
        
        assert k8s_tools.call_tool.called
        assert result == "applied"

    @pytest.mark.asyncio
    async def test_apply_multidoc_with_dangerous_kind_blocked(self, k8s_tools):
        """apply() should block multi-doc YAML if any doc contains dangerous kind."""
        k8s_tools.call_tool = AsyncMock(return_value="applied")
        
        manifest = """
---
apiVersion: v1
kind: Pod
metadata:
  name: test-pod
  namespace: default
spec:
  containers:
  - name: app
    image: nginx:latest
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: admin
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["*"]
"""
        
        with pytest.raises(ValidationError) as exc_info:
            await k8s_tools.kubectl_apply(manifest, namespace="default")
        
        error_msg = str(exc_info.value)
        assert "ClusterRole" in error_msg

    @pytest.mark.asyncio
    async def test_apply_multidoc_all_safe_allowed(self, k8s_tools):
        """apply() should allow multi-doc YAML if all docs are safe kinds."""
        k8s_tools.call_tool = AsyncMock(return_value="applied")
        
        manifest = """
---
apiVersion: v1
kind: Pod
metadata:
  name: test-pod
  namespace: default
spec:
  containers:
  - name: app
    image: nginx:latest
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: config
  namespace: default
data:
  key: value
"""
        
        result = await k8s_tools.kubectl_apply(manifest, namespace="default")
        
        assert k8s_tools.call_tool.called
        assert result == "applied"

    def test_dangerous_kinds_constant_includes_clusterolebinding(self):
        """Verify ClusterRoleBinding is in DANGEROUS_KINDS."""
        assert "ClusterRoleBinding" in DANGEROUS_KINDS

    def test_dangerous_kinds_constant_includes_clusterrole(self):
        """Verify ClusterRole is in DANGEROUS_KINDS."""
        assert "ClusterRole" in DANGEROUS_KINDS
