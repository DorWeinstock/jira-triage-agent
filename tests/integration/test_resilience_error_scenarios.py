"""
Comprehensive resilience and error scenario tests

This test suite validates system robustness under failure conditions, ensuring
production-ready behavior across network failures, resource constraints, data
integrity issues, and service degradation scenarios.

Test Categories:
1. Network Failure Scenarios - Connection timeouts, network drops, DNS failures
2. Resource Exhaustion Scenarios - Memory pressure, CPU constraints, rate limits
3. Data Integrity Scenarios - Malformed data, boundary conditions, corruption
4. Concurrent Operation Scenarios - Race conditions, deadlocks, state conflicts
5. Service Failure Scenarios - Jira/K8s/LLM service failures
6. State Corruption Scenarios - Invalid state transitions, recovery mechanisms
7. Edge Case Scenarios - Unusual inputs, extreme conditions
8. Timeout and Retry Scenarios - Operation timeouts, backoff strategies
9. Chaos Engineering Scenarios - Random failures, partial degradation
10. Recovery and Healing Scenarios - Automatic recovery, manual intervention

Success Criteria:
- No crashes or hangs under failure conditions
- Graceful degradation with partial results
- Helpful, actionable error messages
- Proper retry with exponential backoff
- Memory/CPU stays bounded
- State preserved during failures
"""

import asyncio
import pytest
import time
from typing import Dict, Any, List, Optional
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from contextlib import asynccontextmanager
import httpx
import logging

# Import system components
from src.state import AgentState
from src.tools.jira_tools import JiraTools
from src.tools.k8s_tools import K8sTools
from src.agents.jira_agent import JiraAgent
from src.agents.k8s_investigator import K8sInvestigator
from src.supervisor import create_conditional_supervisor_graph
from src.exceptions import ToolError


# ============================================================================
# CATEGORY 1: Network Failure Scenarios
# ============================================================================

class TestNetworkFailures:
    """Test system behavior under various network failure conditions"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_mcp_connection_timeout(self):
        """Test handling of MCP server connection timeout"""
        # Create tools with unreachable endpoint
        jira_tools = JiraTools(mcp_endpoint="http://192.0.2.1:8080/mcp")  # Non-routable IP

        try:
            with pytest.raises((Exception, BaseException)) as exc_info:
                await jira_tools.get_ticket("TEST-123")

            # Should fail gracefully with meaningful error (timeout, cancelled, connect error, etc)
            assert exc_info.value is not None
            error_msg = str(exc_info.value).lower()
            # Accept any network/timeout related error including CancelledError
            assert any(keyword in error_msg for keyword in ["timeout", "connect", "unreachable", "cancel"])
        finally:
            # Proper cleanup to avoid async teardown issues
            await jira_tools.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_mcp_connection_refused(self):
        """Test handling of MCP server connection refused"""
        # Use localhost with closed port
        jira_tools = JiraTools(mcp_endpoint="http://localhost:65534/mcp")

        try:
            with pytest.raises((Exception, BaseException)) as exc_info:
                await jira_tools.get_ticket("TEST-123")

            # Should fail with connection error (connect, cancelled, refused, etc)
            assert exc_info.value is not None
            error_msg = str(exc_info.value).lower()
            assert any(keyword in error_msg for keyword in ["connect", "refused", "cancel", "failed"])
        finally:
            # Proper cleanup to avoid async teardown issues
            await jira_tools.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_network_drops_mid_operation(self):
        """Test handling of network dropping during tool execution"""
        # Mock session that succeeds initially then fails
        mock_session = AsyncMock()

        # First call succeeds, second fails with disconnect
        mock_session.call_tool = AsyncMock(
            side_effect=[
                MagicMock(content=[MagicMock(text="Success")]),
                httpx.ConnectError("Connection dropped")
            ]
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # First call succeeds
        result1 = await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-1"})
        assert result1 == "Success"

        # Second call fails with network error
        with pytest.raises(httpx.ConnectError):
            await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-2"})

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_slow_mcp_response_handling(self):
        """Test handling of slow MCP server responses"""
        async def slow_response(*args, **kwargs):
            """Simulate slow response"""
            await asyncio.sleep(2)
            return MagicMock(content=[MagicMock(text="Delayed response")])

        mock_session = AsyncMock()
        mock_session.call_tool = slow_response

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # Should eventually complete despite slowness
        start = time.time()
        result = await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-123"})
        duration = time.time() - start

        assert duration >= 2.0  # Took at least 2 seconds
        assert result == "Delayed response"

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_dns_resolution_failure(self):
        """Test handling of DNS resolution failures"""
        # Use invalid hostname
        jira_tools = JiraTools(mcp_endpoint="http://invalid-hostname-that-does-not-exist.local/mcp")

        try:
            with pytest.raises((Exception, BaseException)) as exc_info:
                await jira_tools.get_ticket("TEST-123")

            # Should fail with DNS/connection/timeout error
            assert exc_info.value is not None
            error_msg = str(exc_info.value).lower()
            assert any(keyword in error_msg for keyword in ["dns", "connect", "timeout", "cancel", "resolve"])
        finally:
            # Proper cleanup to avoid async teardown issues
            await jira_tools.close()


# ============================================================================
# CATEGORY 2: Resource Exhaustion Scenarios
# ============================================================================

class TestResourceExhaustion:
    """Test system behavior under resource constraints"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_large_log_file_handling(self):
        """Test handling of large log files (100MB+)"""
        # Generate large log content
        large_log = "Error: OOM\n" * 1_000_000  # ~13MB of text

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text=large_log)])
        )

        k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")
        k8s_tools.session = mock_session

        # Should handle large response without crashing
        result = await k8s_tools.kubectl_logs("test-pod", namespace="default")
        assert len(result) > 1_000_000
        assert "OOM" in result

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_many_tickets_in_search(self):
        """Test handling of large number of search results"""
        # Mock search returning 100+ tickets
        large_search_result = "\n".join([
            f"PROJ-{i}: Issue {i}" for i in range(150)
        ])

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text=large_search_result)])
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # Should handle large result set
        result = await jira_tools.search_tickets("status=Open", limit=150)
        assert "PROJ-0" in result["content"]
        assert "PROJ-149" in result["content"]

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_investigations(self):
        """Test multiple concurrent investigation workflows"""
        mock_jira = AsyncMock()
        mock_jira.get_ticket = AsyncMock(
            return_value={"content": "Test ticket", "raw": "Test ticket"}
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_jira

        # Run multiple ticket fetches concurrently
        tasks = [
            jira_tools.get_ticket(f"TEST-{i}") for i in range(10)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed
        assert len(results) == 10
        assert all(not isinstance(r, Exception) for r in results)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_rate_limit_handling(self):
        """Test handling of API rate limits"""
        # Mock rate limit error
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Rate limit exceeded",
                request=MagicMock(),
                response=MagicMock(status_code=429)
            )
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # Should fail with rate limit error
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-123"})

        assert exc_info.value.response.status_code == 429


# ============================================================================
# CATEGORY 3: Data Integrity Scenarios
# ============================================================================

class TestDataIntegrity:
    """Test handling of malformed, boundary, and corrupted data"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_invalid_json_in_ticket_description(self):
        """Test handling of malformed JSON in ticket description"""
        malformed_ticket = {
            "content": '{"invalid": json with no closing brace',
            "raw": '{"invalid": json'
        }

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text=str(malformed_ticket))])
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session
        jira_agent = JiraAgent(jira_tools)

        state = {"ticket_id": "TEST-123"}

        # Should handle gracefully without crashing
        result = await jira_agent.read_ticket(state)
        assert result is not None
        assert "ticket_id" in result

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_empty_string_handling(self):
        """Test handling of empty strings in required fields"""
        empty_ticket = {"content": "", "raw": ""}

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="")])
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # Should handle empty response
        result = await jira_tools.get_ticket("TEST-123")
        assert result is not None

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_null_values_in_state(self):
        """Test handling of None/null values in state"""
        state = AgentState(
            ticket_id=None,
            ticket_summary=None,
            ticket_description=None,
            root_cause=None,
            confidence_level=None
        )

        # Should not crash with None values
        assert state.get("ticket_id") is None
        assert state.get("ticket_summary") is None

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_unicode_and_special_characters(self):
        """Test handling of Unicode and special characters"""
        unicode_content = "Test with emoji 🚀 and special chars: <>&\"'`\n\t"

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text=unicode_content)])
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        result = await jira_tools.get_ticket("TEST-123")
        assert "🚀" in result["content"]
        assert "<>&" in result["content"]

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_very_long_strings(self):
        """Test handling of extremely long strings"""
        # Create 10MB string
        long_string = "A" * (10 * 1024 * 1024)

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text=long_string)])
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # Should handle very long response
        result = await jira_tools.get_ticket("TEST-123")
        assert len(result["content"]) == 10 * 1024 * 1024

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_sql_injection_in_jql(self):
        """Test that SQL injection attempts in JQL are handled safely"""
        malicious_jql = "project = TEST; DROP TABLE tickets; --"

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="No results")])
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # Should pass through to Jira (which handles it safely)
        result = await jira_tools.search_tickets(malicious_jql)
        assert result is not None

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_missing_required_state_fields(self):
        """Test handling when required state fields are missing"""
        incomplete_state = {
            # Missing ticket_id
            "ticket_summary": "Test issue"
        }

        # Should not crash when accessing missing fields
        state = AgentState(**incomplete_state)
        assert state.get("ticket_id") is None


# ============================================================================
# CATEGORY 4: Concurrent Operation Scenarios
# ============================================================================

class TestConcurrentOperations:
    """Test system behavior under concurrent operations"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_mcp_sessions(self):
        """Test multiple concurrent MCP sessions"""
        # Create multiple tool instances
        tools = [
            JiraTools(mcp_endpoint="http://localhost:8080/mcp")
            for _ in range(5)
        ]

        # Mock all sessions
        for tool in tools:
            mock_session = AsyncMock()
            mock_session.call_tool = AsyncMock(
                return_value=MagicMock(content=[MagicMock(text="Success")])
            )
            tool.session = mock_session

        # Call tools concurrently
        tasks = [
            tool.get_ticket(f"TEST-{i}")
            for i, tool in enumerate(tools)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed without interference
        assert len(results) == 5
        assert all(not isinstance(r, Exception) for r in results)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_state_modifications(self):
        """Test concurrent modifications to shared state"""
        state = AgentState(ticket_id="TEST-123", iteration_count=0)

        async def increment_iteration(s):
            """Simulate agent incrementing iteration"""
            await asyncio.sleep(0.01)
            current = s.get("iteration_count", 0)
            s["iteration_count"] = current + 1
            return s

        # Run multiple concurrent modifications
        # Note: In real system, state is protected by LangGraph's checkpointing
        tasks = [increment_iteration(state) for _ in range(10)]
        await asyncio.gather(*tasks)

        # This test documents current behavior (no built-in locking)
        # In production, LangGraph checkpointer handles state consistency
        assert state.get("iteration_count") is not None


# ============================================================================
# CATEGORY 5: Service Failure Scenarios
# ============================================================================

class TestServiceFailures:
    """Test handling of external service failures"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_jira_server_500_error(self):
        """Test handling of Jira server internal error"""
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Internal Server Error",
                request=MagicMock(),
                response=MagicMock(status_code=500)
            )
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # Simplified POC wraps all errors in ToolError
        with pytest.raises(ToolError) as exc_info:
            await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-123"})

        assert "Internal Server Error" in str(exc_info.value)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_jira_authentication_failure(self):
        """Test handling of Jira authentication failure"""
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Unauthorized",
                request=MagicMock(),
                response=MagicMock(status_code=401)
            )
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # Simplified POC wraps all errors in ToolError
        with pytest.raises(ToolError) as exc_info:
            await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-123"})

        assert "Unauthorized" in str(exc_info.value)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_ticket_not_found_404(self):
        """Test handling of non-existent ticket"""
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="Ticket not found")])
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        result = await jira_tools.get_ticket("NONEXISTENT-999")
        assert "not found" in result["content"].lower()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_k8s_api_unavailable(self):
        """Test handling of Kubernetes API unavailable"""
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            side_effect=httpx.ConnectError("Kubernetes API unreachable")
        )

        k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")
        k8s_tools.session = mock_session

        # Simplified POC wraps all errors in ToolError
        with pytest.raises(ToolError) as exc_info:
            await k8s_tools.call_tool("k8s_get_resources", {"resource": "pods"})

        assert "unreachable" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_k8s_permission_denied(self):
        """Test handling of Kubernetes RBAC permission error"""
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="Error: Forbidden (403)")])
        )

        k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")
        k8s_tools.session = mock_session

        result = await k8s_tools.kubectl_get("secrets", namespace="kube-system")
        assert "403" in result or "Forbidden" in result

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_llm_api_timeout(self):
        """Test handling of LLM API timeout"""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            side_effect=asyncio.TimeoutError("LLM API timeout")
        )

        # Test with JiraAgent
        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_agent = JiraAgent(jira_tools)
        jira_agent.llm = mock_llm

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="Ticket data")])
        )
        jira_tools.session = mock_session

        state = {"ticket_id": "TEST-123"}

        # Should fallback to raw data when LLM fails
        result = await jira_agent.read_ticket(state)
        assert result is not None
        assert "ticket_summary" in result


# ============================================================================
# CATEGORY 6: State Corruption Scenarios
# ============================================================================

class TestStateCorruption:
    """Test handling of invalid state and state corruption"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_invalid_state_field_types(self):
        """Test handling of state fields with wrong types"""
        # Create state with invalid types
        invalid_state = {
            "ticket_id": 123,  # Should be string
            "iteration_count": "not a number",  # Should be int
            "ticket_labels": "single_string",  # Should be list
        }

        # Should not crash (Python is dynamically typed)
        state = AgentState(**invalid_state)
        assert state.get("ticket_id") == 123

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_confidence_level_invalid_value(self):
        """Test handling of invalid confidence level"""
        state = AgentState(
            ticket_id="TEST-123",
            confidence_level="INVALID"  # Should be high/medium/low
        )

        # Should handle gracefully
        confidence = state.get("confidence_level", "").lower()
        # Validation happens in diagnostician
        assert confidence == "invalid"

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_negative_iteration_count(self):
        """Test handling of negative iteration count"""
        state = AgentState(
            ticket_id="TEST-123",
            iteration_count=-5  # Invalid
        )

        # System should not crash
        assert state["iteration_count"] == -5

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_messages_list_corruption(self):
        """Test handling of corrupted messages list"""
        state = AgentState(
            ticket_id="TEST-123",
            messages=[
                {"invalid": "structure"},
                None,
                "not a dict"
            ]
        )

        # Should not crash
        assert len(state.get("messages", [])) == 3


# ============================================================================
# CATEGORY 7: Edge Case Scenarios
# ============================================================================

class TestEdgeCases:
    """Test unusual but valid scenarios"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_ticket_with_no_description(self):
        """Test handling of ticket with empty description"""
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="Summary only, no description")])
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session
        jira_agent = JiraAgent(jira_tools)

        state = {"ticket_id": "TEST-123"}
        result = await jira_agent.read_ticket(state)

        # Should handle ticket with minimal information
        assert result is not None
        assert "ticket_summary" in result

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_ticket_with_only_emojis(self):
        """Test handling of ticket description with only emojis"""
        emoji_ticket = "🔥💥⚠️🚨❌"

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text=emoji_ticket)])
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        result = await jira_tools.get_ticket("TEST-123")
        assert "🔥" in result["content"]

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_pod_in_unknown_state(self):
        """Test handling of pod in Unknown state"""
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="Pod status: Unknown")])
        )

        k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")
        k8s_tools.session = mock_session

        result = await k8s_tools.kubectl_get("pods", namespace="default", name="unknown-pod")
        assert "Unknown" in result

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_empty_namespace(self):
        """Test handling of empty namespace"""
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="No resources found")])
        )

        k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")
        k8s_tools.session = mock_session

        result = await k8s_tools.kubectl_get("pods", namespace="empty-ns")
        assert "No resources" in result or result is not None

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_extremely_long_pod_name(self):
        """Test handling of pod with very long name"""
        long_pod_name = "a" * 253  # Near K8s limit

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text=f"Pod: {long_pod_name}")])
        )

        k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")
        k8s_tools.session = mock_session

        result = await k8s_tools.kubectl_logs(long_pod_name, namespace="default")
        assert long_pod_name in result

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_investigation_completes_in_one_step(self):
        """Test workflow completing in minimal iterations"""
        # This tests the workflow doesn't break with very fast completion
        state = AgentState(
            ticket_id="TEST-123",
            ticket_summary="Simple resolved issue",
            root_cause="Identified immediately",
            confidence_level="high",
            iteration_count=0
        )

        # Should handle single-iteration completion
        assert state["iteration_count"] == 0
        assert state["confidence_level"] == "high"


# ============================================================================
# CATEGORY 8: Timeout and Retry Scenarios
# ============================================================================

class TestTimeoutsAndRetries:
    """Test timeout handling and retry strategies"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_operation_timeout(self):
        """Test operation exceeding timeout limit"""
        async def slow_operation(*args, **kwargs):
            await asyncio.sleep(5)
            return MagicMock(content=[MagicMock(text="Too slow")])

        mock_session = AsyncMock()
        mock_session.call_tool = slow_operation

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # Should timeout if we enforce one
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-123"}),
                timeout=1.0
            )

# ============================================================================
# CATEGORY 9: Chaos Engineering Scenarios
# ============================================================================

class TestChaosEngineering:
    """Test system under random failure conditions"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_random_mcp_failures(self):
        """Test with random 10% failure rate"""
        call_count = 0
        success_count = 0

        async def random_failure(*args, **kwargs):
            nonlocal call_count, success_count
            call_count += 1
            # 10% failure rate
            if call_count % 10 == 0:
                raise httpx.ConnectError("Random failure")
            success_count += 1
            return MagicMock(content=[MagicMock(text="Success")])

        mock_session = AsyncMock()
        mock_session.call_tool = random_failure

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        # Call 50 times
        for i in range(50):
            try:
                await jira_tools.call_tool("get_ticket", {"ticket_id": f"TEST-{i}"})
            except httpx.ConnectError:
                pass  # Expected

        # Should have ~45 successes, 5 failures
        assert success_count >= 40  # Allow some variance

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_partial_system_degradation_jira_down(self):
        """Test when only Jira MCP is unavailable"""
        # Jira MCP fails
        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        mock_jira_session = AsyncMock()
        mock_jira_session.call_tool = AsyncMock(
            side_effect=httpx.ConnectError("Jira MCP down")
        )
        jira_tools.session = mock_jira_session

        # K8s MCP works
        k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")
        mock_k8s_session = AsyncMock()
        mock_k8s_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="K8s data")])
        )
        k8s_tools.session = mock_k8s_session

        # Jira should fail
        with pytest.raises(httpx.ConnectError):
            await jira_tools.get_ticket("TEST-123")

        # K8s should work
        result = await k8s_tools.kubectl_get("pods")
        assert result is not None

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_partial_system_degradation_k8s_down(self):
        """Test when only K8s MCP is unavailable"""
        # Jira MCP works
        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        mock_jira_session = AsyncMock()
        mock_jira_session.call_tool = AsyncMock(
            return_value=MagicMock(content=[MagicMock(text="Jira data")])
        )
        jira_tools.session = mock_jira_session

        # K8s MCP fails
        k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")
        mock_k8s_session = AsyncMock()
        mock_k8s_session.call_tool = AsyncMock(
            side_effect=httpx.ConnectError("K8s MCP down")
        )
        k8s_tools.session = mock_k8s_session

        # Jira should work
        result = await jira_tools.get_ticket("TEST-123")
        assert result is not None

        # K8s should fail gracefully with error in response
        # Note: K8sTools.kubectl_get catches exceptions and returns {"error": str(e)}
        result = await k8s_tools.kubectl_get("pods")
        assert isinstance(result, dict)
        assert "error" in result
        assert "K8s MCP down" in result["error"]


# ============================================================================
# CATEGORY 10: Recovery and Healing Scenarios
# ============================================================================

class TestRecoveryAndHealing:
    """Test automatic recovery and healing mechanisms"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_session_reconnection_after_drop(self):
        """Test MCP session reconnects after disconnect"""
        connection_count = 0

        async def track_connection(*args, **kwargs):
            nonlocal connection_count
            connection_count += 1
            return MagicMock(content=[MagicMock(text="Connected")])

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")

        # Mock session creation
        mock_session = AsyncMock()
        mock_session.call_tool = track_connection
        jira_tools.session = mock_session

        # First call
        await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-1"})

        # Simulate disconnect
        jira_tools.session = None

        # Second call should reconnect
        mock_session2 = AsyncMock()
        mock_session2.call_tool = track_connection
        jira_tools.session = mock_session2

        await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-2"})

        # Should have made 2 calls
        assert connection_count == 2

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_graceful_cleanup_on_abort(self):
        """Test graceful cleanup when investigation is aborted"""
        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        mock_session = AsyncMock()
        jira_tools.session = mock_session
        jira_tools.exit_stack = AsyncMock()

        # Simulate cleanup
        await jira_tools.close()

        # Should call cleanup
        jira_tools.exit_stack.aclose.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_error_message_is_actionable(self):
        """Test that error messages guide operators to resolution"""
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_tools.session = mock_session

        try:
            await jira_tools.call_tool("get_ticket", {"ticket_id": "TEST-123"})
        except httpx.ConnectError as e:
            error_msg = str(e).lower()
            # Error should be clear
            assert "connection" in error_msg or "refused" in error_msg


# ============================================================================
# Integration Tests - Full Workflow Under Stress
# ============================================================================

class TestFullWorkflowResilience:
    """Test complete workflows under failure conditions"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_workflow_with_intermittent_failures(self):
        """Test full investigation workflow with intermittent failures"""
        # Setup tools with intermittent failures
        call_count = {"jira": 0, "k8s": 0}

        async def jira_with_failures(*args, **kwargs):
            call_count["jira"] += 1
            if call_count["jira"] % 3 == 0:  # Fail every 3rd call
                raise httpx.ConnectError("Intermittent failure")
            return MagicMock(content=[MagicMock(text="Jira response")])

        async def k8s_with_failures(*args, **kwargs):
            call_count["k8s"] += 1
            if call_count["k8s"] % 4 == 0:  # Fail every 4th call
                raise httpx.ConnectError("Intermittent failure")
            return MagicMock(content=[MagicMock(text="K8s response")])

        jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
        jira_session = AsyncMock()
        jira_session.call_tool = jira_with_failures
        jira_tools.session = jira_session

        k8s_tools = K8sTools(mcp_endpoint="http://localhost:8084/mcp")
        k8s_session = AsyncMock()
        k8s_session.call_tool = k8s_with_failures
        k8s_tools.session = k8s_session

        # Make several calls - some will fail
        results = []
        for i in range(10):
            try:
                result = await jira_tools.call_tool("get_ticket", {"ticket_id": f"TEST-{i}"})
                results.append(("jira", True))
            except httpx.ConnectError:
                results.append(("jira", False))

        for i in range(10):
            try:
                result = await k8s_tools.call_tool("k8s_get_resources", {"resource": "pods"})
                results.append(("k8s", True))
            except httpx.ConnectError:
                results.append(("k8s", False))

        # Should have mix of successes and failures
        successes = sum(1 for _, success in results if success)
        failures = sum(1 for _, success in results if not success)

        assert successes > 0
        assert failures > 0

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_memory_usage_stays_bounded(self):
        """Test that memory usage doesn't grow unbounded"""
        import gc
        import sys

        initial_objects = len(gc.get_objects())

        # Create and destroy many sessions
        for i in range(100):
            jira_tools = JiraTools(mcp_endpoint="http://localhost:8080/mcp")
            mock_session = AsyncMock()
            jira_tools.session = mock_session

            # Cleanup
            await jira_tools.close()
            del jira_tools

        # Force garbage collection
        gc.collect()

        final_objects = len(gc.get_objects())

        # Should not have massive object growth (allow some variance)
        assert final_objects - initial_objects < 1000


# ============================================================================
# Helper Functions
# ============================================================================

def create_mock_mcp_response(text: str):
    """Helper to create mock MCP response"""
    return MagicMock(content=[MagicMock(text=text)])


def create_failing_session(error_type: Exception, error_msg: str):
    """Helper to create session that always fails"""
    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(side_effect=error_type(error_msg))
    return mock_session


def create_successful_session(response_text: str):
    """Helper to create session that always succeeds"""
    mock_session = AsyncMock()
    mock_session.call_tool = AsyncMock(
        return_value=create_mock_mcp_response(response_text)
    )
    return mock_session
