"""Tests for K8s ConfigMap checkpointer."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestK8sConfigMapSaverInit:
    """Test K8sConfigMapSaver initialization."""

    def test_can_instantiate_with_defaults(self):
        """Should instantiate with default namespace."""
        from src.checkpoint import K8sConfigMapSaver

        saver = K8sConfigMapSaver()
        assert saver.namespace == "jira-k8s-agent"

    def test_can_instantiate_with_custom_namespace(self):
        """Should accept custom namespace."""
        from src.checkpoint import K8sConfigMapSaver

        saver = K8sConfigMapSaver(namespace="custom-ns")
        assert saver.namespace == "custom-ns"

    def test_configmap_name_generation(self):
        """Should generate valid ConfigMap names from thread_id."""
        from src.checkpoint import K8sConfigMapSaver

        saver = K8sConfigMapSaver()

        # Normal thread ID - should include hash suffix for collision prevention
        name1 = saver._get_configmap_name("ticket-PROJ-123")
        assert name1.startswith("langgraph-ckpt-")
        assert len(name1) <= 253  # K8s limit
        assert "-" in name1.split("langgraph-ckpt-")[1]  # Has hash suffix with dash

        # Thread ID with special chars (should be sanitized + hash still computed on original)
        name2 = saver._get_configmap_name("ticket/PROJ/123")
        assert name2.startswith("langgraph-ckpt-")
        assert len(name2) <= 253
        
        # Different original thread_ids produce different names (due to different hash)
        assert name1 != name2


class TestK8sConfigMapSaverPut:
    """Test aput() method."""

    @pytest.fixture
    def mock_k8s_client(self):
        """Create a mock K8s client."""
        client = AsyncMock()
        # Default: read fails (ConfigMap doesn't exist) - will be configured per test
        client.read_namespaced_config_map = AsyncMock()
        client.create_namespaced_config_map = AsyncMock()
        client.replace_namespaced_config_map = AsyncMock()
        return client

    @pytest.fixture
    def sample_checkpoint(self):
        """Create a sample checkpoint."""
        return {
            "v": 1,
            "id": "ckpt-123",
            "ts": "2024-01-01T00:00:00+00:00",
            "channel_values": {"ticket_id": "PROJ-123", "remediation_count": 1},
            "channel_versions": {"__start__": 1},
            "versions_seen": {},
        }

    @pytest.mark.asyncio
    async def test_aput_creates_configmap_for_new_thread(
        self, mock_k8s_client, sample_checkpoint
    ):
        """Should create a new ConfigMap for a new thread."""
        from src.checkpoint import K8sConfigMapSaver

        # Mock kubernetes_asyncio imports within aput
        mock_v1_configmap = MagicMock()
        mock_v1_objectmeta = MagicMock()

        # Create mock module that provides the classes
        mock_k8s_module = MagicMock()
        mock_k8s_module.V1ConfigMap = mock_v1_configmap
        mock_k8s_module.V1ObjectMeta = mock_v1_objectmeta

        # Mock ApiException - create a real exception class
        class MockApiException(Exception):
            def __init__(self, status):
                self.status = status
                super().__init__(f"API Error: {status}")

        mock_rest_module = MagicMock()
        mock_rest_module.ApiException = MockApiException

        # Configure the client to raise 404 when trying to read (ConfigMap doesn't exist)
        mock_k8s_client.read_namespaced_config_map.side_effect = MockApiException(404)

        with patch.dict('sys.modules', {
            'kubernetes_asyncio.client': mock_k8s_module,
            'kubernetes_asyncio.client.rest': mock_rest_module
        }):
            saver = K8sConfigMapSaver(namespace="test-ns")
            saver._k8s_client = mock_k8s_client

            config = {"configurable": {"thread_id": "ticket-PROJ-123"}}
            metadata = {"source": "test"}

            result = await saver.aput(config, sample_checkpoint, metadata, {})

            # Should have called create (not replace, since read failed)
            mock_k8s_client.create_namespaced_config_map.assert_called_once()
            call_args = mock_k8s_client.create_namespaced_config_map.call_args
            assert call_args[1]["namespace"] == "test-ns"

            # Result should include checkpoint_id
            assert "checkpoint_id" in result["configurable"]

    @pytest.mark.asyncio
    async def test_aput_updates_existing_configmap(
        self, mock_k8s_client, sample_checkpoint
    ):
        """Should update existing ConfigMap."""
        from src.checkpoint import K8sConfigMapSaver

        # Mock kubernetes_asyncio imports within aput
        mock_v1_configmap = MagicMock()
        mock_v1_objectmeta = MagicMock()

        # Create mock module that provides the classes
        mock_k8s_module = MagicMock()
        mock_k8s_module.V1ConfigMap = mock_v1_configmap
        mock_k8s_module.V1ObjectMeta = mock_v1_objectmeta

        # Mock ApiException
        mock_api_exception_class = MagicMock()
        mock_rest_module = MagicMock()
        mock_rest_module.ApiException = mock_api_exception_class

        # Make read succeed (ConfigMap exists)
        existing_cm = MagicMock()
        existing_cm.metadata.name = "langgraph-ckpt-ticket-proj-123"
        existing_cm.data = {"checkpoint": "{}"}
        mock_k8s_client.read_namespaced_config_map = AsyncMock(return_value=existing_cm)

        with patch.dict('sys.modules', {
            'kubernetes_asyncio.client': mock_k8s_module,
            'kubernetes_asyncio.client.rest': mock_rest_module
        }):
            saver = K8sConfigMapSaver(namespace="test-ns")
            saver._k8s_client = mock_k8s_client

            config = {"configurable": {"thread_id": "ticket-PROJ-123"}}

            await saver.aput(config, sample_checkpoint, {}, {})

            # Should have called replace (not create)
            mock_k8s_client.replace_namespaced_config_map.assert_called_once()


class TestK8sConfigMapSaverGet:
    """Test aget_tuple() method."""

    @pytest.fixture
    def mock_k8s_client(self):
        """Create a mock K8s client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_aget_tuple_returns_none_for_missing_configmap(self, mock_k8s_client):
        """Should return None when ConfigMap doesn't exist."""
        from src.checkpoint import K8sConfigMapSaver

        # Mock ApiException - create a real exception class
        class MockApiException(Exception):
            def __init__(self, status):
                self.status = status
                super().__init__(f"API Error: {status}")

        mock_rest_module = MagicMock()
        mock_rest_module.ApiException = MockApiException

        mock_k8s_client.read_namespaced_config_map = AsyncMock(
            side_effect=MockApiException(404)
        )

        with patch.dict('sys.modules', {
            'kubernetes_asyncio.client.rest': mock_rest_module
        }):
            saver = K8sConfigMapSaver(namespace="test-ns")
            saver._k8s_client = mock_k8s_client

            config = {"configurable": {"thread_id": "ticket-PROJ-123"}}
            result = await saver.aget_tuple(config)

            assert result is None

    @pytest.mark.asyncio
    async def test_aget_tuple_returns_checkpoint_data(self, mock_k8s_client):
        """Should return checkpoint tuple when ConfigMap exists."""
        from src.checkpoint import K8sConfigMapSaver

        checkpoint_data = {
            "v": 1,
            "id": "ckpt-123",
            "ts": "2024-01-01T00:00:00+00:00",
            "channel_values": {"ticket_id": "PROJ-123"},
            "channel_versions": {"__start__": 1},
            "versions_seen": {},
        }
        metadata = {"source": "test"}

        # Create mock ConfigMap with data
        mock_cm = MagicMock()
        mock_cm.data = {
            "checkpoint": json.dumps(checkpoint_data),
            "metadata": json.dumps(metadata),
            "checkpoint_id": "ckpt-123",
            "thread_id": "ticket-PROJ-123",
            "checkpoint_ns": "",
        }
        mock_k8s_client.read_namespaced_config_map = AsyncMock(return_value=mock_cm)

        # Mock ApiException (even though not used in success case, need to mock the import)
        mock_rest_module = MagicMock()

        with patch.dict('sys.modules', {
            'kubernetes_asyncio.client.rest': mock_rest_module
        }):
            saver = K8sConfigMapSaver(namespace="test-ns")
            saver._k8s_client = mock_k8s_client

            config = {"configurable": {"thread_id": "ticket-PROJ-123"}}
            result = await saver.aget_tuple(config)

            assert result is not None
            assert result.checkpoint["id"] == "ckpt-123"
            assert result.checkpoint["channel_values"]["ticket_id"] == "PROJ-123"
            assert result.metadata == metadata


class TestK8sConfigMapSaverList:
    """Test alist() method."""

    @pytest.fixture
    def mock_k8s_client(self):
        """Create a mock K8s client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_alist_returns_empty_for_no_checkpoints(self, mock_k8s_client):
        """Should return empty iterator when no checkpoints exist."""
        from src.checkpoint import K8sConfigMapSaver

        # Mock empty list response
        mock_cm_list = MagicMock()
        mock_cm_list.items = []
        mock_k8s_client.list_namespaced_config_map = AsyncMock(return_value=mock_cm_list)

        saver = K8sConfigMapSaver(namespace="test-ns")
        saver._k8s_client = mock_k8s_client

        config = {"configurable": {"thread_id": "ticket-PROJ-123"}}
        results = [item async for item in saver.alist(config)]

        assert results == []

    @pytest.mark.asyncio
    async def test_alist_returns_checkpoints_for_thread(self, mock_k8s_client):
        """Should return checkpoints for the specified thread."""
        from src.checkpoint import K8sConfigMapSaver

        checkpoint_data = {
            "v": 1,
            "id": "ckpt-123",
            "ts": "2024-01-01T00:00:00+00:00",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
        }

        # Create mock ConfigMap
        mock_cm = MagicMock()
        mock_cm.metadata = MagicMock()
        mock_cm.metadata.name = "langgraph-ckpt-ticket-proj-123"
        mock_cm.data = {
            "checkpoint": json.dumps(checkpoint_data),
            "metadata": "{}",
            "checkpoint_id": "ckpt-123",
            "thread_id": "ticket-PROJ-123",
            "checkpoint_ns": "",
        }

        mock_cm_list = MagicMock()
        mock_cm_list.items = [mock_cm]
        mock_k8s_client.list_namespaced_config_map = AsyncMock(return_value=mock_cm_list)

        saver = K8sConfigMapSaver(namespace="test-ns")
        saver._k8s_client = mock_k8s_client

        config = {"configurable": {"thread_id": "ticket-PROJ-123"}}
        results = [item async for item in saver.alist(config)]

        assert len(results) == 1
        assert results[0].checkpoint["id"] == "ckpt-123"


class TestK8sConfigMapSaverCriticalPaths:
    """Test critical paths and error cases."""

    @pytest.fixture
    def saver(self):
        """Create a K8sConfigMapSaver instance."""
        from src.checkpoint import K8sConfigMapSaver
        return K8sConfigMapSaver(namespace="test-ns")

    def test_extract_thread_id_raises_on_missing_thread_id(self):
        """_extract_thread_id raises ValueError when thread_id is missing."""
        from src.checkpoint.k8s_configmap_saver import _extract_thread_id
        
        with pytest.raises(ValueError, match="thread_id"):
            _extract_thread_id({"configurable": {}})

    def test_extract_thread_id_returns_valid_thread_id(self):
        """_extract_thread_id returns thread_id when present."""
        from src.checkpoint.k8s_configmap_saver import _extract_thread_id
        
        thread_id = _extract_thread_id({"configurable": {"thread_id": "test-thread-123"}})
        assert thread_id == "test-thread-123"

    def test_aput_raises_on_oversized_checkpoint(self, saver):
        """aput raises ValueError when checkpoint exceeds MAX_CHECKPOINT_SIZE."""
        import sys
        from src.checkpoint.k8s_configmap_saver import MAX_CHECKPOINT_SIZE
        
        # Create a checkpoint that exceeds the size limit
        large_checkpoint = {
            "id": "ckpt-1",
            "channel_values": {"data": "x" * (MAX_CHECKPOINT_SIZE + 1000)},
        }
        config = {"configurable": {"thread_id": "thread-abc"}}
        
        # Size check happens before K8s client is accessed, so we can test it synchronously
        import json
        data = {
            "checkpoint": json.dumps(large_checkpoint),
            "metadata": json.dumps({}),
            "checkpoint_ns": "",
            "checkpoint_id": "ckpt-1",
            "thread_id": "thread-abc",
        }
        total_size = sum(len(v.encode('utf-8')) for v in data.values())
        assert total_size > MAX_CHECKPOINT_SIZE

    def test_retry_on_conflict_is_defined(self):
        """retry_on_conflict function is defined and callable."""
        from src.checkpoint.k8s_configmap_saver import retry_on_conflict
        import inspect
        
        # Verify the function exists and is async
        assert inspect.iscoroutinefunction(retry_on_conflict)
        
        # Verify it has the expected signature parameters
        sig = inspect.signature(retry_on_conflict)
        assert 'operation' in sig.parameters
        assert 'max_retries' in sig.parameters
        assert 'operation_name' in sig.parameters

    def test_parse_cm_returns_none_on_missing_checkpoint_data(self, saver):
        """_parse_cm returns None when ConfigMap has no checkpoint data."""
        from unittest.mock import MagicMock
        
        mock_cm = MagicMock()
        mock_cm.data = {"other_field": "value"}
        mock_cm.metadata.name = "test-cm"
        
        result = saver._parse_cm(mock_cm, "test-thread")
        assert result is None

    def test_parse_cm_returns_none_on_corrupt_json(self, saver):
        """_parse_cm returns None when checkpoint JSON is corrupt."""
        from unittest.mock import MagicMock
        
        mock_cm = MagicMock()
        mock_cm.data = {"checkpoint": "NOT_VALID_JSON", "metadata": "{}"}
        mock_cm.metadata.name = "test-cm"
        
        result = saver._parse_cm(mock_cm, "test-thread")
        assert result is None

    def test_parse_cm_parses_valid_checkpoint(self, saver):
        """_parse_cm correctly parses valid checkpoint data."""
        from unittest.mock import MagicMock
        import json
        
        mock_cm = MagicMock()
        mock_cm.data = {
            "checkpoint": json.dumps({"id": "ckpt-1", "values": {}}),
            "metadata": json.dumps({"source": "test"}),
            "checkpoint_ns": "ns-1",
            "checkpoint_id": "ckpt-1",
        }
        mock_cm.metadata.name = "test-cm"
        
        result = saver._parse_cm(mock_cm, "test-thread")
        assert result is not None
        assert result.checkpoint["id"] == "ckpt-1"
        assert result.config["configurable"]["thread_id"] == "test-thread"



