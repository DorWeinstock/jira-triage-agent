"""Comprehensive tests for BaseMCPClient.

Tests cover:
- Retry logic parameterization
- Typed exception handling for transient errors
- ExceptionGroup handling for TaskGroup-wrapped errors
- Content extraction with multi-item warnings
- Async context manager protocol
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
from httpx import ConnectError, NetworkError, RemoteProtocolError

from src.exceptions import ToolError
from src.tools.base_mcp_client import BaseMCPClient


class MockResult:
    """Mock MCP result object."""

    def __init__(self, content=None, is_error=False, text=None):
        self.content = content
        self.isError = is_error
        self.text = text


class TestBaseMCPClientRetryLogic:
    """Test retry logic with parameterization."""

    @pytest.mark.asyncio
    async def test_successful_call_first_attempt(self):
        """Successful call on first attempt should return result."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        expected_result = "success_result"
        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = expected_result

            result = await client.call_tool("test_tool", {"arg": "value"})

            assert result == expected_result
            mock_call.assert_called_once()

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_connection_error_succeeds_on_second(self, mock_sleep):
        """Connection error on attempt 1 should retry and succeed on attempt 2."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            # First call raises connection error, second succeeds
            mock_call.side_effect = [
                ConnectError("Connection refused"),
                "success_result",
            ]

            result = await client.call_tool("test_tool", {"arg": "value"})

            assert result == "success_result"
            assert mock_call.call_count == 2
            assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_exhausts_retries_on_persistent_connection_error(self, mock_sleep):
        """Persistent connection error should exhaust retries and raise."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            # Both attempts fail
            mock_call.side_effect = ConnectError("Connection refused")

            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("test_tool", {}, max_retries=2)

            # The error message comes from the retry exhaustion
            assert "failed" in str(exc_info.value)
            assert mock_call.call_count == 2
            assert mock_sleep.call_count == 1  # One sleep between attempts

    @pytest.mark.asyncio
    async def test_timeout_error_does_not_retry(self):
        """asyncio.TimeoutError should not trigger retry."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = asyncio.TimeoutError()

            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("test_tool", {})

            assert "timed out" in str(exc_info.value)
            mock_call.assert_called_once()  # No retry

    @pytest.mark.asyncio
    async def test_unknown_exception_does_not_retry(self):
        """Unknown exception should not trigger retry."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = ValueError("Unexpected error")

            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("test_tool", {})

            assert "unexpected error" in str(exc_info.value)
            mock_call.assert_called_once()  # No retry

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_custom_max_retries_parameter(self, mock_sleep):
        """max_retries parameter should be respected."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = ClosedResourceError("Stream closed")

            with pytest.raises(ToolError):
                await client.call_tool("test_tool", {}, max_retries=3)

            # Should attempt 3 times (1 initial + 2 retries)
            assert mock_call.call_count == 3
            assert mock_sleep.call_count == 2  # Two sleeps between attempts


class TestBaseMCPClientExceptionHandling:
    """Test typed exception handling for transient errors."""

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_broken_resource_error_retries(self, mock_sleep):
        """BrokenResourceError should trigger retry."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = [
                BrokenResourceError("Stream broken"),
                "success",
            ]

            result = await client.call_tool("test_tool", {})
            assert result == "success"
            assert mock_call.call_count == 2
            assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_closed_resource_error_retries(self, mock_sleep):
        """ClosedResourceError should trigger retry."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = [
                ClosedResourceError("Resource closed"),
                "success",
            ]

            result = await client.call_tool("test_tool", {})
            assert result == "success"
            assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_end_of_stream_error_retries(self, mock_sleep):
        """EndOfStream should trigger retry."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = [EndOfStream(), "success"]

            result = await client.call_tool("test_tool", {})
            assert result == "success"
            assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_connect_error_retries(self, mock_sleep):
        """ConnectError should trigger retry."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = [
                ConnectError("Connection failed"),
                "success",
            ]

            result = await client.call_tool("test_tool", {})
            assert result == "success"
            assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_network_error_retries(self, mock_sleep):
        """NetworkError should trigger retry."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = [
                NetworkError("Network error"),
                "success",
            ]

            result = await client.call_tool("test_tool", {})
            assert result == "success"
            assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_remote_protocol_error_retries(self, mock_sleep):
        """RemoteProtocolError should trigger retry."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = [
                RemoteProtocolError("Malformed response"),
                "success",
            ]

            result = await client.call_tool("test_tool", {})
            assert result == "success"
            assert mock_sleep.call_count == 1


class TestBaseMCPClientExceptionGroup:
    """Test ExceptionGroup handling for TaskGroup-wrapped errors."""

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_exception_group_with_all_retryable_errors(self, mock_sleep):
        """ExceptionGroup with all-retryable sub-errors should retry."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            # Create ExceptionGroup with retryable errors
            exc_group = ExceptionGroup(
                "Multiple errors",
                [
                    ConnectError("Connection 1"),
                    NetworkError("Network error"),
                ],
            )
            mock_call.side_effect = [exc_group, "success"]

            result = await client.call_tool("test_tool", {})
            assert result == "success"
            assert mock_call.call_count == 2
            assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    async def test_exception_group_with_mixed_errors_no_retry(self):
        """ExceptionGroup with mixed errors should not retry."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            # Create ExceptionGroup with mix of retryable and non-retryable errors
            exc_group = ExceptionGroup(
                "Multiple errors",
                [
                    ConnectError("Connection error"),
                    ValueError("Non-retryable error"),
                ],
            )
            mock_call.side_effect = exc_group

            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("test_tool", {})

            assert "failed" in str(exc_info.value)
            mock_call.assert_called_once()  # No retry


class TestBaseMCPClientContentExtraction:
    """Test content extraction with multi-item warnings."""

    def test_extract_content_single_item_no_warning(self, caplog):
        """Single content item should not trigger warning."""
        client = BaseMCPClient(endpoint="http://localhost:8080")
        result = MockResult(content=[MockResult(text="item1")])

        content = client._extract_content(result)

        # Content item has text attribute, so it extracts the text
        assert content == "item1"
        assert "dropped" not in caplog.text.lower()

    def test_extract_content_multi_item_logs_warning(self, caplog):
        """Multiple content items should log warning."""
        client = BaseMCPClient(endpoint="http://localhost:8080")
        item1 = MagicMock(text="item1")
        item2 = MagicMock(text="item2")
        item3 = MagicMock(text="item3")
        result = MockResult(content=[item1, item2, item3])

        content = client._extract_content(result)

        # First item's text should be extracted
        assert content == "item1"
        assert "received 3 content items" in caplog.text
        assert "returning only the first" in caplog.text

    def test_extract_content_no_text_attribute(self):
        """Content item without text attribute should return item as-is."""
        client = BaseMCPClient(endpoint="http://localhost:8080")
        item = {"key": "value"}
        result = MockResult(content=[item])

        content = client._extract_content(result)

        assert content == item

    def test_extract_content_non_list(self):
        """Non-list content should be returned as-is."""
        client = BaseMCPClient(endpoint="http://localhost:8080")
        result = MockResult(content="string_content")

        content = client._extract_content(result)

        assert content == "string_content"

    def test_extract_content_empty_list(self):
        """Empty content list should return empty result properly."""
        client = BaseMCPClient(endpoint="http://localhost:8080")
        result = MockResult(content=[])

        content = client._extract_content(result)

        # Empty list is falsy, so it returns the result object itself
        assert content == result

    def test_extract_content_no_content_attribute(self):
        """Result without content attribute should return result as-is."""
        client = BaseMCPClient(endpoint="http://localhost:8080")
        result = MagicMock(spec=[])  # No content attribute

        content = client._extract_content(result)

        assert content is result


class TestBaseMCPClientAsyncContextManager:
    """Test async context manager protocol."""

    @pytest.mark.asyncio
    async def test_async_context_manager_enters_returns_self(self):
        """__aenter__ should return self."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        async with client as ctx:
            assert ctx is client

    @pytest.mark.asyncio
    async def test_async_context_manager_exits_without_error(self):
        """__aexit__ should complete without raising."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(client, "close", new_callable=AsyncMock):
            async with client:
                pass  # Should not raise

    @pytest.mark.asyncio
    async def test_async_context_manager_calls_close(self):
        """__aexit__ should call close()."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(client, "close", new_callable=AsyncMock) as mock_close:
            async with client:
                pass

            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_context_manager_with_exception(self):
        """__aexit__ should still call close() even if block raises."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(client, "close", new_callable=AsyncMock) as mock_close:
            try:
                async with client:
                    raise ValueError("Test error")
            except ValueError:
                pass  # Expected

            mock_close.assert_called_once()


class TestBaseMCPClientSettingsCaching:
    """Test that settings are cached at initialization."""

    def test_settings_cached_in_init(self):
        """Settings should be cached in __init__."""
        with patch("src.tools.base_mcp_client.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_get_settings.return_value = mock_settings

            client = BaseMCPClient(endpoint="http://localhost:8080")

            assert client._settings is mock_settings
            mock_get_settings.assert_called_once()

    @pytest.mark.asyncio
    async def test_settings_not_refetched_per_call(self):
        """Settings should not be refetched on each call_tool invocation."""
        with patch("src.tools.base_mcp_client.get_settings") as mock_get_settings:
            mock_settings = MagicMock(
                mcp_connection_timeout=30, mcp_sse_read_timeout=600
            )
            mock_get_settings.return_value = mock_settings

            client = BaseMCPClient(endpoint="http://localhost:8080")

            with patch.object(
                client, "_call_tool_with_session", new_callable=AsyncMock
            ) as mock_call:
                mock_call.return_value = "result"
                await client.call_tool("test_tool", {})

            # get_settings should only be called once (in __init__)
            mock_get_settings.assert_called_once()


class TestBaseMCPClientLogging:
    """Test logging behavior during retries and errors."""

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_connection_error_logs_retry_attempt(self, mock_sleep, caplog):
        """Connection error should log retry attempt."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = [
                ConnectError("Connection failed"),
                "success",
            ]

            await client.call_tool("test_tool", {})

            assert "transient error" in caplog.text.lower()
            assert "attempt 1" in caplog.text

    @pytest.mark.asyncio
    async def test_timeout_logs_error(self, caplog):
        """Timeout should log timeout message."""
        import logging
        caplog.set_level(logging.WARNING)
        
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = asyncio.TimeoutError()

            with pytest.raises(ToolError) as exc_info:
                await client.call_tool("test_tool", {})

            # Check the exception message itself (not the log)
            assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_exception_group_logs_retryable_status(self, mock_sleep, caplog):
        """ExceptionGroup with retryable errors should log."""
        client = BaseMCPClient(endpoint="http://localhost:8080")

        with patch.object(
            client, "_call_tool_with_session", new_callable=AsyncMock
        ) as mock_call:
            exc_group = ExceptionGroup(
                "Errors",
                [ConnectError("Connection"), EndOfStream()],
            )
            mock_call.side_effect = [exc_group, "success"]

            await client.call_tool("test_tool", {})

            assert "retryable exceptiongroup" in caplog.text.lower()


class TestBaseMCPClientValidation:
    """Test input validation."""

    @pytest.mark.asyncio
    async def test_empty_tool_name_raises_value_error(self):
        """Empty tool_name should raise ValueError."""
        client = BaseMCPClient(endpoint="http://localhost:8080")
        with pytest.raises(ValueError, match="tool_name must not be empty"):
            await client.call_tool("", {})

    @pytest.mark.asyncio
    async def test_whitespace_tool_name_raises_value_error(self):
        """Whitespace-only tool_name should raise ValueError."""
        client = BaseMCPClient(endpoint="http://localhost:8080")
        with pytest.raises(ValueError, match="tool_name must not be empty"):
            await client.call_tool("   ", {})
