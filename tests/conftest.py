"""
Shared pytest configuration and fixtures for LangGraph agent tests

This module provides reusable fixtures for testing the Kagent checkpointer
integration, LangGraph multi-agent system, webhook server, and MCP integrations.
"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Any, List, AsyncIterator
from unittest.mock import AsyncMock, Mock, MagicMock
from urllib.parse import urlparse

import pytest
import httpx

# Add src directory to Python path for imports
PROJECT_ROOT = Path(__file__).parent.parent
# The actual src directory is in langgraph-agent subdirectory
LANGGRAPH_AGENT_DIR = PROJECT_ROOT / "langgraph-agent"
SRC_DIR = LANGGRAPH_AGENT_DIR / "src"
# Add both langgraph-agent directory and src for package imports
sys.path.insert(0, str(LANGGRAPH_AGENT_DIR))
sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def project_root() -> Path:
    """
    Provides the absolute path to the project root directory

    Returns:
        Path: Absolute path to langgraph-agent/ directory
    """
    return PROJECT_ROOT


@pytest.fixture
def sample_agent_state() -> Dict[str, Any]:
    """
    Provides a sample AgentState for testing

    Returns:
        Dict: Sample state matching AgentState schema
    """
    return {
        "ticket_id": "TEST-123",
        "ticket_summary": "Test CrashLoopBackOff in api-server",
        "ticket_description": "The api-server pod is crashing repeatedly",
        "messages": [],
        "iteration_count": 0,
        "root_cause": None,
        "confidence_level": None
    }


# ============================================================================
# Webhook Server Test Fixtures
# ============================================================================

@pytest.fixture
def sample_jira_webhook_payload() -> Dict[str, Any]:
    """
    Provides a sample Jira webhook payload for testing

    Returns:
        Dict: Complete Jira webhook payload matching expected schema
    """
    return {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "key": "GAUDISW-123",
            "fields": {
                "project": {
                    "key": "GAUDISW"
                },
                "components": [
                    {"name": "DevOps_K8S"}
                ],
                "issuetype": {
                    "name": "Bug"
                },
                "summary": "CrashLoopBackOff in api-server pod"
            }
        }
    }


@pytest.fixture
def sample_filtered_webhook_payloads() -> List[Dict[str, Any]]:
    """
    Provides multiple webhook payloads for filter testing

    Returns:
        List[Dict]: Payloads with various project/component/type combinations
    """
    return [
        # Should be filtered - wrong project
        {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": "WRONGPROJ-123",
                "fields": {
                    "project": {"key": "WRONGPROJ"},
                    "components": [{"name": "DevOps_K8S"}],
                    "issuetype": {"name": "Bug"},
                    "summary": "Test issue"
                }
            }
        },
        # Should be filtered - wrong component
        {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": "GAUDISW-124",
                "fields": {
                    "project": {"key": "GAUDISW"},
                    "components": [{"name": "WrongComponent"}],
                    "issuetype": {"name": "Bug"},
                    "summary": "Test issue"
                }
            }
        },
        # Should be filtered - wrong issue type
        {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": "GAUDISW-125",
                "fields": {
                    "project": {"key": "GAUDISW"},
                    "components": [{"name": "DevOps_K8S"}],
                    "issuetype": {"name": "Story"},
                    "summary": "Test issue"
                }
            }
        },
        # Should pass - correct combination
        {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": "GAUDISW-126",
                "fields": {
                    "project": {"key": "GAUDISW"},
                    "components": [{"name": "DevOps_K8S"}],
                    "issuetype": {"name": "Bug"},
                    "summary": "Valid test issue"
                }
            }
        }
    ]


# ============================================================================
# MCP Server Mock Fixtures
# ============================================================================

class MockMCPServer:
    """Mock MCP server for testing"""

    def __init__(self, endpoint: str, tools: List[Dict[str, Any]]):
        self.endpoint = endpoint
        self.tools = tools
        self.call_count = 0
        self.tool_calls: List[Dict[str, Any]] = []
        self.connected = False

    async def handle_initialize(self) -> Dict[str, Any]:
        """Handle MCP initialize request"""
        self.connected = True
        return {
            "protocolVersion": "1.0.0",
            "serverInfo": {"name": "mock-server", "version": "1.0.0"}
        }

    async def handle_list_tools(self) -> List[Dict[str, Any]]:
        """Handle MCP list_tools request"""
        return self.tools

    async def handle_call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Handle MCP call_tool request"""
        self.call_count += 1
        self.tool_calls.append({"tool": tool_name, "arguments": arguments})

        # Return mock responses based on tool name
        if tool_name == "get_ticket":
            return self._mock_get_ticket_response(arguments["ticket_id"])
        elif tool_name == "search_tickets":
            return self._mock_search_tickets_response(arguments.get("jql", ""))
        elif tool_name == "add_comment":
            return self._mock_add_comment_response(arguments["ticket_id"])
        elif tool_name == "k8s_get_resources":
            return self._mock_k8s_get_resources(arguments)
        elif tool_name == "k8s_get_pod_logs":
            return self._mock_k8s_get_logs(arguments)
        else:
            return {"status": "success", "result": "mock response"}

    def _mock_get_ticket_response(self, ticket_id: str) -> str:
        return f"Ticket {ticket_id}: CrashLoopBackOff in api-server pod"

    def _mock_search_tickets_response(self, jql: str) -> str:
        return "Found 3 similar tickets: PROJ-100, PROJ-101, PROJ-102"

    def _mock_add_comment_response(self, ticket_id: str) -> str:
        return f"✅ Comment added to {ticket_id}"

    def _mock_k8s_get_resources(self, args: Dict[str, Any]) -> str:
        resource = args.get("resource", "pods")
        return f"Mock {resource} list from namespace {args.get('namespace', 'default')}"

    def _mock_k8s_get_logs(self, args: Dict[str, Any]) -> str:
        return f"Mock logs for pod {args.get('pod', 'unknown')}"


# ============================================================================
# HTTP Client Mock Fixtures
# ============================================================================

@pytest.fixture
def mock_agent_api_client() -> AsyncMock:
    """
    Provides a mock HTTP client for agent API calls

    Returns:
        AsyncMock: Mock httpx.AsyncClient
    """
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    # Mock successful investigation trigger
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "investigation_id": "inv-123-abc",
        "status": "started",
        "ticket_id": "GAUDISW-123"
    }

    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.get = AsyncMock(return_value=mock_response)

    return mock_client


# ============================================================================
# MCP Session Tracking Fixtures
# ============================================================================

@pytest.fixture
def mcp_session_tracker():
    """
    Tracks MCP session lifecycle for testing

    Provides methods to track:
    - Connection attempts
    - Session reuse
    - Tool call counts
    - Cleanup calls
    """

    class SessionTracker:
        def __init__(self):
            self.connections = []
            self.disconnections = []
            self.tool_calls = []
            self.active_sessions = set()

        def track_connection(self, endpoint: str, session_id: str):
            """Track new MCP session connection"""
            self.connections.append({"endpoint": endpoint, "session_id": session_id})
            self.active_sessions.add(session_id)

        def track_disconnection(self, session_id: str):
            """Track MCP session disconnection"""
            self.disconnections.append({"session_id": session_id})
            self.active_sessions.discard(session_id)

        def track_tool_call(self, session_id: str, tool_name: str, arguments: Dict[str, Any]):
            """Track MCP tool call"""
            self.tool_calls.append({
                "session_id": session_id,
                "tool": tool_name,
                "arguments": arguments
            })

        def get_connection_count(self, endpoint: str = None) -> int:
            """Get number of connections (optionally filtered by endpoint)"""
            if endpoint:
                return sum(1 for c in self.connections if c["endpoint"] == endpoint)
            return len(self.connections)

        def get_tool_call_count(self, session_id: str = None) -> int:
            """Get number of tool calls (optionally filtered by session)"""
            if session_id:
                return sum(1 for c in self.tool_calls if c["session_id"] == session_id)
            return len(self.tool_calls)

        def was_session_reused(self, session_id: str) -> bool:
            """Check if a session was reused for multiple tool calls"""
            calls_for_session = [c for c in self.tool_calls if c["session_id"] == session_id]
            return len(calls_for_session) > 1

    return SessionTracker()


# ============================================================================
# MCP Protocol Mock Classes
# ============================================================================

class MockContentBlock:
    """Mock MCP ContentBlock for testing tool results"""
    def __init__(self, text: str):
        self.text = text


class MockToolResult:
    """Mock MCP tool call result with content blocks"""
    def __init__(self, content):
        self.content = (
            content if isinstance(content, list)
            else [MockContentBlock(str(content))]
        )


class MockListToolsResult:
    """Mock MCP list_tools result"""
    def __init__(self, tools):
        self.tools = tools
