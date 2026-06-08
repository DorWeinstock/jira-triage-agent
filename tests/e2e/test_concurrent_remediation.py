"""E2E tests for concurrent remediation locking."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.remediation_lock_service import RemediationLockService
from src.exceptions import LockAcquisitionError


@pytest.mark.e2e
class TestConcurrentRemediation:
    """E2E tests for concurrent remediation scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_lock_acquisition(self):
        """Test that only one of two concurrent acquires succeeds."""
        # This test simulates two tickets trying to lock the same resource

        # Create a shared lock state
        from src.services.remediation_lock_service import LockEntry
        from datetime import datetime, timedelta, timezone

        locks = {}

        async def mock_read_locks(self):
            return locks.copy()

        async def mock_write_locks(self, new_locks):
            nonlocal locks
            locks = new_locks.copy()

        service1 = RemediationLockService()
        service2 = RemediationLockService()

        with patch.object(
            RemediationLockService, "_read_locks", mock_read_locks
        ), patch.object(RemediationLockService, "_write_locks", mock_write_locks):

            # First acquisition should succeed
            result1 = await service1.acquire_lock(
                "deployment", "default", "my-service", "PROJ-123", "thread-123"
            )
            assert result1 is True

            # Second acquisition should fail with LockAcquisitionError
            with pytest.raises(LockAcquisitionError) as exc_info:
                await service2.acquire_lock(
                    "deployment", "default", "my-service", "PROJ-456", "thread-456"
                )

            # Verify error message contains the locking ticket
            assert "PROJ-123" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_different_resources_both_lock(self):
        """Test that different resources can be locked concurrently."""
        # Create a shared lock state
        locks = {}

        async def mock_read_locks(self):
            return locks.copy()

        async def mock_write_locks(self, new_locks):
            nonlocal locks
            locks = new_locks.copy()

        service = RemediationLockService()

        with patch.object(
            RemediationLockService, "_read_locks", mock_read_locks
        ), patch.object(RemediationLockService, "_write_locks", mock_write_locks):
            result1 = await service.acquire_lock(
                "deployment", "default", "service-a", "PROJ-123", "thread-123"
            )
            result2 = await service.acquire_lock(
                "deployment", "default", "service-b", "PROJ-456", "thread-456"
            )

        # Both should succeed (different resources)
        assert result1 is True
        assert result2 is True
