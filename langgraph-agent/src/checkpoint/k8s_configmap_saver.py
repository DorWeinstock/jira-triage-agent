"""K8s ConfigMap-based checkpoint saver for LangGraph.

This module provides a LangGraph checkpointer that persists workflow state
to Kubernetes ConfigMaps, enabling workflow recovery after pod restarts.
"""

import asyncio
import hashlib
import json
import logging
import random
import re
from typing import Any, AsyncIterator, Callable, Iterator, List, Optional, Tuple, TypeVar

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    RunnableConfig,
)

from ..config import get_settings
from ..utils.k8s_client import K8sAsyncClientMixin

logger = logging.getLogger(__name__)

# Maximum ConfigMap data size (leaving room for metadata)
MAX_CHECKPOINT_SIZE = 900 * 1024  # 900KB (ConfigMap limit is 1MB)

# Retry configuration for handling 409 Conflict race conditions
MAX_RETRIES = 5  # Increased from 3 for high-contention scenarios
RETRY_BACKOFF_BASE = 0.05  # seconds (reduced base, exponential growth handles longer waits)
RETRY_JITTER_MAX = 0.1  # Maximum jitter to add to backoff (prevents thundering herd)

T = TypeVar("T")


def _serialize_value(value: Any) -> Any:
    """Serialize write value preserving type; fall back to str for non-JSON types.

    Args:
        value: The value to serialize

    Returns:
        The value itself if JSON-serializable, or string representation if not
    """
    try:
        json.dumps(value)  # validate JSON-serializability
        return value
    except (TypeError, ValueError):
        return str(value)


async def retry_on_conflict(
    operation: Callable[[], T],
    max_retries: int = MAX_RETRIES,
    operation_name: str = "operation",
) -> T:
    """Retry an async operation on 409 Conflict with exponential backoff and jitter.

    Uses exponential backoff with random jitter to handle Kubernetes optimistic
    concurrency control conflicts. Each retry should re-fetch the resource to
    obtain the latest resourceVersion before attempting the update.

    Args:
        operation: Async callable to execute. IMPORTANT: This function must
            re-read the Kubernetes resource on each invocation to get the
            latest resourceVersion.
        max_retries: Maximum number of retry attempts (default: 5)
        operation_name: Human-readable name for logging (default: "operation")

    Returns:
        Result of the operation

    Raises:
        ApiException: If all retries exhausted or non-409 error
    """
    from kubernetes_asyncio.client.rest import ApiException

    last_error: Optional[ApiException] = None
    for attempt in range(max_retries):
        try:
            return await operation()
        except ApiException as e:
            if e.status == 409:
                last_error = e
                if attempt < max_retries - 1:
                    # Exponential backoff with jitter to prevent thundering herd
                    base_backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
                    jitter = random.uniform(0, RETRY_JITTER_MAX)
                    backoff = base_backoff + jitter
                    logger.info(
                        f"409 Conflict on {operation_name}, retrying in {backoff:.3f}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.warning(
                        f"409 Conflict on {operation_name} persisted after {max_retries} retries. "
                        f"This may indicate high contention on the ConfigMap."
                    )
            else:
                raise

    if last_error is None:
        raise RuntimeError("retry_on_conflict: All retries exhausted but no error captured")
    raise last_error


def _extract_thread_id(config: RunnableConfig) -> str:
    """Extract thread_id from config; raise ValueError with clear message if missing.
    
    Args:
        config: Run configuration containing configurable dict
    
    Returns:
        The thread_id string
    
    Raises:
        ValueError: If thread_id is not present in config["configurable"]
    """
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    if not thread_id:
        raise ValueError(
            f"config['configurable']['thread_id'] is required but was not provided. "
            f"Got configurable={configurable!r}"
        )
    return thread_id


class K8sConfigMapSaver(K8sAsyncClientMixin, BaseCheckpointSaver):
    """LangGraph checkpointer that stores state in K8s ConfigMaps.

    This checkpointer is K8s-native and requires no external database.
    It uses the kubernetes-asyncio library to interact with the K8s API.

    Attributes:
        namespace: K8s namespace for checkpoint ConfigMaps
        ttl_seconds: Time-to-live for checkpoints (0 = no TTL)
    """

    def __init__(
        self,
        namespace: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Initialize the K8s ConfigMap checkpointer.

        Args:
            namespace: K8s namespace for ConfigMaps. Defaults to CHECKPOINT_NAMESPACE.
            ttl_seconds: TTL for checkpoints. Defaults to CHECKPOINT_TTL_SECONDS.
        """
        super().__init__()
        settings = get_settings()
        self.namespace = namespace or settings.checkpoint_namespace
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else settings.checkpoint_ttl_seconds

    def _get_configmap_name(self, thread_id: str) -> str:
        """Generate a valid, collision-free K8s ConfigMap name from thread_id.

        Uses a 8-char SHA-256 suffix of the original thread_id to prevent
        collisions when two thread_ids share a long common prefix and get
        truncated to the same 253-char string.

        Args:
            thread_id: The LangGraph thread identifier

        Returns:
            A valid K8s ConfigMap name (lowercase, alphanumeric + hyphens)
        """
        # Compute 8-char hex suffix from full original thread_id
        suffix = hashlib.sha256(thread_id.encode()).hexdigest()[:8]

        # Sanitize: lowercase, replace invalid chars with hyphens
        sanitized = re.sub(r'[^a-z0-9-]', '-', thread_id.lower())
        # Remove consecutive hyphens and trim
        sanitized = re.sub(r'-+', '-', sanitized).strip('-')

        # Prefix + suffix; truncate body to fit within 253 chars total
        # Format: "langgraph-ckpt-<body>-<suffix>"
        prefix = "langgraph-ckpt-"
        max_body_len = 253 - len(prefix) - 1 - len(suffix)  # 1 for separator hyphen
        body = sanitized[:max_body_len]
        return f"{prefix}{body}-{suffix}"

    @staticmethod
    def _parse_cm(cm, thread_id: str) -> Optional["CheckpointTuple"]:
        """Parse a ConfigMap object into a CheckpointTuple.

        Returns None if the ConfigMap has no checkpoint data or data is corrupt.

        Args:
            cm: Kubernetes V1ConfigMap object
            thread_id: The LangGraph thread identifier

        Returns:
            CheckpointTuple if valid checkpoint data found, None otherwise
        """
        data = cm.data or {}
        checkpoint_str = data.get("checkpoint")
        if not checkpoint_str:
            logger.warning(f"ConfigMap {cm.metadata.name} has no checkpoint data")
            return None
        try:
            checkpoint = json.loads(checkpoint_str)
            metadata = json.loads(data.get("metadata", "{}"))
            checkpoint_ns = data.get("checkpoint_ns", "")
            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": checkpoint["id"],
                    }
                },
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=None,
                pending_writes=[],
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse checkpoint in {cm.metadata.name}: {e}")
            return None

    # Synchronous methods - not supported (raise NotImplementedError)
    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Synchronous get - not supported, use aget_tuple."""
        raise NotImplementedError("Use aget_tuple for async operations")

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """Synchronous list - not supported, use alist."""
        raise NotImplementedError("Use alist for async operations")

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Synchronous put - not supported, use aput."""
        raise NotImplementedError("Use aput for async operations")

    # Async methods - to be implemented in subsequent tasks
    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Get the latest checkpoint tuple for a thread.

        Args:
            config: Run configuration containing thread_id

        Returns:
            CheckpointTuple if found, None otherwise
        """
        from kubernetes_asyncio.client.rest import ApiException

        thread_id = _extract_thread_id(config)
        cm_name = self._get_configmap_name(thread_id)
        client = await self._get_k8s_client()

        try:
            cm = await client.read_namespaced_config_map(
                name=cm_name,
                namespace=self.namespace,
            )
        except ApiException as e:
            if e.status == 404:
                logger.debug(f"No checkpoint found for thread {thread_id}")
                return None
            raise

        # Parse checkpoint data using helper
        result = self._parse_cm(cm, thread_id)
        if result is None:
            return None
        
        logger.debug(f"Retrieved checkpoint {result.checkpoint.get('id')} for thread {thread_id}")
        return result

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """List checkpoints for a thread.

        Note: This implementation only stores the latest checkpoint per thread,
        so this will return at most one checkpoint per thread.

        Args:
            config: Run configuration (optional, can filter by thread_id)
            filter: Additional filters (not currently supported)
            before: Return checkpoints before this config (not supported)
            limit: Maximum number of checkpoints to return

        Yields:
            CheckpointTuple for each matching checkpoint
        """
        client = await self._get_k8s_client()

        # Build label selector
        label_selector = "app.kubernetes.io/managed-by=langgraph"
        if config and "configurable" in config:
            thread_id = config["configurable"].get("thread_id")
            if thread_id:
                # Filter by specific thread
                label_selector += f",langgraph.io/thread-id={thread_id[:63]}"

        try:
            cm_list = await client.list_namespaced_config_map(
                namespace=self.namespace,
                label_selector=label_selector,
                limit=limit,
            )
        except Exception as e:
            logger.error(f"Failed to list checkpoint ConfigMaps: {e}")
            return

        for cm in cm_list.items:
            thread_id = cm.data.get("thread_id", "") if cm.data else ""
            result = self._parse_cm(cm, thread_id)
            if result is not None:
                yield result

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Store a checkpoint in a K8s ConfigMap.

        Args:
            config: Run configuration containing thread_id
            checkpoint: The checkpoint data to store
            metadata: Checkpoint metadata
            new_versions: Channel version information

        Returns:
            Updated config with checkpoint_id
        """
        from kubernetes_asyncio.client import V1ConfigMap, V1ObjectMeta
        from kubernetes_asyncio.client.rest import ApiException

        thread_id = _extract_thread_id(config)
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]

        cm_name = self._get_configmap_name(thread_id)
        client = await self._get_k8s_client()

        # Serialize checkpoint data
        data = {
            "checkpoint": json.dumps(checkpoint),
            "metadata": json.dumps(metadata),
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint_id,
            "thread_id": thread_id,
        }

        # Check serialized size
        total_size = sum(len(v.encode('utf-8')) for v in data.values())
        if total_size > MAX_CHECKPOINT_SIZE:
            raise ValueError(
                f"Checkpoint size ({total_size} bytes) exceeds limit ({MAX_CHECKPOINT_SIZE} bytes)"
            )

        # Create ConfigMap object
        labels = {
            "app.kubernetes.io/managed-by": "langgraph",
            "langgraph.io/thread-id": thread_id[:63],  # Label value max 63 chars
        }

        async def do_upsert():
            """Upsert ConfigMap with proper optimistic locking.

            Re-reads the ConfigMap on each call to get the latest resourceVersion,
            which is required by Kubernetes for optimistic concurrency control.
            """
            try:
                # Try to read existing ConfigMap to get current resourceVersion
                existing_cm = await client.read_namespaced_config_map(
                    name=cm_name,
                    namespace=self.namespace,
                )
                # Preserve resourceVersion for optimistic locking - this is critical
                # to avoid 409 Conflict errors when concurrent updates occur
                existing_cm.data = data
                existing_cm.metadata.labels = labels
                logger.debug(
                    f"Updating checkpoint ConfigMap: {cm_name} "
                    f"(resourceVersion: {existing_cm.metadata.resource_version})"
                )
                await client.replace_namespaced_config_map(
                    name=cm_name,
                    namespace=self.namespace,
                    body=existing_cm,
                )
            except ApiException as e:
                if e.status == 404:
                    # Doesn't exist - create it (no resourceVersion needed for create)
                    cm = V1ConfigMap(
                        metadata=V1ObjectMeta(
                            name=cm_name,
                            namespace=self.namespace,
                            labels=labels,
                        ),
                        data=data,
                    )
                    logger.debug(f"Creating checkpoint ConfigMap: {cm_name}")
                    try:
                        await client.create_namespaced_config_map(
                            namespace=self.namespace,
                            body=cm,
                        )
                    except ApiException as create_exc:
                        if create_exc.status == 409:
                            # Another process created it concurrently; re-raise as 409
                            # so retry_on_conflict retries the full read→update path.
                            raise create_exc
                        raise
                else:
                    raise

        await retry_on_conflict(do_upsert, operation_name=f"aput({cm_name})")
        logger.info(f"Saved checkpoint {checkpoint_id} for thread {thread_id}")

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: List[Tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Store intermediate writes before checkpoint commit.

        This implementation stores writes as part of the pending_writes in the
        checkpoint ConfigMap. For simplicity, we append to existing writes.

        Args:
            config: Run configuration containing thread_id
            writes: List of (channel, value) tuples to store
            task_id: Unique identifier for the task
            task_path: Path to the task in the graph (optional)
        """
        from kubernetes_asyncio.client import V1ConfigMap, V1ObjectMeta
        from kubernetes_asyncio.client.rest import ApiException

        thread_id = _extract_thread_id(config)
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        cm_name = self._get_configmap_name(thread_id)
        client = await self._get_k8s_client()

        # Serialize writes - preserve type fidelity instead of coercing to str
        writes_data = [
            {"channel": channel, "value": _serialize_value(value), "task_id": task_id, "task_path": task_path}
            for channel, value in writes
        ]

        # Snapshot writes_data to ensure each retry attempt uses the same
        # original writes, regardless of any external mutation.
        writes_snapshot = list(writes_data)

        async def do_upsert_writes():
            """Upsert writes to ConfigMap with proper optimistic locking.

            Re-reads the ConfigMap on each call to get the latest resourceVersion,
            which is required by Kubernetes for optimistic concurrency control.
            The cm object from read_namespaced_config_map includes the resourceVersion
            in its metadata, which is preserved when passed to replace_namespaced_config_map.
            """
            try:
                # Read existing ConfigMap - preserves resourceVersion in cm.metadata
                cm = await client.read_namespaced_config_map(
                    name=cm_name,
                    namespace=self.namespace,
                )
                data = cm.data or {}

                # Append to existing writes
                existing_writes = json.loads(data.get("pending_writes", "[]"))
                existing_writes.extend(writes_snapshot)
                serialized_writes = json.dumps(existing_writes)

                # Check total ConfigMap size before writing
                current_data_size = sum(len(v.encode("utf-8")) for v in data.values() if isinstance(v, str))
                old_writes_size = len(data.get("pending_writes", "").encode("utf-8"))
                new_total = current_data_size - old_writes_size + len(serialized_writes.encode("utf-8"))
                if new_total > MAX_CHECKPOINT_SIZE:
                    raise ValueError(
                        f"pending_writes size ({new_total} bytes) would exceed limit "
                        f"({MAX_CHECKPOINT_SIZE} bytes) for thread {thread_id}"
                    )

                data["pending_writes"] = serialized_writes

                cm.data = data
                logger.debug(
                    f"Updating pending_writes in ConfigMap: {cm_name} "
                    f"(resourceVersion: {cm.metadata.resource_version}, writes_count: {len(existing_writes)})"
                )
                await client.replace_namespaced_config_map(
                    name=cm_name,
                    namespace=self.namespace,
                    body=cm,
                )
            except ApiException as e:
                if e.status == 404:
                    # Create new ConfigMap with just the writes (no resourceVersion needed)
                    writes_dict = {"pending_writes": json.dumps(writes_snapshot), "thread_id": thread_id, "checkpoint_ns": checkpoint_ns}
                    total_size = sum(len(v.encode("utf-8")) for v in writes_dict.values() if isinstance(v, str))
                    if total_size > MAX_CHECKPOINT_SIZE:
                        raise ValueError(
                            f"pending_writes size ({total_size} bytes) exceeds limit "
                            f"({MAX_CHECKPOINT_SIZE} bytes) for thread {thread_id}"
                        )
                    labels = {
                        "app.kubernetes.io/managed-by": "langgraph",
                        "langgraph.io/thread-id": thread_id[:63],
                    }
                    cm = V1ConfigMap(
                        metadata=V1ObjectMeta(
                            name=cm_name,
                            namespace=self.namespace,
                            labels=labels,
                        ),
                        data=writes_dict,
                    )
                    logger.debug(f"Creating ConfigMap for pending_writes: {cm_name}")
                    try:
                        await client.create_namespaced_config_map(
                            namespace=self.namespace,
                            body=cm,
                        )
                    except ApiException as create_exc:
                        if create_exc.status == 409:
                            # Another process created it concurrently; re-raise as 409
                            # so retry_on_conflict retries the full read→update path.
                            raise create_exc
                        raise
                else:
                    raise

        await retry_on_conflict(do_upsert_writes, operation_name=f"aput_writes({cm_name})")
        logger.debug(f"Stored {len(writes)} writes for thread {thread_id}")
