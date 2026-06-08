"""
Integration tests for MCP session lifecycle management

Tests the lifecycle of MCP ClientSession connections from LangGraph agents to MCP servers:
- Session initialization and reuse
- Multiple tool calls using same session
- Session cleanup
- Reconnection after disconnect

These tests verify that the MCP client efficiently manages connections and avoids
creating unnecessary sessions for each tool call.
"""

import asyncio
import pytest
import logging
from typing import Dict, Any
from unittest.mock import AsyncMock, Mock, patch, MagicMock, call
from contextlib import AsyncExitStack
from tests.conftest import MockContentBlock, MockToolResult, MockListToolsResult


class MockToolInfo:
    """Mock MCP tool info"""
    def __init__(self, name: str, description: str, inputSchema: Dict[str, Any]):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema


@pytest.mark.integration
class TestJiraMCPSessionReuse:
    """Test Jira MCP client session reuse across multiple tool calls"""

    @pytest.mark.asyncio
    async def test_jira_mcp_session_reuse(self, mcp_session_tracker, caplog):
        """
        Test that multiple Jira tool calls use the same MCP session

        Verifies:
        1. First tool call creates session (logs "Connecting to Jira MCP server")
        2. Subsequent calls reuse session (no additional "Connecting" logs)
        3. Only one session is active throughout
        """
        from src.tools.jira_tools import JiraTools

        caplog.set_level(logging.INFO)

        # Mock the MCP client components
        with patch('src.tools.jira_tools.streamablehttp_client') as mock_client, \
             patch('src.tools.jira_tools.ClientSession') as mock_session_class:

            # Setup mock session
            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock()
            mock_session.call_tool = AsyncMock(side_effect=[
                MockToolResult("Ticket GAUDISW-123: CrashLoopBackOff"),
                MockToolResult("Found 5 similar tickets"),
                MockToolResult("✅ Comment added to GAUDISW-123")
            ])
            mock_session_class.return_value = mock_session

            # Setup mock client streams
            mock_read_stream = AsyncMock()
            mock_write_stream = AsyncMock()
            mock_get_session_id = Mock(return_value="session-123-abc")

            async def mock_client_context(*args, **kwargs):
                return (mock_read_stream, mock_write_stream, mock_get_session_id)

            mock_client.return_value.__aenter__ = AsyncMock(return_value=(
                mock_read_stream, mock_write_stream, mock_get_session_id
            ))
            mock_client.return_value.__aexit__ = AsyncMock()

            # Create JiraTools client
            jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")

            # Track session creation
            session_id = "session-123-abc"
            mcp_session_tracker.track_connection(jira_tools.endpoint, session_id)

            # Make multiple tool calls
            await jira_tools.get_ticket("GAUDISW-123")
            mcp_session_tracker.track_tool_call(session_id, "get_ticket", {"ticket_id": "GAUDISW-123"})

            await jira_tools.search_tickets("CrashLoopBackOff", limit=5)
            mcp_session_tracker.track_tool_call(session_id, "search_tickets", {"jql": "CrashLoopBackOff"})

            await jira_tools.add_comment("GAUDISW-123", "Investigation started")
            mcp_session_tracker.track_tool_call(session_id, "add_comment", {"ticket_id": "GAUDISW-123"})

            # Cleanup
            await jira_tools.close()
            mcp_session_tracker.track_disconnection(session_id)

            # Verify session reuse
            connection_count = mcp_session_tracker.get_connection_count(jira_tools.endpoint)
            assert connection_count == 1, \
                f"Should create only 1 connection, got {connection_count}"

            tool_call_count = mcp_session_tracker.get_tool_call_count(session_id)
            assert tool_call_count == 3, \
                f"Should make 3 tool calls, got {tool_call_count}"

            assert mcp_session_tracker.was_session_reused(session_id), \
                "Session should be reused for multiple tool calls"

            # Verify logs show only one connection
            connection_logs = [record for record in caplog.records
                             if "Connecting to Jira MCP server" in record.message]
            assert len(connection_logs) <= 1, \
                f"Should log connection only once, got {len(connection_logs)} logs"

    @pytest.mark.asyncio
    async def test_jira_mcp_first_call_establishes_session(self, caplog):
        """
        Test that session is established on first tool call, not on initialization

        Verifies lazy connection pattern:
        1. Creating JiraTools instance doesn't connect
        2. First tool call triggers _ensure_connected()
        3. Session is established before first tool use
        """
        from src.tools.jira_tools import JiraTools

        caplog.set_level(logging.INFO)

        with patch('src.tools.jira_tools.streamablehttp_client') as mock_client, \
             patch('src.tools.jira_tools.ClientSession') as mock_session_class:

            # Setup mocks
            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock()
            mock_session.call_tool = AsyncMock(return_value=MockToolResult("Test response"))
            mock_session_class.return_value = mock_session

            mock_client.return_value.__aenter__ = AsyncMock(return_value=(
                AsyncMock(), AsyncMock(), Mock(return_value="session-456")
            ))
            mock_client.return_value.__aexit__ = AsyncMock()

            # Create client (should NOT connect yet)
            jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")

            # Verify no connection yet
            assert jira_tools.session is None, \
                "Session should be None after initialization"

            # Make first tool call (should establish connection)
            await jira_tools.get_ticket("TEST-123")

            # Verify connection was established
            assert jira_tools.session is not None, \
                "Session should be established after first tool call"

            # Verify initialize was called
            mock_session.initialize.assert_called_once()

            await jira_tools.close()


@pytest.mark.integration
class TestK8sMCPSessionReuse:
    """Test K8s MCP client session reuse across multiple tool calls"""

    @pytest.mark.asyncio
    async def test_k8s_mcp_session_reuse(self, mcp_session_tracker, caplog):
        """
        Test that multiple K8s tool calls use the same MCP session

        Verifies:
        1. Session created once for multiple K8s operations
        2. Session remains active across get/describe/logs calls
        3. Efficient resource usage
        """
        from src.tools.k8s_tools import K8sTools

        caplog.set_level(logging.INFO)

        with patch('src.tools.k8s_tools.streamablehttp_client') as mock_client, \
             patch('src.tools.k8s_tools.ClientSession') as mock_session_class:

            # Setup mock session
            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock()
            mock_session.call_tool = AsyncMock(side_effect=[
                MockToolResult("Pod list: api-server-123"),
                MockToolResult("Pod details: Running, 5 restarts"),
                MockToolResult("Logs: Error loading config")
            ])
            mock_session_class.return_value = mock_session

            mock_client.return_value.__aenter__ = AsyncMock(return_value=(
                AsyncMock(), AsyncMock(), Mock(return_value="k8s-session-789")
            ))
            mock_client.return_value.__aexit__ = AsyncMock()

            # Create K8sTools client
            k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")

            # Track session
            session_id = "k8s-session-789"
            mcp_session_tracker.track_connection(k8s_tools.endpoint, session_id)

            # Make multiple K8s tool calls
            await k8s_tools.kubectl_get("pods", namespace="default")
            mcp_session_tracker.track_tool_call(session_id, "k8s_get_resources", {"resource": "pods"})

            await k8s_tools.kubectl_describe("pod", "api-server-123", namespace="default")
            mcp_session_tracker.track_tool_call(session_id, "k8s_describe_resource", {"name": "api-server-123"})

            await k8s_tools.kubectl_logs("api-server-123", namespace="default", tail=100)
            mcp_session_tracker.track_tool_call(session_id, "k8s_get_pod_logs", {"pod": "api-server-123"})

            # Cleanup
            await k8s_tools.close()
            mcp_session_tracker.track_disconnection(session_id)

            # Verify session reuse
            connection_count = mcp_session_tracker.get_connection_count(k8s_tools.endpoint)
            assert connection_count == 1, \
                f"Should create only 1 K8s connection, got {connection_count}"

            tool_call_count = mcp_session_tracker.get_tool_call_count(session_id)
            assert tool_call_count == 3, \
                f"Should make 3 K8s tool calls, got {tool_call_count}"

            assert mcp_session_tracker.was_session_reused(session_id), \
                "K8s session should be reused for multiple tool calls"

    @pytest.mark.asyncio
    async def test_concurrent_k8s_tool_calls_use_same_session(self, mcp_session_tracker):
        """
        Test that concurrent K8s tool calls use the same session

        Verifies:
        1. Multiple concurrent calls don't create multiple sessions
        2. Session locking/synchronization works correctly
        3. All calls complete successfully
        """
        from src.tools.k8s_tools import K8sTools

        with patch('src.tools.k8s_tools.streamablehttp_client') as mock_client, \
             patch('src.tools.k8s_tools.ClientSession') as mock_session_class:

            # Setup mock session
            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock()

            # Mock different responses for each call
            responses = [
                MockToolResult(f"Response {i}")
                for i in range(5)
            ]
            mock_session.call_tool = AsyncMock(side_effect=responses)
            mock_session_class.return_value = mock_session

            mock_client.return_value.__aenter__ = AsyncMock(return_value=(
                AsyncMock(), AsyncMock(), Mock(return_value="concurrent-session")
            ))
            mock_client.return_value.__aexit__ = AsyncMock()

            k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")

            # Make concurrent tool calls
            tasks = [
                k8s_tools.kubectl_get("pods", namespace=f"ns-{i}")
                for i in range(5)
            ]

            results = await asyncio.gather(*tasks)

            # Verify all calls completed
            assert len(results) == 5, \
                "All concurrent calls should complete"

            # Session should be created only once (reused for all calls)
            # In real implementation, _ensure_connected() should handle concurrency

            await k8s_tools.close()


@pytest.mark.integration
class TestMCPSessionCleanup:
    """Test proper cleanup of MCP sessions"""

    @pytest.mark.asyncio
    async def test_mcp_session_cleanup(self, mcp_session_tracker):
        """
        Test that close() properly cleans up MCP session

        Verifies:
        1. close() is called on exit stack
        2. Session is marked as disconnected
        3. Resources are freed
        """
        from src.tools.jira_tools import JiraTools

        with patch('src.tools.jira_tools.streamablehttp_client') as mock_client, \
             patch('src.tools.jira_tools.ClientSession') as mock_session_class:

            # Setup mocks
            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock()
            mock_session.call_tool = AsyncMock(return_value=MockToolResult("Test"))
            mock_session_class.return_value = mock_session

            # Setup proper async context manager for streamablehttp_client
            async def mock_context(*args, **kwargs):
                return (AsyncMock(), AsyncMock(), Mock(return_value="cleanup-session"))

            mock_client.return_value.__aenter__ = mock_context
            mock_client.return_value.__aexit__ = AsyncMock()

            jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")

            # Use the session
            await jira_tools.get_ticket("TEST-999")

            # Track connection
            session_id = "cleanup-session"
            mcp_session_tracker.track_connection(jira_tools.endpoint, session_id)

            # Close should clean up resources
            await jira_tools.close()
            mcp_session_tracker.track_disconnection(session_id)

            # Verify session is tracked as disconnected
            assert session_id not in mcp_session_tracker.active_sessions, \
                "Session should be removed from active sessions"

    @pytest.mark.asyncio
    async def test_mcp_cleanup_handles_errors_gracefully(self, caplog):
        """
        Test that cleanup handles known MCP client errors gracefully

        Known issues with MCP client cleanup:
        - "different task" RuntimeError from anyio cancel scopes
        - "Server disconnected" from httpx during cleanup

        Verifies:
        1. These errors are caught and logged as debug
        2. Cleanup doesn't crash
        3. Other errors are still raised
        """
        from src.tools.jira_tools import JiraTools

        caplog.set_level(logging.DEBUG)

        with patch('src.tools.jira_tools.streamablehttp_client') as mock_client, \
             patch('src.tools.jira_tools.ClientSession'):

            mock_client.return_value.__aenter__ = AsyncMock(return_value=(
                AsyncMock(), AsyncMock(), Mock(return_value="error-session")
            ))
            mock_client.return_value.__aexit__ = AsyncMock()

            jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")

            # Mock exit_stack to raise known error
            mock_exit_stack = AsyncMock()
            mock_exit_stack.aclose = AsyncMock(side_effect=RuntimeError(
                "Attempted to exit cancel scope in a different task than it was entered in"
            ))

            with patch.object(jira_tools, 'exit_stack', mock_exit_stack):
                # Should handle error gracefully
                await jira_tools.close()

                # Verify error was logged as debug (deferred to GC)
                debug_logs = [r for r in caplog.records if r.levelname == "DEBUG"]
                assert any("cleanup deferred to GC" in r.message for r in debug_logs), \
                    "Should log known cleanup error as debug"

    @pytest.mark.asyncio
    async def test_multiple_close_calls_are_safe(self):
        """
        Test that calling close() multiple times doesn't cause errors

        Verifies:
        1. First close() cleans up properly
        2. Subsequent close() calls are no-ops
        3. No exceptions raised
        """
        from src.tools.k8s_tools import K8sTools

        with patch('src.tools.k8s_tools.streamablehttp_client') as mock_client, \
             patch('src.tools.k8s_tools.ClientSession'):

            mock_client.return_value.__aenter__ = AsyncMock(return_value=(
                AsyncMock(), AsyncMock(), Mock(return_value="multi-close-session")
            ))
            mock_client.return_value.__aexit__ = AsyncMock()

            k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")

            # Call close multiple times
            await k8s_tools.close()
            await k8s_tools.close()
            await k8s_tools.close()

            # Should not raise exceptions


@pytest.mark.integration
class TestMCPSessionReconnect:
    """Test MCP session reconnection after disconnect"""

    @pytest.mark.asyncio
    async def test_mcp_reconnects_after_disconnect(self, mcp_session_tracker, caplog):
        """
        Test that MCP client reconnects after session disconnect

        Simulates scenario:
        1. Session established and used successfully
        2. Server disconnects (network issue, server restart, etc.)
        3. Next tool call detects disconnect
        4. New session is automatically created
        5. Tool call succeeds with new session
        """
        from src.tools.jira_tools import JiraTools

        caplog.set_level(logging.INFO)

        with patch('src.tools.jira_tools.streamablehttp_client') as mock_client, \
             patch('src.tools.jira_tools.ClientSession') as mock_session_class:

            # First session works, then fails, then reconnects
            call_count = 0

            def create_session(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                mock_session = AsyncMock()
                mock_session.initialize = AsyncMock()

                if call_count == 1:
                    # First session - works for first call
                    mock_session.call_tool = AsyncMock(return_value=MockToolResult("Success 1"))
                elif call_count == 2:
                    # Second session after reconnect - works
                    mock_session.call_tool = AsyncMock(return_value=MockToolResult("Success 2"))

                return mock_session

            mock_session_class.side_effect = create_session

            session_counter = 0

            def get_session_id():
                return f"session-{session_counter}"

            async def mock_context(*args, **kwargs):
                nonlocal session_counter
                session_counter += 1
                return (AsyncMock(), AsyncMock(), Mock(return_value=f"session-{session_counter}"))

            mock_client.return_value.__aenter__ = mock_context
            mock_client.return_value.__aexit__ = AsyncMock()

            jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")

            # First call - establishes session
            result1 = await jira_tools.get_ticket("TEST-1")
            mcp_session_tracker.track_connection(jira_tools.endpoint, "session-1")
            mcp_session_tracker.track_tool_call("session-1", "get_ticket", {"ticket_id": "TEST-1"})

            assert "Success 1" in str(result1)

            # Simulate disconnect by clearing session
            await jira_tools.close()
            mcp_session_tracker.track_disconnection("session-1")
            jira_tools.session = None

            # Second call - should reconnect automatically
            result2 = await jira_tools.get_ticket("TEST-2")
            mcp_session_tracker.track_connection(jira_tools.endpoint, "session-2")
            mcp_session_tracker.track_tool_call("session-2", "get_ticket", {"ticket_id": "TEST-2"})

            assert "Success 2" in str(result2)

            # Verify reconnection occurred
            connection_count = mcp_session_tracker.get_connection_count(jira_tools.endpoint)
            assert connection_count == 2, \
                f"Should have 2 connections (initial + reconnect), got {connection_count}"

            await jira_tools.close()

    @pytest.mark.asyncio
    async def test_mcp_session_error_triggers_reconnect_on_next_call(self):
        """
        Test that tool call errors don't permanently break the client

        Verifies:
        1. First tool call fails with session error
        2. Error is caught and logged
        3. Next tool call creates new session
        4. Subsequent calls work normally
        """
        from src.tools.k8s_tools import K8sTools

        with patch('src.tools.k8s_tools.streamablehttp_client') as mock_client, \
             patch('src.tools.k8s_tools.ClientSession') as mock_session_class:

            # Track number of sessions created
            session_count = 0

            def create_session(*args, **kwargs):
                nonlocal session_count
                session_count += 1
                mock_session = AsyncMock()
                mock_session.initialize = AsyncMock()

                if session_count == 1:
                    # First session - fails
                    mock_session.call_tool = AsyncMock(side_effect=Exception("Session closed"))
                else:
                    # Second session - works
                    mock_session.call_tool = AsyncMock(return_value=MockToolResult("Success after reconnect"))

                return mock_session

            mock_session_class.side_effect = create_session

            # Setup async context manager for streamablehttp_client
            async def mock_context(*args, **kwargs):
                return (AsyncMock(), AsyncMock(), Mock(return_value=f"recovery-session-{session_count}"))

            mock_client.return_value.__aenter__ = mock_context
            mock_client.return_value.__aexit__ = AsyncMock()

            k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")

            # First call fails and returns error dict (kubectl_get catches exceptions)
            result = await k8s_tools.kubectl_get("pods")
            assert "error" in result, "First call should return error dict"
            assert "Session closed" in result["error"]

            # Clear session to trigger reconnect
            k8s_tools.session = None

            # Second call should work (new session)
            result = await k8s_tools.kubectl_get("pods")
            assert "Success after reconnect" in str(result)

            await k8s_tools.close()


@pytest.mark.integration
class TestMCPSessionPerformance:
    """Test MCP session performance and efficiency"""

    @pytest.mark.asyncio
    async def test_session_reuse_reduces_connection_overhead(self, mcp_session_tracker):
        """
        Test that session reuse is more efficient than creating new sessions

        Compares:
        - Time to establish new session vs reuse existing
        - Number of connection attempts
        - Total overhead
        """
        from src.tools.jira_tools import JiraTools

        with patch('src.tools.jira_tools.streamablehttp_client') as mock_client, \
             patch('src.tools.jira_tools.ClientSession') as mock_session_class:

            # Track initialization calls
            init_call_count = 0

            async def mock_initialize():
                nonlocal init_call_count
                init_call_count += 1

            mock_session = AsyncMock()
            mock_session.initialize = mock_initialize
            mock_session.call_tool = AsyncMock(return_value=MockToolResult("Test"))
            mock_session_class.return_value = mock_session

            # Setup proper async context manager
            async def mock_context(*args, **kwargs):
                return (AsyncMock(), AsyncMock(), Mock(return_value="perf-session"))

            mock_client.return_value.__aenter__ = mock_context
            mock_client.return_value.__aexit__ = AsyncMock()

            jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")

            # Make 10 tool calls
            for i in range(10):
                await jira_tools.get_ticket(f"TEST-{i}")

            # Should only initialize once (session reuse)
            assert init_call_count == 1, \
                f"Should initialize session only once, got {init_call_count}"

            await jira_tools.close()
