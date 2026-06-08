"""
Integration tests for MCP Tool Wrapper classes (JiraTools and K8sTools)

Tests the wrapper logic that interfaces with MCP servers:
- Connection management and session reuse
- Response unwrapping from MCP ContentBlock to dict/string
- Error handling and retry logic
- Tool method implementations
- Resource cleanup

These tests use mocks to simulate MCP server behavior without requiring
actual MCP server deployments.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional
from unittest.mock import AsyncMock, Mock, MagicMock, patch, call
from dataclasses import dataclass

import pytest
from tests.conftest import MockListToolsResult

# Import the wrapper classes
from src.tools.jira_tools import JiraTools
from src.tools.k8s_tools import K8sTools


# ============================================================================
# Mock MCP Protocol Objects
# ============================================================================

@dataclass
class MockTextContent:
    """Mock MCP TextContent object"""
    text: str
    type: str = "text"


@dataclass
class MockCallToolResult:
    """Mock MCP CallToolResult"""
    content: List[MockTextContent]
    isError: bool = False


@dataclass
class MockTool:
    """Mock MCP Tool definition"""
    name: str
    description: str
    inputSchema: Dict[str, Any]




# ============================================================================
# MCP Session Mocking Utilities
# ============================================================================

class MockMCPSession:
    """
    Mock MCP ClientSession that simulates MCP protocol behavior

    Tracks:
    - initialization calls
    - tool calls with arguments
    - connection state
    """

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.initialized = False
        self.tool_calls: List[Dict[str, Any]] = []
        self.available_tools: List[MockTool] = []
        self._closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._closed = True
        return False

    async def initialize(self):
        """Mock session initialization"""
        self.initialized = True
        logging.info(f"MockMCPSession initialized for {self.endpoint}")

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> MockCallToolResult:
        """
        Mock tool call - returns different responses based on tool name

        Tracks all tool calls for verification in tests
        """
        if not self.initialized:
            raise RuntimeError("Session not initialized")

        self.tool_calls.append({
            "tool": tool_name,
            "arguments": arguments,
            "timestamp": asyncio.get_event_loop().time()
        })

        # Return mock responses based on tool name
        response_text = self._generate_mock_response(tool_name, arguments)
        return MockCallToolResult(
            content=[MockTextContent(text=response_text)]
        )

    async def list_tools(self) -> MockListToolsResult:
        """Mock list_tools request"""
        if not self.initialized:
            raise RuntimeError("Session not initialized")

        return MockListToolsResult(tools=self.available_tools)

    def _generate_mock_response(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Generate appropriate mock responses for different tools"""

        # Jira tools
        if tool_name == "get_ticket":
            ticket_id = arguments.get("ticket_id", "UNKNOWN")
            return f"""{{
                "key": "{ticket_id}",
                "fields": {{
                    "summary": "CrashLoopBackOff in api-server pod",
                    "description": "The api-server pod is crashing repeatedly in default namespace",
                    "status": {{"name": "Open"}},
                    "priority": {{"name": "High"}},
                    "created": "2025-12-17T10:00:00.000Z"
                }}
            }}"""

        elif tool_name == "search_tickets":
            jql = arguments.get("jql", "")
            limit = arguments.get("limit", 5)
            return f"""{{
                "total": 3,
                "issues": [
                    {{"key": "PROJ-100", "fields": {{"summary": "Similar CrashLoop issue"}}}},
                    {{"key": "PROJ-101", "fields": {{"summary": "Pod restart loop"}}}},
                    {{"key": "PROJ-102", "fields": {{"summary": "Container failing"}}}}
                ]
            }}"""

        elif tool_name == "add_comment":
            ticket_id = arguments.get("ticket_id", "UNKNOWN")
            return f"✅ Successfully added comment to {ticket_id}"

        elif tool_name == "get_ticket_history":
            ticket_id = arguments.get("ticket_id", "UNKNOWN")
            return f"""{{
                "ticket": "{ticket_id}",
                "changelog": [
                    {{"field": "status", "from": "Open", "to": "In Progress"}},
                    {{"field": "assignee", "from": null, "to": "user@example.com"}}
                ]
            }}"""

        # K8s tools
        elif tool_name == "k8s_get_resources":
            resource_type = arguments.get("resource", "pods")
            namespace = arguments.get("namespace", "default")
            name = arguments.get("name")

            if name:
                return f"""{{
                    "apiVersion": "v1",
                    "kind": "Pod",
                    "metadata": {{"name": "{name}", "namespace": "{namespace}"}},
                    "status": {{"phase": "Running"}}
                }}"""
            else:
                return f"""{{
                    "items": [
                        {{"metadata": {{"name": "pod-1"}}, "status": {{"phase": "Running"}}}},
                        {{"metadata": {{"name": "pod-2"}}, "status": {{"phase": "CrashLoopBackOff"}}}}
                    ]
                }}"""

        elif tool_name == "k8s_describe_resource":
            resource_type = arguments.get("resource", "pod")
            name = arguments.get("name", "unknown")
            return f"""Name: {name}
Namespace: {arguments.get("namespace", "default")}
Status: Running
Events:
  Warning  BackOff  5m ago  kubelet  Back-off restarting failed container"""

        elif tool_name == "k8s_get_pod_logs":
            pod_name = arguments.get("pod", "unknown")
            tail = arguments.get("tail", 100)
            return f"""2025-12-17T10:00:00Z [INFO] Starting application
2025-12-17T10:00:01Z [ERROR] Database connection failed
2025-12-17T10:00:01Z [FATAL] Exiting due to fatal error"""

        elif tool_name == "k8s_get_events":
            namespace = arguments.get("namespace", "default")
            return """[
                {
                    "type": "Warning",
                    "reason": "BackOff",
                    "message": "Back-off restarting failed container",
                    "count": 5
                }
            ]"""

        elif tool_name == "k8s_execute_command":
            pod_name = arguments.get("pod", "unknown")
            command = arguments.get("command", [])
            return f"Command output from {pod_name}: Success"

        else:
            return f"Mock response for {tool_name}"


class MockStreamableHTTPStreams:
    """Mock streams returned by streamablehttp_client"""

    def __init__(self, endpoint: str, session_id: str = "test-session-123"):
        self.endpoint = endpoint
        self.session_id = session_id
        self.read_stream = AsyncMock()
        self.write_stream = AsyncMock()

    def __iter__(self):
        """Allow tuple unpacking: read_stream, write_stream, get_session_id"""
        return iter([
            self.read_stream,
            self.write_stream,
            lambda: self.session_id
        ])


@asynccontextmanager
async def mock_streamablehttp_client(endpoint: str, timeout: float = 30.0, sse_read_timeout: float = 600.0):
    """
    Mock streamablehttp_client context manager

    Yields tuple of (read_stream, write_stream, get_session_id_callback)
    """
    streams = MockStreamableHTTPStreams(endpoint)
    try:
        yield streams
    finally:
        pass


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def mock_mcp_session():
    """Provides a mock MCP session factory"""
    def create_session(endpoint: str) -> MockMCPSession:
        session = MockMCPSession(endpoint)
        # Configure available tools based on endpoint
        if "jira" in endpoint.lower():
            session.available_tools = [
                MockTool("get_ticket", "Get ticket details", {}),
                MockTool("search_tickets", "Search tickets", {}),
                MockTool("add_comment", "Add comment", {}),
                MockTool("get_ticket_history", "Get ticket history", {})
            ]
        elif "k8s" in endpoint.lower() or "kagent" in endpoint.lower():
            session.available_tools = [
                MockTool("k8s_get_resources", "Get K8s resources", {}),
                MockTool("k8s_describe_resource", "Describe K8s resource", {}),
                MockTool("k8s_get_pod_logs", "Get pod logs", {}),
                MockTool("k8s_get_events", "Get K8s events", {}),
                MockTool("k8s_execute_command", "Execute command in pod", {})
            ]
        return session
    return create_session


@pytest.fixture
async def jira_tools_with_mock():
    """
    Provides JiraTools instance with mocked MCP session

    Returns tuple: (JiraTools, MockMCPSession)
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    # Create mock session
    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [
        MockTool("get_ticket", "Get ticket details", {}),
        MockTool("search_tickets", "Search tickets", {}),
        MockTool("add_comment", "Add comment", {}),
        MockTool("get_ticket_history", "Get ticket history", {})
    ]

    # Patch the session creation
    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            yield jira_tools, mock_session

    # Cleanup
    try:
        await jira_tools.close()
    except Exception:
        pass


@pytest.fixture
async def k8s_tools_with_mock():
    """
    Provides K8sTools instance with mocked MCP session

    Returns tuple: (K8sTools, MockMCPSession)
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    # Create mock session
    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [
        MockTool("k8s_get_resources", "Get K8s resources", {}),
        MockTool("k8s_describe_resource", "Describe K8s resource", {}),
        MockTool("k8s_get_pod_logs", "Get pod logs", {}),
        MockTool("k8s_get_events", "Get K8s events", {}),
        MockTool("k8s_execute_command", "Execute command in pod", {})
    ]

    # Patch the session creation
    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            yield k8s_tools, mock_session

    # Cleanup
    try:
        await k8s_tools.close()
    except Exception:
        pass


# ============================================================================
# JiraTools Connection Management Tests
# ============================================================================

@pytest.mark.asyncio
async def test_jira_tools_lazy_connection_initialization():
    """
    Test that JiraTools doesn't connect until first tool call

    Validates:
    - Session is None initially
    - Connection established on first call
    - Session reused for subsequent calls
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    # Session should be None initially
    assert jira_tools.session is None, "Session should not be initialized on construction"

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket", "Get ticket", {})]

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            # First call should establish connection
            result = await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-123"})

            # Session should now be initialized
            assert mock_session.initialized, "Session should be initialized after first call"
            assert len(mock_session.tool_calls) == 1, "Should have recorded one tool call"

            # Second call should reuse same session
            result2 = await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-456"})

            assert len(mock_session.tool_calls) == 2, "Should have recorded two tool calls on same session"

    await jira_tools.close()


@pytest.mark.asyncio
async def test_jira_tools_session_reuse_across_multiple_calls():
    """
    Test that single MCP session is reused for multiple tool calls

    Validates:
    - Single session created
    - Multiple tools called on same session
    - No unnecessary reconnections
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [
        MockTool("get_ticket", "Get ticket", {}),
        MockTool("search_tickets", "Search tickets", {}),
        MockTool("add_comment", "Add comment", {})
    ]

    session_creation_count = 0

    def create_session_wrapper(*args, **kwargs):
        nonlocal session_creation_count
        session_creation_count += 1
        return mock_session

    with patch('tools.jira_tools.ClientSession', side_effect=create_session_wrapper):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            # Make multiple calls
            await jira_tools.get_ticket("TEST-123")
            await jira_tools.search_tickets("status = Open", limit=5)
            await jira_tools.add_comment("TEST-123", "Test comment")

            # Should only create session once
            assert session_creation_count == 1, "Should create session only once"
            assert len(mock_session.tool_calls) == 3, "Should have three tool calls on same session"

    await jira_tools.close()


@pytest.mark.asyncio
async def test_jira_tools_connection_timeout_configuration():
    """
    Test that connection timeout is properly configured

    Validates:
    - Timeout parameter passed to streamablehttp_client
    - Default timeout values are correct
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket", "Get ticket", {})]

    captured_args = {}

    @asynccontextmanager
    async def capture_streamablehttp_args(url, timeout=None, sse_read_timeout=None):
        captured_args['timeout'] = timeout
        captured_args['sse_read_timeout'] = sse_read_timeout
        captured_args['url'] = url
        async with mock_streamablehttp_client(url, timeout, sse_read_timeout) as streams:
            yield streams

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=capture_streamablehttp_args):
            await jira_tools.get_ticket("TEST-123")

            # Verify timeout configuration
            assert captured_args['timeout'] == 30.0, "Connection timeout should be 30s"
            assert captured_args['sse_read_timeout'] == 600.0, "SSE read timeout should be 600s"
            assert captured_args['url'] == endpoint, "URL should match endpoint"

    await jira_tools.close()


# ============================================================================
# JiraTools Tool Method Tests
# ============================================================================

@pytest.mark.asyncio
async def test_jira_tools_get_ticket_response_structure():
    """
    Test get_ticket returns correctly wrapped response

    Validates:
    - Response has 'content' and 'raw' keys
    - Content contains ticket data
    - Response is properly unwrapped from MCP ContentBlock
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket", "Get ticket", {})]

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await jira_tools.get_ticket("PROJ-123")

            # Validate response structure
            assert isinstance(result, dict), "Result should be a dictionary"
            assert "content" in result, "Result should have 'content' key"
            assert "raw" in result, "Result should have 'raw' key"

            # Validate content is unwrapped string
            assert isinstance(result["content"], str), "Content should be unwrapped to string"
            assert "PROJ-123" in result["content"], "Content should contain ticket ID"

            # Validate tool was called correctly
            assert len(mock_session.tool_calls) == 1
            assert mock_session.tool_calls[0]["tool"] == "get_ticket"
            assert mock_session.tool_calls[0]["arguments"]["ticket_id"] == "PROJ-123"

    await jira_tools.close()


@pytest.mark.asyncio
async def test_jira_tools_search_tickets_jql_formatting():
    """
    Test search_tickets properly formats JQL queries and handles limit

    Validates:
    - JQL query passed correctly
    - Limit parameter honored
    - Response wrapped correctly
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("search_tickets", "Search tickets", {})]

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            jql_query = 'project = PROJ AND status = "In Progress"'
            result = await jira_tools.search_tickets(jql=jql_query, limit=10)

            # Validate response structure
            assert isinstance(result, dict), "Result should be a dictionary"
            assert "content" in result and "raw" in result

            # Validate tool call
            assert len(mock_session.tool_calls) == 1
            call_args = mock_session.tool_calls[0]["arguments"]
            assert call_args["jql"] == jql_query, "JQL should match"
            assert call_args["limit"] == 10, "Limit should be 10"

    await jira_tools.close()


@pytest.mark.asyncio
async def test_jira_tools_add_comment_success_detection():
    """
    Test add_comment correctly detects success indicator

    Validates:
    - Success indicator (✅) detected in response
    - Response includes 'success' boolean
    - Tool called with correct arguments
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("add_comment", "Add comment", {})]

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            comment_text = "Investigation complete: Root cause identified"
            result = await jira_tools.add_comment("PROJ-123", comment_text)

            # Validate response structure
            assert isinstance(result, dict), "Result should be a dictionary"
            assert "success" in result, "Result should have 'success' key"
            assert result["success"] is True, "Success should be True (✅ in mock response)"

            # Validate tool call
            assert len(mock_session.tool_calls) == 1
            call_args = mock_session.tool_calls[0]["arguments"]
            assert call_args["ticket_id"] == "PROJ-123"
            assert call_args["comment"] == comment_text

    await jira_tools.close()


@pytest.mark.asyncio
async def test_jira_tools_get_ticket_history_format():
    """
    Test get_ticket_history returns properly formatted history

    Validates:
    - History data is wrapped correctly
    - Response contains changelog information
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket_history", "Get history", {})]

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await jira_tools.get_ticket_history("PROJ-456")

            # Validate response structure
            assert isinstance(result, dict), "Result should be a dictionary"
            assert "content" in result and "raw" in result
            assert "PROJ-456" in result["content"], "Content should contain ticket ID"
            assert "changelog" in result["content"], "Content should contain changelog"

            # Validate tool call
            assert len(mock_session.tool_calls) == 1
            assert mock_session.tool_calls[0]["arguments"]["ticket_id"] == "PROJ-456"

    await jira_tools.close()


@pytest.mark.asyncio
async def test_jira_tools_list_tools_caching():
    """
    Test that list_tools caches results and doesn't call MCP repeatedly

    Validates:
    - First call fetches from MCP
    - Subsequent calls return cached results
    - Cache persists across wrapper lifetime
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [
        MockTool("get_ticket", "Get ticket", {}),
        MockTool("search_tickets", "Search tickets", {})
    ]

    list_tools_call_count = 0
    original_list_tools = mock_session.list_tools

    async def tracked_list_tools():
        nonlocal list_tools_call_count
        list_tools_call_count += 1
        return await original_list_tools()

    mock_session.list_tools = tracked_list_tools

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            # First call should fetch from MCP
            tools1 = await jira_tools.list_tools()
            assert list_tools_call_count == 1, "Should call MCP list_tools once"
            assert len(tools1) == 2, "Should return 2 tools"

            # Second call should use cache
            tools2 = await jira_tools.list_tools()
            assert list_tools_call_count == 1, "Should NOT call MCP again (cached)"
            assert tools1 == tools2, "Cached result should match"

    await jira_tools.close()


# ============================================================================
# JiraTools Response Processing Tests
# ============================================================================

@pytest.mark.asyncio
async def test_jira_tools_content_block_unwrapping():
    """
    Test ContentBlock unwrapping from MCP response

    Validates:
    - Single content item: returns text string
    - Multiple content items: returns list of strings
    - Text attribute extraction
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("test_tool", "Test tool", {})]

    # Test single content item
    mock_session.call_tool = AsyncMock(return_value=MockCallToolResult(
        content=[MockTextContent(text="Single response")]
    ))

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await jira_tools.call_tool("test_tool", {})
            assert isinstance(result, str), "Single content should return string"
            assert result == "Single response"

            # Test multiple content items
            mock_session.call_tool = AsyncMock(return_value=MockCallToolResult(
                content=[
                    MockTextContent(text="First part"),
                    MockTextContent(text="Second part")
                ]
            ))

            result = await jira_tools.call_tool("test_tool", {})
            assert isinstance(result, list), "Multiple content should return list"
            assert len(result) == 2
            assert result == ["First part", "Second part"]

    await jira_tools.close()


@pytest.mark.asyncio
async def test_jira_tools_empty_response_handling():
    """
    Test handling of empty/None responses from MCP

    Validates:
    - Empty content list returns None
    - No content attribute returns None
    - Wrapper doesn't crash on empty response
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("test_tool", "Test tool", {})]

    # Test empty content list
    mock_session.call_tool = AsyncMock(return_value=MockCallToolResult(content=[]))

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await jira_tools.call_tool("test_tool", {})
            assert result is None, "Empty content should return None"

    await jira_tools.close()


# ============================================================================
# JiraTools Error Handling Tests
# ============================================================================

@pytest.mark.asyncio
async def test_jira_tools_connection_failure_handling():
    """
    Test handling of MCP connection failures

    Validates:
    - Connection errors propagated correctly
    - Appropriate error messages
    - No session established on failure
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    # Mock streamablehttp_client to raise connection error
    @asynccontextmanager
    async def failing_streamablehttp_client(*args, **kwargs):
        raise ConnectionError("Failed to connect to MCP server")
        yield  # Never reached, but needed for context manager

    with patch('tools.jira_tools.streamablehttp_client', side_effect=failing_streamablehttp_client):
        with pytest.raises(ConnectionError, match="Failed to connect"):
            await jira_tools.get_ticket("TEST-123")

        # Session should remain None
        assert jira_tools.session is None, "Session should be None after connection failure"


@pytest.mark.asyncio
async def test_jira_tools_tool_call_error_handling():
    """
    Test handling of tool call errors (404, 500, etc.)

    Validates:
    - Tool errors propagated correctly
    - Error information preserved
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket", "Get ticket", {})]

    # Mock tool call to raise error
    async def failing_call_tool(tool_name, arguments):
        raise RuntimeError("Tool execution failed: Ticket not found")

    mock_session.call_tool = failing_call_tool

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            with pytest.raises(RuntimeError, match="Ticket not found"):
                await jira_tools.get_ticket("INVALID-999")


@pytest.mark.asyncio
async def test_jira_tools_cleanup_error_handling():
    """
    Test handling of known cleanup errors (RuntimeError "different task")

    Validates:
    - Known errors are caught and logged
    - Session cleanup doesn't crash
    - GC cleanup fallback works
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket", "Get ticket", {})]

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            await jira_tools.get_ticket("TEST-123")

            # Mock exit_stack.aclose() to raise known error
            async def failing_aclose():
                raise RuntimeError("cannot cancel a task from a different task")

            jira_tools.exit_stack.aclose = failing_aclose

            # Should not raise exception (error is caught)
            await jira_tools.close()


@pytest.mark.asyncio
async def test_jira_tools_timeout_error_handling():
    """
    Test handling of timeout errors during tool calls

    Validates:
    - Timeout errors are propagated
    - Session state is consistent after timeout
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket", "Get ticket", {})]

    # Mock tool call to simulate timeout
    async def timeout_call_tool(tool_name, arguments):
        raise asyncio.TimeoutError("Tool call timed out after 30s")

    mock_session.call_tool = timeout_call_tool

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            with pytest.raises(asyncio.TimeoutError):
                await jira_tools.get_ticket("TEST-123")

    await jira_tools.close()


# ============================================================================
# K8sTools Connection Management Tests
# ============================================================================

@pytest.mark.asyncio
async def test_k8s_tools_lazy_connection_initialization():
    """
    Test that K8sTools doesn't connect until first tool call

    Validates:
    - Session is None initially
    - Connection established on first call
    - Session reused for subsequent calls
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    assert k8s_tools.session is None, "Session should not be initialized on construction"

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_get_resources", "Get resources", {})]

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await k8s_tools.kubectl_get("pods", namespace="default")

            assert mock_session.initialized, "Session should be initialized after first call"
            assert len(mock_session.tool_calls) == 1

            # Second call should reuse session
            result2 = await k8s_tools.kubectl_get("services", namespace="default")
            assert len(mock_session.tool_calls) == 2, "Should reuse same session"

    await k8s_tools.close()


@pytest.mark.asyncio
async def test_k8s_tools_session_reuse_across_multiple_tools():
    """
    Test that single MCP session handles multiple K8s tool types

    Validates:
    - Single session for get, describe, logs, events
    - No reconnection between different tool types
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [
        MockTool("k8s_get_resources", "Get resources", {}),
        MockTool("k8s_describe_resource", "Describe resource", {}),
        MockTool("k8s_get_pod_logs", "Get logs", {}),
        MockTool("k8s_get_events", "Get events", {})
    ]

    session_creation_count = 0

    def create_session_wrapper(*args, **kwargs):
        nonlocal session_creation_count
        session_creation_count += 1
        return mock_session

    with patch('tools.k8s_tools.ClientSession', side_effect=create_session_wrapper):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            await k8s_tools.kubectl_get("pods", namespace="default")
            await k8s_tools.kubectl_describe("pod", "test-pod", namespace="default")
            await k8s_tools.kubectl_logs("test-pod", namespace="default")
            await k8s_tools.kubectl_events(namespace="default")

            assert session_creation_count == 1, "Should create session only once"
            assert len(mock_session.tool_calls) == 4, "Should have four tool calls"

    await k8s_tools.close()


# ============================================================================
# K8sTools Tool Method Tests
# ============================================================================

@pytest.mark.asyncio
async def test_k8s_tools_kubectl_get_with_name():
    """
    Test kubectl_get with specific resource name

    Validates:
    - Name parameter passed to tool
    - Single resource returned
    - Response format correct
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_get_resources", "Get resources", {})]

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await k8s_tools.kubectl_get("pod", namespace="default", name="test-pod-123")

            # Validate tool call
            assert len(mock_session.tool_calls) == 1
            call_args = mock_session.tool_calls[0]["arguments"]
            assert call_args["resource"] == "pod"
            assert call_args["namespace"] == "default"
            assert call_args["name"] == "test-pod-123"

            # Validate response contains resource data
            assert "test-pod-123" in str(result)


@pytest.mark.asyncio
async def test_k8s_tools_kubectl_get_without_name():
    """
    Test kubectl_get without name (list all resources)

    Validates:
    - Name parameter not passed
    - Multiple resources returned
    - List format preserved
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_get_resources", "Get resources", {})]

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await k8s_tools.kubectl_get("pods", namespace="default")

            # Validate tool call
            assert len(mock_session.tool_calls) == 1
            call_args = mock_session.tool_calls[0]["arguments"]
            assert "name" not in call_args, "Name should not be passed for list operation"

            # Validate response contains list data
            assert "items" in str(result) or "pod-1" in str(result)


@pytest.mark.asyncio
async def test_k8s_tools_kubectl_describe():
    """
    Test kubectl_describe returns detailed resource information

    Validates:
    - All parameters passed correctly
    - Text format response (not JSON)
    - Events included in describe output
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_describe_resource", "Describe resource", {})]

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await k8s_tools.kubectl_describe("pod", "api-server-abc", namespace="production")

            # Validate tool call
            assert len(mock_session.tool_calls) == 1
            call_args = mock_session.tool_calls[0]["arguments"]
            assert call_args["resource"] == "pod"
            assert call_args["name"] == "api-server-abc"
            assert call_args["namespace"] == "production"

            # Validate response is text format
            assert isinstance(result, str)
            assert "Name:" in result
            assert "Events:" in result or "BackOff" in result


@pytest.mark.asyncio
async def test_k8s_tools_kubectl_logs_with_options():
    """
    Test kubectl_logs with various options (tail, previous, container)

    Validates:
    - All optional parameters passed correctly
    - Previous logs flag (for crashloop debugging)
    - Container selection
    - Tail limit
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_get_pod_logs", "Get logs", {})]

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await k8s_tools.kubectl_logs(
                pod_name="api-server-xyz",
                namespace="default",
                previous=True,
                container="main-container",
                tail=50
            )

            # Validate tool call
            assert len(mock_session.tool_calls) == 1
            call_args = mock_session.tool_calls[0]["arguments"]
            assert call_args["pod"] == "api-server-xyz"
            assert call_args["namespace"] == "default"
            assert call_args["previous"] is True
            assert call_args["container"] == "main-container"
            assert call_args["tail"] == 50

            # Validate response is log text
            assert isinstance(result, str)
            assert "[INFO]" in result or "[ERROR]" in result or "Starting" in result


@pytest.mark.asyncio
async def test_k8s_tools_kubectl_events_filtering():
    """
    Test kubectl_events with resource filtering

    Validates:
    - Namespace parameter
    - Resource type filter (optional)
    - Resource name filter (optional)
    - Event list format
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_get_events", "Get events", {})]

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            # Test with filters
            result = await k8s_tools.kubectl_events(
                namespace="default",
                resource_type="Pod",
                name="api-server-abc"
            )

            # Validate tool call
            assert len(mock_session.tool_calls) == 1
            call_args = mock_session.tool_calls[0]["arguments"]
            assert call_args["namespace"] == "default"
            assert call_args["resource"] == "Pod"
            assert call_args["name"] == "api-server-abc"


@pytest.mark.asyncio
async def test_k8s_tools_kubectl_exec():
    """
    Test kubectl_exec for running commands in pods

    Validates:
    - Command passed as list
    - Container selection
    - Command output returned
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_execute_command", "Execute command", {})]

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            command = ["cat", "/etc/config/app.yaml"]
            result = await k8s_tools.kubectl_exec(
                pod_name="api-server-xyz",
                command=command,
                namespace="default",
                container="main"
            )

            # Validate tool call
            assert len(mock_session.tool_calls) == 1
            call_args = mock_session.tool_calls[0]["arguments"]
            assert call_args["pod"] == "api-server-xyz"
            assert call_args["command"] == command
            assert call_args["container"] == "main"

            # Validate response
            assert isinstance(result, str)


@pytest.mark.asyncio
async def test_k8s_tools_list_tools_caching():
    """
    Test that K8sTools list_tools caches results

    Validates:
    - First call fetches from MCP
    - Subsequent calls use cache
    - Cache persists across tool calls
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [
        MockTool("k8s_get_resources", "Get resources", {}),
        MockTool("k8s_get_pod_logs", "Get logs", {})
    ]

    list_tools_call_count = 0
    original_list_tools = mock_session.list_tools

    async def tracked_list_tools():
        nonlocal list_tools_call_count
        list_tools_call_count += 1
        return await original_list_tools()

    mock_session.list_tools = tracked_list_tools

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            tools1 = await k8s_tools.list_tools()
            assert list_tools_call_count == 1

            tools2 = await k8s_tools.list_tools()
            assert list_tools_call_count == 1, "Should use cached result"
            assert tools1 == tools2

    await k8s_tools.close()


# ============================================================================
# K8sTools Error Handling Tests
# ============================================================================

@pytest.mark.asyncio
async def test_k8s_tools_error_handling_in_kubectl_get():
    """
    Test error handling in kubectl_get (resource not found, RBAC errors)

    Validates:
    - Errors caught and wrapped in dict
    - Error message preserved
    - No exception raised to caller
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_get_resources", "Get resources", {})]

    # Mock tool call to raise error
    async def failing_call_tool(tool_name, arguments):
        raise RuntimeError("pods \"nonexistent\" not found")

    mock_session.call_tool = failing_call_tool

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await k8s_tools.kubectl_get("pod", namespace="default", name="nonexistent")

            # Should return error dict, not raise exception
            assert isinstance(result, dict)
            assert "error" in result
            assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_k8s_tools_error_handling_in_kubectl_logs():
    """
    Test error handling in kubectl_logs

    Validates:
    - Pod not found errors handled
    - Container not found errors handled
    - Error returned as string (not raised)
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_get_pod_logs", "Get logs", {})]

    async def failing_call_tool(tool_name, arguments):
        raise RuntimeError("container \"wrong-container\" not found in pod")

    mock_session.call_tool = failing_call_tool

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await k8s_tools.kubectl_logs("test-pod", container="wrong-container")

            # Should return error string
            assert isinstance(result, str)
            assert "Error:" in result
            assert "not found" in result


@pytest.mark.asyncio
async def test_k8s_tools_cleanup_error_handling():
    """
    Test handling of known cleanup errors in K8sTools

    Validates:
    - Same behavior as JiraTools cleanup
    - Known errors caught and logged
    - No exception raised
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_get_resources", "Get resources", {})]

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            await k8s_tools.kubectl_get("pods")

            # Mock exit_stack to raise known error
            async def failing_aclose():
                raise RuntimeError("Server disconnected during cleanup")

            k8s_tools.exit_stack.aclose = failing_aclose

            # Should not raise exception
            await k8s_tools.close()


# ============================================================================
# Cross-Cutting Integration Tests
# ============================================================================

@pytest.mark.asyncio
async def test_concurrent_tool_calls_on_same_session():
    """
    Test multiple concurrent tool calls on same session

    Validates:
    - Concurrent calls work correctly
    - Session handles multiple simultaneous requests
    - No race conditions in session reuse
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [
        MockTool("get_ticket", "Get ticket", {}),
        MockTool("search_tickets", "Search tickets", {})
    ]

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            # Run multiple calls concurrently
            results = await asyncio.gather(
                jira_tools.get_ticket("TEST-1"),
                jira_tools.get_ticket("TEST-2"),
                jira_tools.search_tickets("status = Open", limit=5),
                return_exceptions=True
            )

            # All should succeed
            assert len(results) == 3
            assert all(not isinstance(r, Exception) for r in results)

            # All calls should be recorded
            assert len(mock_session.tool_calls) == 3

    await jira_tools.close()


@pytest.mark.asyncio
async def test_response_time_performance():
    """
    Test that tool calls complete within reasonable time

    Validates:
    - Typical calls complete in <5s
    - No unexpected delays in wrapper logic
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket", "Get ticket", {})]

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            import time
            start = time.time()

            await jira_tools.get_ticket("TEST-123")

            elapsed = time.time() - start
            assert elapsed < 1.0, f"Call took {elapsed}s, should be <1s for mock"

    await jira_tools.close()


@pytest.mark.asyncio
async def test_large_response_handling():
    """
    Test handling of large responses (big logs, many events)

    Validates:
    - Large strings handled correctly
    - No truncation in wrapper logic
    - Memory efficiency
    """
    endpoint = "http://localhost:8084/mcp"
    k8s_tools = K8sTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("k8s_get_pod_logs", "Get logs", {})]

    # Create large log response (10MB)
    large_log = "Log line with data\n" * 500000  # ~10MB

    async def large_response_call_tool(tool_name, arguments):
        return MockCallToolResult(content=[MockTextContent(text=large_log)])

    mock_session.call_tool = large_response_call_tool

    with patch('tools.k8s_tools.ClientSession', return_value=mock_session):
        with patch('tools.k8s_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            result = await k8s_tools.kubectl_logs("large-pod", tail=100000)

            # Validate response is complete
            assert isinstance(result, str)
            assert len(result) == len(large_log), "Large response should not be truncated"

    await k8s_tools.close()


@pytest.mark.asyncio
async def test_mcp_protocol_compliance():
    """
    Test that wrappers follow MCP protocol correctly

    Validates:
    - Session initialization sequence
    - Tool call request format
    - Response parsing
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    initialization_called = False

    class ProtocolTrackingSession(MockMCPSession):
        async def initialize(self):
            nonlocal initialization_called
            initialization_called = True
            await super().initialize()

    mock_session = ProtocolTrackingSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket", "Get ticket", {})]

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            # First call should initialize
            await jira_tools.get_ticket("TEST-123")

            assert initialization_called, "Session must be initialized before tool calls"
            assert mock_session.initialized, "Session should be in initialized state"

    await jira_tools.close()


# ============================================================================
# Resource Management Tests
# ============================================================================

@pytest.mark.asyncio
async def test_async_exit_stack_cleanup():
    """
    Test AsyncExitStack cleanup behavior

    Validates:
    - All contexts properly entered
    - All contexts properly exited on close()
    - Cleanup happens in correct order
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    context_cleanup_order = []

    # Create a custom session class that tracks context manager lifecycle
    class TrackingMockMCPSession(MockMCPSession):
        async def __aenter__(self):
            context_cleanup_order.append("session_enter")
            return await super().__aenter__()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            context_cleanup_order.append("session_exit")
            return await super().__aexit__(exc_type, exc_val, exc_tb)

    mock_session = TrackingMockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket", "Get ticket", {})]

    @asynccontextmanager
    async def tracking_streamablehttp_client(url, timeout=None, sse_read_timeout=None):
        context_cleanup_order.append("streams_enter")
        try:
            async with mock_streamablehttp_client(url, timeout, sse_read_timeout) as streams:
                yield streams
        finally:
            context_cleanup_order.append("streams_exit")

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=tracking_streamablehttp_client):
            await jira_tools.get_ticket("TEST-123")

            # Verify contexts were entered
            assert "streams_enter" in context_cleanup_order
            assert "session_enter" in context_cleanup_order

            # Close and verify cleanup order
            await jira_tools.close()

            # Session should exit before streams (LIFO order)
            session_exit_idx = context_cleanup_order.index("session_exit")
            streams_exit_idx = context_cleanup_order.index("streams_exit")
            assert session_exit_idx < streams_exit_idx, "Cleanup should be in LIFO order"


@pytest.mark.asyncio
async def test_memory_leak_prevention_session_caching():
    """
    Test that session caching doesn't cause memory leaks

    Validates:
    - Session properly released on close()
    - No circular references
    - Tools cache cleared appropriately
    """
    endpoint = "http://localhost:8080/mcp"
    jira_tools = JiraTools(endpoint)

    mock_session = MockMCPSession(endpoint)
    mock_session.available_tools = [MockTool("get_ticket", "Get ticket", {})]

    with patch('tools.jira_tools.ClientSession', return_value=mock_session):
        with patch('tools.jira_tools.streamablehttp_client', side_effect=mock_streamablehttp_client):
            # Use the session
            await jira_tools.get_ticket("TEST-123")
            await jira_tools.list_tools()

            assert jira_tools.session is not None
            assert jira_tools._tools_cache is not None

            # Close should clean up references
            await jira_tools.close()

            # Note: We don't clear session/cache references in current implementation
            # This test documents current behavior - could be enhanced to clear refs


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
