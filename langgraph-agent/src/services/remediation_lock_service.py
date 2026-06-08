"""Simple in-memory lock service for POC.

Removed for POC:
- K8s ConfigMap-based distributed locking (420+ lines)
- Optimistic locking with resourceVersion
- K8s client dependencies
- Complex retry logic for conflict resolution

For production, consider adding:
- Redis or etcd for distributed locking
- Persistent lock storage across pod restarts
- More sophisticated lock expiration strategies
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple, Optional

from ..config import get_settings
from ..exceptions import AgentError

logger = logging.getLogger(__name__)

AGENT_NAME = "LockService"


class RemediationLockService:
    """Simple in-memory locking with TTL.

    For POC running single pod. Not suitable for multi-pod deployment.
    """

    def __init__(self, ttl_seconds: Optional[int] = None):
        """Initialize lock service.

        Args:
            ttl_seconds: Lock TTL in seconds (defaults to config value)
        """
        self._settings = get_settings()
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else self._settings.lock_default_ttl
        self._locks: Dict[str, Tuple[datetime, str, str]] = {}  # key -> (expiry, ticket_id, thread_id)
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger(__name__)

    async def close(self) -> None:
        """Clean up resources. No-op for in-memory lock service."""
        pass

    def _generate_resource_key(
        self, resource_type: str, namespace: str, name: str
    ) -> str:
        """Generate a unique key for a K8s resource.

        Args:
            resource_type: Type of resource (e.g., "deployment", "pod")
            namespace: K8s namespace
            name: Resource name

        Returns:
            Resource key like "deployment--default--my-service"
        """
        return f"{resource_type.lower()}--{namespace.lower()}--{name.lower()}"

    def _validate_inputs(self, **kwargs: str) -> None:
        """Validate that all input strings are non-empty.

        Raises:
            ValueError: If any input is None, empty, or whitespace-only.
        """
        for field, value in kwargs.items():
            if not value or not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string, got: {value!r}")

    def _cleanup_expired(self) -> None:
        """Remove expired locks (called internally before acquire)."""
        now = datetime.now(timezone.utc)
        expired = [k for k, (expiry, _, _) in self._locks.items() if now >= expiry]
        for key in expired:
            ticket_id = self._locks[key][1]
            del self._locks[key]
            logger.info(f"[{AGENT_NAME}] Lock expired: {key} (was owned by {ticket_id})")

    async def acquire_lock(
        self,
        resource_type: str,
        namespace: str,
        name: str,
        ticket_id: str,
        thread_id: str,
    ) -> bool:
        """Acquire lock on a K8s resource.

        Idempotent - acquiring same lock twice with same ticket_id succeeds.
        Expired locks are automatically replaced.

        Args:
            resource_type: Type of resource (e.g., "deployment", "pod")
            namespace: K8s namespace
            name: Resource name
            ticket_id: Jira ticket ID requesting the lock
            thread_id: LangGraph thread ID

        Returns:
            True if lock acquired, False if already locked by another ticket
        """
        self._validate_inputs(
            resource_type=resource_type,
            namespace=namespace,
            name=name,
            ticket_id=ticket_id,
            thread_id=thread_id,
        )

        if not self._settings.lock_enabled:
            return True

        resource_key = self._generate_resource_key(resource_type, namespace, name)

        async with self._lock:
            # Clean expired locks
            self._cleanup_expired()

            # Check if locked (expired locks already cleaned)
            if resource_key in self._locks:
                _, current_ticket, _ = self._locks[resource_key]
                if current_ticket == ticket_id:
                    logger.debug(f"[{AGENT_NAME}] Lock already owned: {resource_key}")
                    return True
                else:
                    logger.info(f"[{AGENT_NAME}] Lock held by {current_ticket}: {resource_key}")
                    return False

            # Acquire lock
            expiry = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
            self._locks[resource_key] = (expiry, ticket_id, thread_id)
            logger.info(f"[{AGENT_NAME}] Lock acquired by {ticket_id}: {resource_key} (TTL: {self.ttl_seconds}s)")
            return True

    async def release_lock(
        self,
        resource_type: str,
        namespace: str,
        name: str,
        ticket_id: str,
    ) -> None:
        """Release lock on a K8s resource.

        Idempotent - releasing non-existent lock succeeds.
        Only the ticket that owns the lock can release it.

        Args:
            resource_type: Type of resource
            namespace: K8s namespace
            name: Resource name
            ticket_id: Jira ticket ID releasing the lock

        Raises:
            AgentError: If lock is owned by a different ticket
        """
        self._validate_inputs(
            resource_type=resource_type,
            namespace=namespace,
            name=name,
            ticket_id=ticket_id,
        )

        if not self._settings.lock_enabled:
            return

        resource_key = self._generate_resource_key(resource_type, namespace, name)

        async with self._lock:
            if resource_key not in self._locks:
                logger.debug(f"[{AGENT_NAME}] Lock already released: {resource_key}")
                return

            _, current_ticket, _ = self._locks[resource_key]
            if current_ticket == ticket_id:
                del self._locks[resource_key]
                logger.info(f"[{AGENT_NAME}] Lock released by {ticket_id}: {resource_key}")
            else:
                raise AgentError(
                    f"Cannot release lock on {resource_key}: owned by {current_ticket}, not {ticket_id}",
                    resource_key=resource_key,
                    locked_by=current_ticket,
                )

    async def is_locked(
        self,
        resource_type: str,
        namespace: str,
        name: str,
    ) -> Optional[str]:
        """Check if a resource is locked.

        Expired locks are treated as unlocked.

        Args:
            resource_type: Type of resource
            namespace: K8s namespace
            name: Resource name

        Returns:
            Ticket ID that owns the lock, or None if unlocked/expired
        """
        self._validate_inputs(
            resource_type=resource_type,
            namespace=namespace,
            name=name,
        )

        if not self._settings.lock_enabled:
            return None

        resource_key = self._generate_resource_key(resource_type, namespace, name)

        async with self._lock:
            self._cleanup_expired()

            if resource_key in self._locks:
                expiry, ticket_id, _ = self._locks[resource_key]
                if datetime.now(timezone.utc) < expiry:
                    return ticket_id

        return None

    async def cleanup_expired_locks(self) -> int:
        """Remove all expired locks.

        Returns:
            Number of expired locks removed
        """
        if not self._settings.lock_enabled:
            return 0

        async with self._lock:
            before_count = len(self._locks)
            self._cleanup_expired()
            after_count = len(self._locks)
            removed = before_count - after_count

            if removed > 0:
                logger.info(f"[{AGENT_NAME}] Cleaned up {removed} expired locks")
            return removed
