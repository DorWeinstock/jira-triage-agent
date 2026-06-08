"""Integration tests for concurrent remediation at the agent level.

This test simulates two AgentRuns processing different tickets that both
try to remediate the same K8s resource (e.g., restart the same deployment).
Only one should succeed; the other should skip due to lock.

Note: K8sRemediationExecutor is a PURE EXECUTOR with NO LLM calls.
It receives a structured RemediationPlan from the Diagnostician.
"""

import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.k8s_remediation_executor import K8sRemediationExecutor
from src.services.remediation_lock_service import RemediationLockService
from src.state import AgentState
from src.models import RemediationPlan, ActionType


class TestConcurrentAgentRemediation:
    """Test two agents trying to remediate the same resource concurrently."""

    @pytest.fixture
    def mock_k8s_tools(self):
        """Create mock K8s tools that simulate successful actions."""
        tools = MagicMock()
        tools.call_tool = AsyncMock(
            return_value="deployment.apps/my-service restarted"
        )
        return tools

    @pytest.fixture
    def restart_my_service_plan(self) -> RemediationPlan:
        """Create a restart plan for my-service."""
        return RemediationPlan(
            remediation_possible=True,
            action=ActionType.RESTART,
            resource_type="deployment",
            namespace="production",
            name="my-service",
            reason="Restart to clear memory and recover from OOM",
        )

    @pytest.fixture
    def state_ticket_123(self) -> dict:
        """State for first ticket targeting my-service deployment."""
        return {
            "ticket_id": "PROJ-123",
            "thread_id": "thread-123",
            "namespace": "production",
            "root_cause": "Pod crash due to OOM",
            "recommended_action": "Restart deployment my-service",
            "confidence_level": "high",
        }

    @pytest.fixture
    def state_ticket_456(self) -> dict:
        """State for second ticket targeting the SAME my-service deployment."""
        return {
            "ticket_id": "PROJ-456",
            "thread_id": "thread-456",
            "namespace": "production",
            "root_cause": "Application not responding",
            "recommended_action": "Restart deployment my-service",
            "confidence_level": "high",
        }

    @pytest.mark.asyncio
    async def test_second_agent_blocked_when_resource_locked(
        self, mock_k8s_tools, restart_my_service_plan, state_ticket_456
    ):
        """Test that second agent is blocked when resource is already locked by first."""
        # Pre-populate lock as if first agent already acquired it
        lock_key = "deployment--production--my-service"
        # Lock structure: (expiry, ticket_id, thread_id)
        existing_lock = (
            datetime.now(timezone.utc) + timedelta(minutes=30),  # expiry
            "PROJ-123",  # ticket_id
            "thread-123",  # thread_id
        )
        locks = {lock_key: existing_lock}

        async def mock_read_locks(self):
            return locks.copy()

        async def mock_write_locks(self, new_locks):
            nonlocal locks
            locks = new_locks.copy()

        with patch.object(
            RemediationLockService, "_read_locks", mock_read_locks
        ), patch.object(
            RemediationLockService, "_write_locks", mock_write_locks
        ):
            lock_service = RemediationLockService()
            agent = K8sRemediationExecutor(mock_k8s_tools, lock_service=lock_service)

            # Second agent tries to remediate - should be blocked
            result = await agent.execute_plan(state_ticket_456, restart_my_service_plan)

        # Should fail due to lock
        assert result["success"] is False, f"Second agent should fail: {result}"
        assert "locked" in result.get("error", "").lower() or "PROJ-123" in result.get(
            "error", ""
        ), f"Error should mention lock or blocking ticket: {result}"

    @pytest.mark.asyncio
    async def test_concurrent_remediation_different_resources_both_succeed(
        self, mock_k8s_tools
    ):
        """Test that different resources can be remediated concurrently."""
        locks = {}

        async def mock_read_locks(self):
            return locks.copy()

        async def mock_write_locks(self, new_locks):
            nonlocal locks
            locks = new_locks.copy()

        state_service_a = {
            "ticket_id": "PROJ-123",
            "thread_id": "thread-123",
            "namespace": "production",
            "root_cause": "OOM on service-a",
            "recommended_action": "Restart deployment service-a",
            "confidence_level": "high",
        }

        state_service_b = {
            "ticket_id": "PROJ-456",
            "thread_id": "thread-456",
            "namespace": "production",
            "root_cause": "OOM on service-b",
            "recommended_action": "Restart deployment service-b",
            "confidence_level": "high",
        }

        plan_a = RemediationPlan(
            remediation_possible=True,
            action=ActionType.RESTART,
            resource_type="deployment",
            namespace="production",
            name="service-a",
            reason="Restart service-a",
        )

        plan_b = RemediationPlan(
            remediation_possible=True,
            action=ActionType.RESTART,
            resource_type="deployment",
            namespace="production",
            name="service-b",
            reason="Restart service-b",
        )

        with patch.object(
            RemediationLockService, "_read_locks", mock_read_locks
        ), patch.object(
            RemediationLockService, "_write_locks", mock_write_locks
        ):
            lock_service_1 = RemediationLockService()
            lock_service_2 = RemediationLockService()

            agent1 = K8sRemediationExecutor(mock_k8s_tools, lock_service=lock_service_1)
            agent2 = K8sRemediationExecutor(mock_k8s_tools, lock_service=lock_service_2)

            result1 = await agent1.execute_plan(state_service_a, plan_a)
            result2 = await agent2.execute_plan(state_service_b, plan_b)

        # Both should succeed (different resources)
        assert result1["success"] is True, f"First agent should succeed: {result1}"
        assert result2["success"] is True, f"Second agent should succeed: {result2}"

    @pytest.mark.asyncio
    async def test_truly_concurrent_execution_with_delay(self, mock_k8s_tools):
        """Test truly concurrent execution where operations overlap."""
        locks = {}
        lock_mutex = asyncio.Lock()

        async def mock_read_locks(self):
            async with lock_mutex:
                return {k: v for k, v in locks.items()}

        async def mock_write_locks(self, new_locks):
            nonlocal locks
            async with lock_mutex:
                locks = {k: v for k, v in new_locks.items()}

        state_1 = {
            "ticket_id": "PROJ-111",
            "thread_id": "thread-111",
            "namespace": "production",
            "root_cause": "Issue 1",
            "recommended_action": "Restart deployment shared-service",
            "confidence_level": "high",
        }

        state_2 = {
            "ticket_id": "PROJ-222",
            "thread_id": "thread-222",
            "namespace": "production",
            "root_cause": "Issue 2",
            "recommended_action": "Restart deployment shared-service",
            "confidence_level": "high",
        }

        # Both agents target the same service
        shared_plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.RESTART,
            resource_type="deployment",
            namespace="production",
            name="shared-service",
            reason="Restart shared-service",
        )

        # Create a slow K8s tool that holds the lock longer
        slow_k8s_tools = MagicMock()
        call_count = [0]

        async def slow_call_tool(*args, **kwargs):
            call_count[0] += 1
            # First call takes longer, second call is fast
            if call_count[0] == 1:
                await asyncio.sleep(0.1)  # Hold lock for 100ms
            return "deployment.apps/shared-service restarted"

        slow_k8s_tools.call_tool = slow_call_tool

        with patch.object(
            RemediationLockService, "_read_locks", mock_read_locks
        ), patch.object(
            RemediationLockService, "_write_locks", mock_write_locks
        ):
            lock_service_1 = RemediationLockService()
            lock_service_2 = RemediationLockService()

            agent1 = K8sRemediationExecutor(slow_k8s_tools, lock_service=lock_service_1)
            agent2 = K8sRemediationExecutor(slow_k8s_tools, lock_service=lock_service_2)

            # Start first agent
            task1 = asyncio.create_task(
                agent1.execute_plan(state_1, shared_plan)
            )

            # Small delay to ensure first agent acquires lock first
            await asyncio.sleep(0.01)

            # Start second agent while first is still holding the lock
            task2 = asyncio.create_task(
                agent2.execute_plan(state_2, shared_plan)
            )

            # Wait for both
            result1, result2 = await asyncio.gather(task1, task2)

        # First should succeed, second should fail due to lock
        assert result1["success"] is True, f"First agent should succeed: {result1}"
        assert result2["success"] is False, f"Second agent should fail: {result2}"
        assert "locked" in result2.get("error", "").lower() or "PROJ-111" in result2.get(
            "error", ""
        ), f"Error should mention lock: {result2}"

    @pytest.mark.asyncio
    async def test_lock_released_after_success(self, mock_k8s_tools):
        """Test that lock is released after successful remediation, allowing second attempt."""
        locks = {}

        async def mock_read_locks(self):
            return locks.copy()

        async def mock_write_locks(self, new_locks):
            nonlocal locks
            locks = new_locks.copy()

        state_1 = {
            "ticket_id": "PROJ-FIRST",
            "thread_id": "thread-first",
            "namespace": "production",
            "root_cause": "First issue",
            "recommended_action": "Restart deployment target-service",
            "confidence_level": "high",
        }

        state_2 = {
            "ticket_id": "PROJ-SECOND",
            "thread_id": "thread-second",
            "namespace": "production",
            "root_cause": "Second issue (different ticket)",
            "recommended_action": "Restart deployment target-service",
            "confidence_level": "high",
        }

        plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.RESTART,
            resource_type="deployment",
            namespace="production",
            name="target-service",
            reason="Restart target-service",
        )

        with patch.object(
            RemediationLockService, "_read_locks", mock_read_locks
        ), patch.object(
            RemediationLockService, "_write_locks", mock_write_locks
        ):
            lock_service = RemediationLockService()
            agent = K8sRemediationExecutor(mock_k8s_tools, lock_service=lock_service)

            # First remediation succeeds and releases lock
            result1 = await agent.execute_plan(state_1, plan)
            assert result1["success"] is True

            # Second remediation should also succeed (lock was released)
            result2 = await agent.execute_plan(state_2, plan)
            assert (
                result2["success"] is True
            ), f"Second attempt should succeed after lock release: {result2}"

    @pytest.mark.asyncio
    async def test_same_ticket_can_reacquire_own_lock(self, mock_k8s_tools):
        """Test that the same ticket can re-acquire its own lock (idempotent)."""
        lock_key = "deployment--production--my-service"
        # Lock structure: (expiry, ticket_id, thread_id)
        existing_lock = (
            datetime.now(timezone.utc) + timedelta(minutes=30),  # expiry
            "PROJ-123",  # ticket_id
            "thread-123",  # thread_id
        )
        locks = {lock_key: existing_lock}

        async def mock_read_locks(self):
            return locks.copy()

        async def mock_write_locks(self, new_locks):
            nonlocal locks
            locks = new_locks.copy()

        state = {
            "ticket_id": "PROJ-123",  # Same ticket that owns the lock
            "thread_id": "thread-123",
            "namespace": "production",
            "root_cause": "Retry after failure",
            "recommended_action": "Restart deployment my-service",
            "confidence_level": "high",
        }

        plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.RESTART,
            resource_type="deployment",
            namespace="production",
            name="my-service",
            reason="Restart my-service",
        )

        with patch.object(
            RemediationLockService, "_read_locks", mock_read_locks
        ), patch.object(
            RemediationLockService, "_write_locks", mock_write_locks
        ):
            lock_service = RemediationLockService()
            agent = K8sRemediationExecutor(mock_k8s_tools, lock_service=lock_service)

            # Same ticket should be able to proceed (it owns the lock)
            result = await agent.execute_plan(state, plan)

        assert result["success"] is True, f"Same ticket should succeed: {result}"


class TestK8sRemediationExecutorHasNoLLM:
    """Verify K8sRemediationExecutor is a pure executor without LLM."""

    def test_executor_has_no_llm_attribute(self):
        """K8sRemediationExecutor should not have an LLM attribute."""
        mock_k8s_tools = MagicMock()
        executor = K8sRemediationExecutor(mock_k8s_tools)

        # Should not have llm attribute
        assert not hasattr(executor, 'llm'), "K8sRemediationExecutor should not have an LLM"

    def test_executor_only_has_tools_and_lock_service(self):
        """K8sRemediationExecutor should only have tools and lock_service."""
        mock_k8s_tools = MagicMock()
        mock_lock_service = MagicMock()
        executor = K8sRemediationExecutor(mock_k8s_tools, lock_service=mock_lock_service)

        # Should have tools and lock_service
        assert executor.tools is mock_k8s_tools
        assert executor.lock_service is mock_lock_service

        # Should NOT have llm
        assert not hasattr(executor, 'llm')
