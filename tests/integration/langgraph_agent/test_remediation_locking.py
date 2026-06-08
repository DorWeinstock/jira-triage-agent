"""Integration tests for remediation locking.

Tests that the K8sRemediationExecutor properly acquires and releases locks
when executing remediation plans.

Note: K8sRemediationExecutor no longer has an LLM - it's a pure executor.
It receives a structured RemediationPlan from the Diagnostician.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agents.k8s_remediation_executor import K8sRemediationExecutor
from src.models import ActionType, RemediationPlan
from src.state import AgentState


class TestRemediationLocking:
    """Test K8sRemediationExecutor locking behavior."""

    @pytest.fixture
    def mock_k8s_tools(self):
        """Create mock K8s tools."""
        tools = MagicMock()
        tools.call_tool = AsyncMock(return_value="deployment restarted")
        return tools

    @pytest.fixture
    def mock_lock_service(self):
        """Create mock lock service."""
        service = MagicMock()
        service.acquire_lock = AsyncMock(return_value=True)
        service.release_lock = AsyncMock()
        return service

    @pytest.fixture
    def sample_state(self) -> dict:
        """Create sample agent state for testing."""
        return {
            "ticket_id": "PROJ-123",
            "thread_id": "thread-123",
            "namespace": "production",
            "affected_deployments": ["my-service"],
            "root_cause": "Missing ConfigMap",
            "recommended_action": "Restart the deployment",
        }

    @pytest.fixture
    def restart_plan(self) -> RemediationPlan:
        """Create a restart remediation plan."""
        return RemediationPlan(
            remediation_possible=True,
            action=ActionType.RESTART,
            resource_type="deployment",
            name="my-service",
            namespace="production",
            reason="Restart to clear memory and pick up config changes",
        )

    @pytest.mark.asyncio
    async def test_execute_plan_acquires_lock(
        self, mock_k8s_tools, mock_lock_service, sample_state, restart_plan
    ):
        """Test that execute_plan acquires lock before remediation."""
        agent = K8sRemediationExecutor(mock_k8s_tools, lock_service=mock_lock_service)

        await agent.execute_plan(sample_state, restart_plan)

        mock_lock_service.acquire_lock.assert_called_once()
        # Verify the lock was acquired with correct parameters
        call_kwargs = mock_lock_service.acquire_lock.call_args.kwargs
        assert call_kwargs["resource_type"] == "deployment"
        assert call_kwargs["name"] == "my-service"
        assert call_kwargs["namespace"] == "production"
        assert call_kwargs["ticket_id"] == "PROJ-123"

    @pytest.mark.asyncio
    async def test_execute_plan_skips_when_locked(
        self, mock_k8s_tools, mock_lock_service, sample_state, restart_plan
    ):
        """Test that execute_plan skips remediation when resource is locked."""
        from src.exceptions import LockAcquisitionError

        mock_lock_service.acquire_lock = AsyncMock(
            side_effect=LockAcquisitionError(
                "Resource locked",
                resource_key="deployment--production--my-service",
                locked_by="PROJ-456",
            )
        )
        agent = K8sRemediationExecutor(mock_k8s_tools, lock_service=mock_lock_service)

        result = await agent.execute_plan(sample_state, restart_plan)

        assert result["success"] is False
        assert "locked" in result["error"].lower()
        assert "PROJ-456" in result["error"]
        mock_k8s_tools.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_plan_releases_lock_on_success(
        self, mock_k8s_tools, mock_lock_service, sample_state, restart_plan
    ):
        """Test that execute_plan releases lock after successful remediation."""
        agent = K8sRemediationExecutor(mock_k8s_tools, lock_service=mock_lock_service)

        await agent.execute_plan(sample_state, restart_plan)

        mock_lock_service.release_lock.assert_called_once()
        # Verify the lock was released with correct parameters
        call_kwargs = mock_lock_service.release_lock.call_args.kwargs
        assert call_kwargs["resource_type"] == "deployment"
        assert call_kwargs["name"] == "my-service"
        assert call_kwargs["namespace"] == "production"
        assert call_kwargs["ticket_id"] == "PROJ-123"

    @pytest.mark.asyncio
    async def test_execute_plan_releases_lock_on_failure(
        self, mock_k8s_tools, mock_lock_service, sample_state, restart_plan
    ):
        """Test that execute_plan releases lock even on K8s failure."""
        mock_k8s_tools.call_tool = AsyncMock(side_effect=Exception("K8s error"))
        agent = K8sRemediationExecutor(mock_k8s_tools, lock_service=mock_lock_service)

        result = await agent.execute_plan(sample_state, restart_plan)

        assert result["success"] is False
        assert "error" in result
        mock_lock_service.release_lock.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_plan_no_lock_for_manual_intervention(
        self, mock_k8s_tools, mock_lock_service, sample_state
    ):
        """Test that manual intervention actions don't acquire locks."""
        manual_plan = RemediationPlan(
            remediation_possible=False,
            action=ActionType.MANUAL_INTERVENTION,
            resource_type="deployment",
            name="complex-service",
            namespace="production",
            reason="Requires DBA intervention",
            manual_instructions="Contact DBA team to fix the database",
        )
        agent = K8sRemediationExecutor(mock_k8s_tools, lock_service=mock_lock_service)

        await agent.execute_plan(sample_state, manual_plan)

        mock_lock_service.acquire_lock.assert_not_called()
        mock_lock_service.release_lock.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_plan_scale_action(
        self, mock_k8s_tools, mock_lock_service, sample_state
    ):
        """Test that scale action executes correctly."""
        scale_plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.SCALE,
            resource_type="deployment",
            name="my-service",
            namespace="production",
            replicas=2,
            reason="Scale up from 0 to restore service",
        )
        agent = K8sRemediationExecutor(mock_k8s_tools, lock_service=mock_lock_service)

        result = await agent.execute_plan(sample_state, scale_plan)

        # Verify K8s tool was called with correct parameters
        mock_k8s_tools.call_tool.assert_called_once()
        call_args = mock_k8s_tools.call_tool.call_args
        assert call_args[0][0] == "kubectl_scale"
        assert call_args[0][1]["deployment"] == "my-service"
        assert call_args[0][1]["replicas"] == 2
        assert result["action_taken"] == "Scaled deployment my-service to 2 replicas"
