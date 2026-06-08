"""Unit tests for checkpointer 409 Conflict retry logic."""

import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock


class MockApiException(Exception):
    """Mock kubernetes ApiException for testing."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"API Exception: {status}")


# Create mock kubernetes_asyncio module before importing the checkpointer
mock_k8s_module = MagicMock()
mock_k8s_module.client.rest.ApiException = MockApiException
mock_k8s_module.client.Configuration.get_default_copy.return_value = MagicMock()
mock_k8s_module.config.load_incluster_config = MagicMock()
mock_k8s_module.config.ConfigException = Exception
sys.modules["kubernetes_asyncio"] = mock_k8s_module
sys.modules["kubernetes_asyncio.client"] = mock_k8s_module.client
sys.modules["kubernetes_asyncio.client.rest"] = mock_k8s_module.client.rest
sys.modules["kubernetes_asyncio.config"] = mock_k8s_module.config

from src.checkpoint.k8s_configmap_saver import retry_on_conflict, MAX_RETRIES


class TestRetryOnConflict:
    """Test the retry_on_conflict helper function."""

    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self):
        """Test that operation succeeds without retry when no error."""
        operation = AsyncMock(return_value="success")

        result = await retry_on_conflict(operation)

        assert result == "success"
        assert operation.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_409_then_succeeds(self):
        """Test that operation retries on 409 and succeeds on second attempt."""
        # First call raises 409, second succeeds
        operation = AsyncMock(
            side_effect=[MockApiException(409), "success"]
        )

        # Patch asyncio.sleep to avoid actual delays
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await retry_on_conflict(operation)

        assert result == "success"
        assert operation.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_multiple_times_then_succeeds(self):
        """Test that operation retries multiple times before succeeding."""
        # First two calls raise 409, third succeeds
        operation = AsyncMock(
            side_effect=[
                MockApiException(409),
                MockApiException(409),
                "success",
            ]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await retry_on_conflict(operation)

        assert result == "success"
        assert operation.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries_exhausted(self):
        """Test that exception is raised after max retries are exhausted."""
        # All attempts raise 409
        operation = AsyncMock(
            side_effect=[MockApiException(409)] * MAX_RETRIES
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(MockApiException) as exc_info:
                await retry_on_conflict(operation)

        assert exc_info.value.status == 409
        assert operation.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_non_409_error_not_retried(self):
        """Test that non-409 errors are raised immediately without retry."""
        operation = AsyncMock(side_effect=MockApiException(500))

        with pytest.raises(MockApiException) as exc_info:
            await retry_on_conflict(operation)

        assert exc_info.value.status == 500
        assert operation.call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_404_error_not_retried(self):
        """Test that 404 errors are raised immediately without retry."""
        operation = AsyncMock(side_effect=MockApiException(404))

        with pytest.raises(MockApiException) as exc_info:
            await retry_on_conflict(operation)

        assert exc_info.value.status == 404
        assert operation.call_count == 1

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self):
        """Test that backoff delays increase exponentially with jitter."""
        operation = AsyncMock(
            side_effect=[
                MockApiException(409),
                MockApiException(409),
                "success",
            ]
        )
        sleep_calls = []

        async def mock_sleep(duration):
            sleep_calls.append(duration)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await retry_on_conflict(operation)

        # Verify exponential backoff with jitter:
        # Base: 0.05 * (2 ** attempt), Jitter: [0, 0.1]
        # Attempt 0: 0.05 + [0, 0.1] = [0.05, 0.15]
        # Attempt 1: 0.10 + [0, 0.1] = [0.10, 0.20]
        assert len(sleep_calls) == 2
        assert 0.05 <= sleep_calls[0] <= 0.15  # 0.05 base + up to 0.1 jitter
        assert 0.10 <= sleep_calls[1] <= 0.20  # 0.10 base + up to 0.1 jitter
        # Second delay should be larger than first (exponential growth)
        # Note: jitter can cause overlap, so we just verify ranges


class TestCheckpointerRaceCondition:
    """Test that checkpointer handles race conditions correctly."""

    @pytest.mark.asyncio
    async def test_concurrent_create_handled(self):
        """Test that concurrent create (read 404 then create 409) is handled."""
        from src.checkpoint.k8s_configmap_saver import K8sConfigMapSaver

        saver = K8sConfigMapSaver(namespace="test-ns")

        # Mock the K8s client
        mock_client = MagicMock()

        # Simulate race condition:
        # 1. First read returns 404
        # 2. First create returns 409 (another process created it)
        # 3. Second read succeeds (exists now)
        # 4. Second replace succeeds
        read_call_count = [0]
        create_call_count = [0]

        async def mock_read(*args, **kwargs):
            read_call_count[0] += 1
            if read_call_count[0] == 1:
                raise MockApiException(404)
            return MagicMock()

        async def mock_create(*args, **kwargs):
            create_call_count[0] += 1
            raise MockApiException(409)

        async def mock_replace(*args, **kwargs):
            return MagicMock()

        mock_client.read_namespaced_config_map = mock_read
        mock_client.create_namespaced_config_map = mock_create
        mock_client.replace_namespaced_config_map = mock_replace

        # Patch _get_k8s_client to return our mock
        async def get_mock_client():
            return mock_client

        with patch.object(saver, "_get_k8s_client", get_mock_client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                config = {
                    "configurable": {
                        "thread_id": "test-thread",
                        "checkpoint_ns": "",
                    }
                }
                checkpoint = {"id": "test-checkpoint-id"}
                metadata = {}

                result = await saver.aput(config, checkpoint, metadata, {})

        assert result["configurable"]["checkpoint_id"] == "test-checkpoint-id"
        # Should have read twice (once 404, once success after 409 retry)
        assert read_call_count[0] == 2
        # Should have tried to create once (got 409)
        assert create_call_count[0] == 1
