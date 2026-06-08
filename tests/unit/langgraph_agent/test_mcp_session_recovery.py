"""
Unit tests for MCP session termination auto-recovery.

Tests the BaseMCPClient's ability to:
1. Detect session termination errors
2. Auto-reconnect and retry tool calls with exponential backoff
3. Handle max retry exhaustion
4. Proactively check session health
"""

import asyncio
import pytest
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from tests.conftest import MockContentBlock, MockToolResult


@pytest.fixture
def mock_mcp_streams():
    """Provide mock MCP client streams."""
    mock_read_stream = AsyncMock()
    mock_write_stream = AsyncMock()
    mock_get_session_id = Mock(return_value="test-session-123")
    return mock_read_stream, mock_write_stream, mock_get_session_id


class TestSessionTerminationDetection:
    """Test detection of session termination errors."""

    def test_detects_session_terminated_message(self):
        """Test that 'Session terminated' message is detected."""
        from src.tools.base_mcp_client import BaseMCPClient

        client = BaseMCPClient(
            endpoint="http://localhost:8080/mcp",
            client_name="TestClient"
        )

        exc = Exception("Session terminated")
        assert client._is_session_terminated_error(exc) is True

    def test_detects_session_expired_message(self):
        """Test that 'Session expired' message is detected."""
        from src.tools.base_mcp_client import BaseMCPClient

        client = BaseMCPClient(
            endpoint="http://localhost:8080/mcp",
            client_name="TestClient"
        )

        exc = Exception("Session expired after 15 minutes")
        assert client._is_session_terminated_error(exc) is True

    def test_detects_404_in_error_message(self):
        """Test that HTTP 404 errors are detected as session termination."""
        from src.tools.base_mcp_client import BaseMCPClient

        client = BaseMCPClient(
            endpoint="http://localhost:8080/mcp",
            client_name="TestClient"
        )

        exc = Exception("HTTP 404 Not Found")
        assert client._is_session_terminated_error(exc) is True

    def test_detects_connection_reset_errors(self):
        """Test that connection reset errors are detected for recovery."""
        from src.tools.base_mcp_client import BaseMCPClient

        client = BaseMCPClient(
            endpoint="http://localhost:8080/mcp",
            client_name="TestClient"
        )

        connection_errors = [
            Exception("Connection reset by peer"),
            Exception("Connection closed unexpectedly"),
            Exception("Server disconnected"),
            Exception("Broken pipe"),
            Exception("Stream ended"),
            Exception("RemoteDisconnected: Remote end closed connection"),
        ]

        for exc in connection_errors:
            assert client._is_session_terminated_error(exc) is True, \
                f"Should detect '{exc}' as requiring session recovery"

    def test_does_not_detect_other_errors(self):
        """Test that unrecoverable errors are not detected as session termination."""
        from src.tools.base_mcp_client import BaseMCPClient

        client = BaseMCPClient(
            endpoint="http://localhost:8080/mcp",
            client_name="TestClient"
        )

        other_errors = [
            Exception("Tool not found"),
            Exception("Invalid arguments"),
            Exception("Internal server error"),
            Exception("Permission denied"),
        ]

        for exc in other_errors:
            assert client._is_session_terminated_error(exc) is False, \
                f"Should not detect '{exc}' as session termination"


@pytest.mark.asyncio
class TestSessionAutoRecovery:
    """Test auto-recovery when session is terminated."""

    async def test_reconnects_on_session_terminated(self, mock_mcp_streams):
        """Test that client reconnects and retries on session termination with backoff."""
        from src.tools.jira_tools import JiraTools
        from src.tools.base_mcp_client import SESSION_RECONNECT_BASE_DELAY

        with patch('src.tools.base_mcp_client.streamablehttp_client') as mock_client, \
             patch('src.tools.base_mcp_client.ClientSession') as mock_session_class, \
             patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:

            # Create a session that fails first, then succeeds after reconnect
            session_count = 0

            def create_session(*args, **kwargs):
                nonlocal session_count
                session_count += 1
                mock_session = AsyncMock()
                mock_session.initialize = AsyncMock()

                if session_count == 1:
                    # First session - fails with "Session terminated"
                    mock_session.call_tool = AsyncMock(
                        side_effect=Exception("Session terminated")
                    )
                else:
                    # Second session - succeeds
                    mock_session.call_tool = AsyncMock(
                        return_value=MockToolResult("Success after reconnect")
                    )

                return mock_session

            mock_session_class.side_effect = create_session

            # Setup async context manager
            async def mock_context(*args, **kwargs):
                return mock_mcp_streams

            mock_client.return_value.__aenter__ = mock_context
            mock_client.return_value.__aexit__ = AsyncMock()

            jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")

            # This call should fail first, reconnect, then succeed
            result = await jira_tools.get_ticket("TEST-123")

            # Verify reconnection occurred (2 sessions created)
            assert session_count == 2, \
                f"Should create 2 sessions (initial + reconnect), got {session_count}"

            # Verify result is from second session
            assert "Success after reconnect" in str(result)

            # Verify exponential backoff was applied
            # First retry should sleep for SESSION_RECONNECT_BASE_DELAY * (2^0) = 0.5s
            mock_sleep.assert_called()

            await jira_tools.close()

    async def test_max_retries_exhausted(self, mock_mcp_streams):
        """Test that client raises error after max retries exhausted."""
        from src.tools.jira_tools import JiraTools
        from src.exceptions import MCPSessionTerminatedError
        from src.tools.base_mcp_client import MAX_SESSION_RECONNECT_ATTEMPTS

        with patch('src.tools.base_mcp_client.streamablehttp_client') as mock_client, \
             patch('src.tools.base_mcp_client.ClientSession') as mock_session_class, \
             patch('asyncio.sleep', new_callable=AsyncMock):

            session_count = 0

            # Create sessions that always fail with session terminated
            def create_failing_session(*args, **kwargs):
                nonlocal session_count
                session_count += 1
                mock_session = AsyncMock()
                mock_session.initialize = AsyncMock()
                mock_session.call_tool = AsyncMock(
                    side_effect=Exception("Session terminated")
                )
                return mock_session

            mock_session_class.side_effect = create_failing_session

            async def mock_context(*args, **kwargs):
                return mock_mcp_streams

            mock_client.return_value.__aenter__ = mock_context
            mock_client.return_value.__aexit__ = AsyncMock()

            jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")

            # Should raise MCPSessionTerminatedError after max retries
            with pytest.raises(MCPSessionTerminatedError):
                await jira_tools.get_ticket("TEST-123")

            # Verify all retries were attempted (initial + MAX_SESSION_RECONNECT_ATTEMPTS)
            assert session_count == MAX_SESSION_RECONNECT_ATTEMPTS + 1, \
                f"Should create {MAX_SESSION_RECONNECT_ATTEMPTS + 1} sessions, got {session_count}"

            await jira_tools.close()

    async def test_non_session_errors_not_retried(self, mock_mcp_streams):
        """Test that non-session errors are raised immediately."""
        from src.tools.jira_tools import JiraTools

        with patch('src.tools.base_mcp_client.streamablehttp_client') as mock_client, \
             patch('src.tools.base_mcp_client.ClientSession') as mock_session_class:

            session_count = 0

            def create_session(*args, **kwargs):
                nonlocal session_count
                session_count += 1
                mock_session = AsyncMock()
                mock_session.initialize = AsyncMock()
                # This error should NOT trigger reconnection (not a session error)
                mock_session.call_tool = AsyncMock(
                    side_effect=ValueError("Tool execution failed: invalid arguments")
                )
                return mock_session

            mock_session_class.side_effect = create_session

            async def mock_context(*args, **kwargs):
                return mock_mcp_streams

            mock_client.return_value.__aenter__ = mock_context
            mock_client.return_value.__aexit__ = AsyncMock()

            jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")

            # Use a valid ticket ID format to bypass JiraTools validation
            # The error should come from the MCP tool call, not reconnect
            with pytest.raises(ValueError) as exc_info:
                await jira_tools.get_ticket("TEST-123")

            assert "Tool execution failed" in str(exc_info.value)

            # Should only create 1 session (no reconnection attempts)
            assert session_count == 1, \
                f"Should create only 1 session for non-session error, got {session_count}"

            await jira_tools.close()


@pytest.mark.asyncio
class TestSessionReset:
    """Test session reset functionality."""

    async def test_reset_clears_session_state(self, mock_mcp_streams):
        """Test that _reset_session clears all session state."""
        from src.tools.base_mcp_client import BaseMCPClient

        client = BaseMCPClient(
            endpoint="http://localhost:8080/mcp",
            client_name="TestClient"
        )

        # Manually set some state
        client.session = AsyncMock()
        client._session_id = "old-session-123"
        client._tools_cache = {"tool1": "cached"}

        # Reset the session
        await client._reset_session()

        # Verify state is cleared
        assert client.session is None, "Session should be cleared"
        assert client._session_id is None, "Session ID should be cleared"
        assert client._tools_cache is None, "Tools cache should be cleared"

    async def test_reset_handles_cleanup_errors(self, mock_mcp_streams, caplog):
        """Test that _reset_session handles cleanup errors gracefully."""
        import logging
        from src.tools.base_mcp_client import BaseMCPClient

        caplog.set_level(logging.DEBUG)

        client = BaseMCPClient(
            endpoint="http://localhost:8080/mcp",
            client_name="TestClient"
        )

        # Mock exit_stack to raise error on close
        mock_exit_stack = AsyncMock()
        mock_exit_stack.aclose = AsyncMock(
            side_effect=RuntimeError("Cleanup error during reset")
        )
        client.exit_stack = mock_exit_stack

        # Reset should succeed despite cleanup error
        await client._reset_session()

        # State should still be cleared
        assert client.session is None
        assert client._session_id is None


@pytest.mark.asyncio
class TestK8sToolsSessionRecovery:
    """Test K8s tools session recovery specifically."""

    async def test_k8s_tools_reconnects_on_session_terminated(self, mock_mcp_streams):
        """Test K8sTools reconnects when session is terminated."""
        from src.tools.k8s_tools import K8sTools

        with patch('src.tools.base_mcp_client.streamablehttp_client') as mock_client, \
             patch('src.tools.base_mcp_client.ClientSession') as mock_session_class, \
             patch('asyncio.sleep', new_callable=AsyncMock):

            session_count = 0

            def create_session(*args, **kwargs):
                nonlocal session_count
                session_count += 1
                mock_session = AsyncMock()
                mock_session.initialize = AsyncMock()

                if session_count == 1:
                    mock_session.call_tool = AsyncMock(
                        side_effect=Exception("Session terminated")
                    )
                else:
                    mock_session.call_tool = AsyncMock(
                        return_value=MockToolResult("NAME   READY   STATUS\npod-1   1/1   Running")
                    )

                return mock_session

            mock_session_class.side_effect = create_session

            async def mock_context(*args, **kwargs):
                return mock_mcp_streams

            mock_client.return_value.__aenter__ = mock_context
            mock_client.return_value.__aexit__ = AsyncMock()

            k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")

            result = await k8s_tools.kubectl_get("pods", namespace="default")

            # Should have reconnected
            assert session_count == 2

            await k8s_tools.close()


@pytest.mark.asyncio
class TestSessionHealthCheck:
    """Test proactive session health checks."""

    async def test_ensure_healthy_session_establishes_connection(self, mock_mcp_streams):
        """Test that ensure_healthy_session establishes connection when none exists."""
        from src.tools.base_mcp_client import BaseMCPClient

        with patch('src.tools.base_mcp_client.streamablehttp_client') as mock_client, \
             patch('src.tools.base_mcp_client.ClientSession') as mock_session_class:

            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock()
            mock_session_class.return_value = mock_session

            async def mock_context(*args, **kwargs):
                return mock_mcp_streams

            mock_client.return_value.__aenter__ = mock_context
            mock_client.return_value.__aexit__ = AsyncMock()

            client = BaseMCPClient(
                endpoint="http://localhost:8080/mcp",
                client_name="TestClient"
            )

            # Should not be connected initially
            assert client.session is None

            # Health check should establish connection
            result = await client.ensure_healthy_session()

            assert result is True
            assert client.session is not None

    async def test_ensure_healthy_session_resets_stale_session(self, mock_mcp_streams):
        """Test that ensure_healthy_session resets session with no session_id."""
        from src.tools.base_mcp_client import BaseMCPClient

        with patch('src.tools.base_mcp_client.streamablehttp_client') as mock_client, \
             patch('src.tools.base_mcp_client.ClientSession') as mock_session_class:

            session_count = 0

            def create_session(*args, **kwargs):
                nonlocal session_count
                session_count += 1
                mock_session = AsyncMock()
                mock_session.initialize = AsyncMock()
                return mock_session

            mock_session_class.side_effect = create_session

            async def mock_context(*args, **kwargs):
                return mock_mcp_streams

            mock_client.return_value.__aenter__ = mock_context
            mock_client.return_value.__aexit__ = AsyncMock()

            client = BaseMCPClient(
                endpoint="http://localhost:8080/mcp",
                client_name="TestClient"
            )

            # Simulate stale session state (session exists but no session_id)
            client.session = AsyncMock()
            client._session_id = None  # Stale - no session ID

            # Health check should detect and reset
            result = await client.ensure_healthy_session()

            assert result is True
            # Should have created a new session
            assert session_count == 1

    async def test_ensure_healthy_session_returns_false_on_failure(self, mock_mcp_streams):
        """Test that ensure_healthy_session returns False when connection fails."""
        from src.tools.base_mcp_client import BaseMCPClient
        from src.exceptions import MCPConnectionError

        with patch('src.tools.base_mcp_client.streamablehttp_client') as mock_client:

            # Make connection fail
            mock_client.side_effect = ConnectionError("Connection refused")

            client = BaseMCPClient(
                endpoint="http://localhost:8080/mcp",
                client_name="TestClient"
            )

            # Health check should return False, not raise
            result = await client.ensure_healthy_session()

            assert result is False
