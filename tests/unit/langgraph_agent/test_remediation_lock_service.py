"""Unit tests for RemediationLockService.

Tests cover:
- Settings caching
- Resource key generation
- Input validation (empty strings, None, whitespace)
- Lock acquisition (success, collision, idempotent, expired replacement)
- Lock release (success, ownership check, idempotent)
- Lock status checks (locked, expired, missing)
- Cleanup methods
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.services.remediation_lock_service import RemediationLockService
from src.exceptions import AgentError


class TestRemediationLockService:
    """Test RemediationLockService functionality."""

    @pytest.fixture
    def lock_service(self):
        """Create lock service instance for testing."""
        with patch("src.services.remediation_lock_service.get_settings") as mock_settings:
            mock_settings.return_value.lock_enabled = True
            mock_settings.return_value.lock_default_ttl = 1800
            return RemediationLockService()

    def test_init_caches_settings(self):
        """Test that settings are cached in __init__."""
        with patch("src.services.remediation_lock_service.get_settings") as mock_get:
            mock_get.return_value.lock_enabled = True
            mock_get.return_value.lock_default_ttl = 1800
            service = RemediationLockService()
            
            # get_settings called only once during init
            mock_get.assert_called_once()
            assert service._settings is not None
            assert service.ttl_seconds == 1800

    def test_init_custom_ttl(self):
        """Test initialization with custom TTL."""
        with patch("src.services.remediation_lock_service.get_settings") as mock_settings:
            mock_settings.return_value.lock_enabled = True
            mock_settings.return_value.lock_default_ttl = 1800
            service = RemediationLockService(ttl_seconds=3600)
            
            assert service.ttl_seconds == 3600

    def test_generate_resource_key(self, lock_service):
        """Test resource key generation."""
        key = lock_service._generate_resource_key(
            resource_type="deployment",
            namespace="default",
            name="my-service",
        )
        
        assert key == "deployment--default--my-service"

    def test_generate_resource_key_lowercases(self, lock_service):
        """Test resource key lowercases all components."""
        key = lock_service._generate_resource_key(
            resource_type="Deployment",
            namespace="My-Namespace",
            name="My-Service",
        )
        
        assert key == "deployment--my-namespace--my-service"

    def test_validate_inputs_empty_string(self, lock_service):
        """Test input validation rejects empty strings."""
        with pytest.raises(ValueError, match="resource_type must be a non-empty string"):
            lock_service._validate_inputs(
                resource_type="",
                namespace="default",
                name="test",
            )

    def test_validate_inputs_none(self, lock_service):
        """Test input validation rejects None."""
        with pytest.raises(ValueError, match="resource_type must be a non-empty string"):
            lock_service._validate_inputs(
                resource_type=None,
                namespace="default",
                name="test",
            )

    def test_validate_inputs_whitespace(self, lock_service):
        """Test input validation rejects whitespace-only strings."""
        with pytest.raises(ValueError, match="namespace must be a non-empty string"):
            lock_service._validate_inputs(
                resource_type="deployment",
                namespace="   ",
                name="test",
            )

    @pytest.mark.asyncio
    async def test_acquire_lock_disabled(self):
        """Test acquire_lock returns True when locks disabled."""
        with patch("src.services.remediation_lock_service.get_settings") as mock_settings:
            mock_settings.return_value.lock_enabled = False
            service = RemediationLockService()
            
            result = await service.acquire_lock(
                resource_type="deployment",
                namespace="default",
                name="test",
                ticket_id="PROJ-123",
                thread_id="thread-abc",
            )
            
            assert result is True

    @pytest.mark.asyncio
    async def test_acquire_lock_success(self, lock_service):
        """Test successfully acquiring a lock."""
        result = await lock_service.acquire_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-123",
            thread_id="thread-abc",
        )
        
        assert result is True
        assert "deployment--default--my-service" in lock_service._locks

    @pytest.mark.asyncio
    async def test_acquire_lock_idempotent_same_ticket(self, lock_service):
        """Test acquiring lock when already owned by same ticket (idempotent)."""
        # First acquire
        await lock_service.acquire_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-123",
            thread_id="thread-abc",
        )
        
        # Second acquire with same ticket
        result = await lock_service.acquire_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-123",
            thread_id="thread-xyz",
        )
        
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_lock_blocked_different_ticket(self, lock_service):
        """Test acquiring lock when already owned by different ticket."""
        # First acquire
        await lock_service.acquire_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-123",
            thread_id="thread-abc",
        )
        
        # Second acquire with different ticket
        result = await lock_service.acquire_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-456",
            thread_id="thread-xyz",
        )
        
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_lock_replaces_expired(self, lock_service):
        """Test acquiring lock when existing lock is expired."""
        key = "deployment--default--my-service"
        
        # Manually insert an expired lock
        expired_time = datetime.now(timezone.utc) - timedelta(seconds=100)
        lock_service._locks[key] = (expired_time, "PROJ-OLD", "thread-old")
        
        # Acquire should replace it
        result = await lock_service.acquire_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-123",
            thread_id="thread-abc",
        )
        
        assert result is True
        # Verify old lock was replaced
        _, ticket_id, _ = lock_service._locks[key]
        assert ticket_id == "PROJ-123"

    @pytest.mark.asyncio
    async def test_cleanup_expired_is_sync(self, lock_service):
        """Test that _cleanup_expired is synchronous (not async)."""
        import inspect
        
        # _cleanup_expired should not be a coroutine function
        assert not inspect.iscoroutinefunction(lock_service._cleanup_expired)

    @pytest.mark.asyncio
    async def test_release_lock_disabled(self):
        """Test release_lock no-op when locks disabled."""
        with patch("src.services.remediation_lock_service.get_settings") as mock_settings:
            mock_settings.return_value.lock_enabled = False
            service = RemediationLockService()
            
            # Should not raise
            await service.release_lock(
                resource_type="deployment",
                namespace="default",
                name="test",
                ticket_id="PROJ-123",
            )

    @pytest.mark.asyncio
    async def test_release_lock_success(self, lock_service):
        """Test successfully releasing a lock."""
        key = "deployment--default--my-service"
        
        # Acquire lock
        await lock_service.acquire_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-123",
            thread_id="thread-abc",
        )
        
        assert key in lock_service._locks
        
        # Release lock
        await lock_service.release_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-123",
        )
        
        assert key not in lock_service._locks

    @pytest.mark.asyncio
    async def test_release_lock_wrong_owner_raises(self, lock_service):
        """Test releasing lock when not the owner raises AgentError."""
        # Acquire lock with one ticket
        await lock_service.acquire_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-123",
            thread_id="thread-abc",
        )
        
        # Try to release with different ticket
        with pytest.raises(AgentError) as exc_info:
            await lock_service.release_lock(
                resource_type="deployment",
                namespace="default",
                name="my-service",
                ticket_id="PROJ-456",
            )
        
        assert "PROJ-456" in str(exc_info.value)
        assert exc_info.value.context.get("locked_by") == "PROJ-123"

    @pytest.mark.asyncio
    async def test_release_lock_idempotent(self, lock_service):
        """Test releasing non-existent lock is idempotent."""
        # Should not raise
        await lock_service.release_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-123",
        )

    @pytest.mark.asyncio
    async def test_is_locked_disabled(self):
        """Test is_locked returns None when locks disabled."""
        with patch("src.services.remediation_lock_service.get_settings") as mock_settings:
            mock_settings.return_value.lock_enabled = False
            service = RemediationLockService()
            
            result = await service.is_locked(
                resource_type="deployment",
                namespace="default",
                name="test",
            )
            
            assert result is None

    @pytest.mark.asyncio
    async def test_is_locked_active(self, lock_service):
        """Test is_locked returns ticket_id when resource is locked."""
        # Acquire lock
        await lock_service.acquire_lock(
            resource_type="deployment",
            namespace="default",
            name="my-service",
            ticket_id="PROJ-123",
            thread_id="thread-abc",
        )
        
        # Check lock
        result = await lock_service.is_locked(
            resource_type="deployment",
            namespace="default",
            name="my-service",
        )
        
        assert result == "PROJ-123"

    @pytest.mark.asyncio
    async def test_is_locked_expired(self, lock_service):
        """Test is_locked returns None when lock is expired."""
        key = "deployment--default--my-service"
        
        # Manually insert an expired lock
        expired_time = datetime.now(timezone.utc) - timedelta(seconds=100)
        lock_service._locks[key] = (expired_time, "PROJ-123", "thread-abc")
        
        # Check lock - should return None (expired)
        result = await lock_service.is_locked(
            resource_type="deployment",
            namespace="default",
            name="my-service",
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_is_locked_missing(self, lock_service):
        """Test is_locked returns None when resource is not locked."""
        result = await lock_service.is_locked(
            resource_type="deployment",
            namespace="default",
            name="my-service",
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_cleanup_expired_locks_disabled(self):
        """Test cleanup_expired_locks returns 0 when locks disabled."""
        with patch("src.services.remediation_lock_service.get_settings") as mock_settings:
            mock_settings.return_value.lock_enabled = False
            service = RemediationLockService()
            
            removed_count = await service.cleanup_expired_locks()
            
            assert removed_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_expired_locks_removes_expired(self, lock_service):
        """Test cleanup_expired_locks removes expired entries."""
        now = datetime.now(timezone.utc)
        
        # Add expired lock
        expired_time = now - timedelta(seconds=100)
        lock_service._locks["deployment--default--old"] = (expired_time, "PROJ-OLD", "thread-old")
        
        # Add active lock
        active_time = now + timedelta(seconds=1800)
        lock_service._locks["deployment--default--new"] = (active_time, "PROJ-NEW", "thread-new")
        
        # Cleanup
        removed_count = await lock_service.cleanup_expired_locks()
        
        assert removed_count == 1
        assert "deployment--default--old" not in lock_service._locks
        assert "deployment--default--new" in lock_service._locks

    @pytest.mark.asyncio
    async def test_cleanup_expired_locks_no_expired(self, lock_service):
        """Test cleanup_expired_locks returns 0 when no locks are expired."""
        now = datetime.now(timezone.utc)
        
        # Add active lock
        active_time = now + timedelta(seconds=1800)
        lock_service._locks["deployment--default--new"] = (active_time, "PROJ-NEW", "thread-new")
        
        # Cleanup
        removed_count = await lock_service.cleanup_expired_locks()
        
        assert removed_count == 0
