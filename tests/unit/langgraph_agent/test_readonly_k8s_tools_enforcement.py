"""Tests for ReadOnlyK8sTools write operation blocking.

These tests verify that ReadOnlyK8sTools properly blocks write operations
at the tool level, providing defense-in-depth for investigation agents.

SECURITY: These tests are critical for ensuring that investigation agents
(K8sInvestigator, VerificationService, Diagnostician) cannot modify cluster state.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.tools.readonly_k8s_tools import ReadOnlyK8sTools, BLOCKED_WRITE_TOOLS, READONLY_TOOLS


class TestReadOnlyK8sToolsEnforcement:
    """Test that ReadOnlyK8sTools blocks all write operations."""

    @pytest.fixture
    def readonly_tools(self):
        """Create ReadOnlyK8sTools with mocked endpoint."""
        return ReadOnlyK8sTools(mcp_endpoint="http://mock-k8s-mcp:8080")

    @pytest.mark.asyncio
    async def test_blocks_kubectl_scale(self, readonly_tools):
        """kubectl_scale should be blocked."""
        with pytest.raises(PermissionError) as exc_info:
            await readonly_tools.call_tool("kubectl_scale", {
                "namespace": "production",
                "deployment": "api-server",
                "replicas": 3
            })

        assert "kubectl_scale" in str(exc_info.value)
        assert "not permitted" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_blocks_kubectl_rollout_restart(self, readonly_tools):
        """kubectl_rollout_restart should be blocked."""
        with pytest.raises(PermissionError) as exc_info:
            await readonly_tools.call_tool("kubectl_rollout_restart", {
                "namespace": "production",
                "deployment": "api-server"
            })

        assert "kubectl_rollout_restart" in str(exc_info.value)
        assert "not permitted" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_blocks_kubectl_apply(self, readonly_tools):
        """kubectl_apply should be blocked."""
        with pytest.raises(PermissionError) as exc_info:
            await readonly_tools.call_tool("kubectl_apply", {
                "namespace": "production",
                "manifest": "apiVersion: v1\nkind: ConfigMap"
            })

        assert "kubectl_apply" in str(exc_info.value)
        assert "not permitted" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_blocks_kubectl_delete(self, readonly_tools):
        """kubectl_delete should be blocked."""
        with pytest.raises(PermissionError) as exc_info:
            await readonly_tools.call_tool("kubectl_delete", {
                "namespace": "production",
                "resource_type": "pod",
                "name": "api-server-xyz123"
            })

        assert "kubectl_delete" in str(exc_info.value)
        assert "not permitted" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_blocks_kubectl_exec(self, readonly_tools):
        """kubectl_exec should be blocked (future-proofing)."""
        with pytest.raises(PermissionError) as exc_info:
            await readonly_tools.call_tool("kubectl_exec", {
                "namespace": "production",
                "pod": "api-server-xyz123",
                "command": "cat /etc/passwd"
            })

        assert "kubectl_exec" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_blocks_kubectl_patch(self, readonly_tools):
        """kubectl_patch should be blocked (future-proofing)."""
        with pytest.raises(PermissionError) as exc_info:
            await readonly_tools.call_tool("kubectl_patch", {
                "namespace": "production",
                "resource_type": "deployment",
                "name": "api-server",
                "patch": '{"spec": {"replicas": 5}}'
            })

        assert "kubectl_patch" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_blocks_unknown_tools(self, readonly_tools):
        """Unknown tools should be blocked by default (security default)."""
        with pytest.raises(PermissionError) as exc_info:
            await readonly_tools.call_tool("kubectl_drain", {
                "node": "worker-1"
            })

        assert "not permitted" in str(exc_info.value).lower()


class TestReadOnlyK8sToolsAllowedOperations:
    """Test that ReadOnlyK8sTools allows read operations."""

    @pytest.fixture
    def readonly_tools(self):
        """Create ReadOnlyK8sTools with mocked base method."""
        tools = ReadOnlyK8sTools(mcp_endpoint="http://mock-k8s-mcp:8080")
        # Mock the parent's call_tool to simulate successful MCP calls
        tools._mock_result = "mock result"
        return tools

    def test_readonly_tools_constant_contains_expected_tools(self):
        """READONLY_TOOLS should contain all allowed read operations."""
        expected = {
            "kubectl_get_pods",
            "kubectl_get_pods_all_namespaces",
            "kubectl_get_deployments",
            "kubectl_get_deployments_all_namespaces",
            "kubectl_get_services",
            "kubectl_get_services_all_namespaces",
            "kubectl_logs",
            "kubectl_events",
            "kubectl_describe_pod",
        }
        assert READONLY_TOOLS == expected

    def test_blocked_write_tools_constant_complete(self):
        """BLOCKED_WRITE_TOOLS should contain all dangerous operations."""
        # Verify it includes the operations mentioned in requirements
        dangerous_ops = {
            "kubectl_scale",
            "kubectl_rollout_restart",
            "kubectl_apply",
            "kubectl_delete",
            "kubectl_exec",
            "kubectl_patch",
            "kubectl_edit",
            "kubectl_drain",
            "kubectl_cordon",
            "kubectl_taint",
            "kubectl_create",
            "kubectl_replace",
        }
        for op in dangerous_ops:
            assert op in BLOCKED_WRITE_TOOLS, f"{op} should be in BLOCKED_WRITE_TOOLS"


class TestK8sInvestigatorUsesReadOnlyTools:
    """Test that K8sInvestigator is properly configured with ReadOnlyK8sTools."""

    def test_k8s_investigator_type_hint_requires_readonly_tools(self):
        """K8sInvestigator should require ReadOnlyK8sTools type."""
        from src.agents.k8s_investigator import K8sInvestigator
        import inspect

        # Get the __init__ signature
        sig = inspect.signature(K8sInvestigator.__init__)
        k8s_tools_param = sig.parameters.get("k8s_tools")

        assert k8s_tools_param is not None
        # The annotation should be ReadOnlyK8sTools
        assert "ReadOnlyK8sTools" in str(k8s_tools_param.annotation)

    def test_k8s_investigator_imports_readonly_tools(self):
        """K8sInvestigator module should import ReadOnlyK8sTools."""
        from src.agents import k8s_investigator

        # Check the import statement exists
        assert hasattr(k8s_investigator, 'ReadOnlyK8sTools') or \
               'readonly_k8s_tools' in str(k8s_investigator.__file__).lower() or \
               any('ReadOnlyK8sTools' in line
                   for line in open(k8s_investigator.__file__).read().split('\n')
                   if 'import' in line)


class TestSupervisorToolSeparation:
    """Test that supervisor properly separates read/write tools."""

    def test_supervisor_creates_readonly_tools_for_investigator(self):
        """Supervisor should inject ReadOnlyK8sTools to K8sInvestigator."""
        from unittest.mock import MagicMock, patch
        from src.supervisor import create_conditional_supervisor_graph
        from src.tools.k8s_tools import K8sTools
        from src.tools.readonly_k8s_tools import ReadOnlyK8sTools

        mock_jira_tools = MagicMock()
        mock_k8s_tools = MagicMock(spec=K8sTools)
        mock_k8s_tools.endpoint = "http://k8s-mcp:8080"

        # Patch K8sInvestigator to capture what tools it receives
        captured_tools = []

        original_init = None

        def mock_investigator_init(self, k8s_tools):
            captured_tools.append(k8s_tools)
            self.llm = MagicMock()
            self.tools = k8s_tools

        with patch('src.supervisor.K8sInvestigator.__init__', mock_investigator_init):
            with patch('src.supervisor.K8sRemediationExecutor'):
                with patch('src.supervisor.JiraAgent'):
                    with patch('src.supervisor.Diagnostician'):
                        with patch('src.supervisor.VerificationService'):
                            graph = create_conditional_supervisor_graph(
                                mock_jira_tools,
                                mock_k8s_tools,
                            )

        # Verify K8sInvestigator received ReadOnlyK8sTools
        assert len(captured_tools) == 1
        assert isinstance(captured_tools[0], ReadOnlyK8sTools)

    def test_supervisor_uses_full_k8s_tools_for_remediation(self):
        """Supervisor should inject full K8sTools to K8sRemediationExecutor."""
        from unittest.mock import MagicMock, patch
        from src.supervisor import create_conditional_supervisor_graph
        from src.tools.k8s_tools import K8sTools

        mock_jira_tools = MagicMock()
        mock_k8s_tools = MagicMock(spec=K8sTools)
        mock_k8s_tools.endpoint = "http://k8s-mcp:8080"

        # Patch K8sRemediationExecutor to capture what tools it receives
        captured_tools = []

        def mock_remediation_init(self, k8s_tools, lock_service=None):
            captured_tools.append(k8s_tools)
            self.llm = MagicMock()
            self.tools = k8s_tools
            self.lock_service = lock_service or MagicMock()

        with patch('src.supervisor.K8sRemediationExecutor.__init__', mock_remediation_init):
            with patch('src.supervisor.K8sInvestigator'):
                with patch('src.supervisor.JiraAgent'):
                    with patch('src.supervisor.Diagnostician'):
                        with patch('src.supervisor.VerificationService'):
                            graph = create_conditional_supervisor_graph(
                                mock_jira_tools,
                                mock_k8s_tools,
                            )

        # Verify K8sRemediationExecutor received full K8sTools (the mock, not ReadOnlyK8sTools)
        assert len(captured_tools) == 1
        assert captured_tools[0] is mock_k8s_tools  # Should be the same object


class TestDiagnosticianHasNoK8sTools:
    """Test that Diagnostician has no direct K8s tool access."""

    def test_diagnostician_init_has_no_k8s_tools_parameter(self):
        """Diagnostician should not accept k8s_tools parameter."""
        from src.agents.diagnostician import Diagnostician
        import inspect

        sig = inspect.signature(Diagnostician.__init__)
        param_names = list(sig.parameters.keys())

        # Should only have 'self' and 'remediation_agent'
        assert 'k8s_tools' not in param_names
        assert 'tools' not in param_names

    def test_diagnostician_has_no_tools_attribute(self):
        """Diagnostician instance should not have a tools attribute."""
        from src.agents.diagnostician import Diagnostician
        from unittest.mock import MagicMock

        mock_remediation = MagicMock()
        diagnostician = Diagnostician(remediation_agent=mock_remediation)

        # Should not have tools attribute
        assert not hasattr(diagnostician, 'tools') or diagnostician.tools is None \
               or getattr(diagnostician, 'tools', 'missing') == 'missing'
