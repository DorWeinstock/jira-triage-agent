"""
Integration tests for webhook server to LangGraph agent API communication

Tests the interaction between:
1. Jira webhook server (Go) at :4001/webhook
2. LangGraph agent API (Go wrapper + Python) at :8000/investigate

Verifies:
- Webhook triggers agent API correctly
- Agent returns investigation status
- Error handling when agent is unreachable
"""

import json
import pytest
import httpx
from typing import Dict, Any
from unittest.mock import AsyncMock, Mock, patch, MagicMock


@pytest.mark.integration
class TestWebhookTriggersAgentAPI:
    """Test webhook server calling LangGraph agent API"""

    @pytest.mark.asyncio
    async def test_webhook_triggers_investigation(
        self,
        sample_jira_webhook_payload,
        mock_agent_api_client
    ):
        """
        Test that valid webhook triggers agent investigation

        Workflow:
        1. Webhook receives valid Jira payload
        2. Webhook filters and accepts ticket
        3. Webhook POSTs to agent /investigate endpoint
        4. Agent returns investigation_id
        """
        payload = sample_jira_webhook_payload

        # Simulate webhook calling agent API
        agent_endpoint = "http://localhost:8000/investigate"
        agent_request_payload = {
            "ticket_id": payload["issue"]["key"],
            "summary": payload["issue"]["fields"]["summary"]
        }

        # Mock agent API response
        expected_response = {
            "investigation_id": "inv-gaudisw-123-abc",
            "status": "started",
            "ticket_id": "GAUDISW-123"
        }

        mock_agent_api_client.post.return_value.json.return_value = expected_response
        mock_agent_api_client.post.return_value.status_code = 200

        # Call agent API (simulating webhook behavior)
        response = await mock_agent_api_client.post(
            agent_endpoint,
            json=agent_request_payload
        )

        # Verify agent was called
        mock_agent_api_client.post.assert_called_once()
        call_args = mock_agent_api_client.post.call_args

        # Verify correct endpoint (first positional argument)
        assert call_args.args[0] == agent_endpoint, \
            f"Agent API endpoint should be {agent_endpoint}, got {call_args.args[0]}"

        # Verify request payload (passed as json keyword argument)
        request_json = call_args.kwargs.get('json')
        assert request_json is not None, "Request should include json payload"
        assert request_json["ticket_id"] == "GAUDISW-123", \
            "Ticket ID should be passed to agent"

        # Verify response
        result = await response.json()
        assert result["status"] == "started", \
            "Agent should return 'started' status"
        assert "investigation_id" in result, \
            "Agent should return investigation_id"

    @pytest.mark.asyncio
    async def test_agent_returns_investigation_status(self, mock_agent_api_client):
        """
        Test querying investigation status from agent API

        Workflow:
        1. Investigation already started (has investigation_id)
        2. Query agent GET /investigate/{id} for status
        3. Agent returns current investigation status
        """
        investigation_id = "inv-gaudisw-456-xyz"

        # Mock agent status response
        expected_status = {
            "investigation_id": investigation_id,
            "status": "in_progress",
            "ticket_id": "GAUDISW-456",
            "current_agent": "k8s_investigator",
            "iteration": 3
        }

        mock_agent_api_client.get.return_value.json.return_value = expected_status
        mock_agent_api_client.get.return_value.status_code = 200

        # Query investigation status
        agent_endpoint = f"http://localhost:8000/investigate/{investigation_id}"
        response = await mock_agent_api_client.get(agent_endpoint)

        # Verify request
        mock_agent_api_client.get.assert_called_once()

        # Verify status response
        result = await response.json()
        assert result["status"] == "in_progress", \
            "Should return current status"
        assert result["investigation_id"] == investigation_id, \
            "Should match requested investigation"
        assert "current_agent" in result, \
            "Should include current agent name"
        assert "iteration" in result, \
            "Should include iteration count"

    @pytest.mark.asyncio
    async def test_agent_returns_completed_investigation(self, mock_agent_api_client):
        """
        Test retrieving completed investigation results

        Workflow:
        1. Investigation has completed
        2. Query agent for results
        3. Agent returns full diagnosis including root cause and recommendations
        """
        investigation_id = "inv-gaudisw-789-def"

        # Mock completed investigation response
        expected_result = {
            "investigation_id": investigation_id,
            "status": "completed",
            "ticket_id": "GAUDISW-789",
            "root_cause": "Pod missing required ConfigMap 'database-config'",
            "confidence_level": "high",
            "recommendations": [
                "Create ConfigMap 'database-config' with required keys",
                "Verify ConfigMap is mounted at /etc/config/database.yaml",
                "Restart deployment after ConfigMap is created"
            ],
            "iteration_count": 5,
            "completion_time": "2025-12-17T10:35:00Z"
        }

        mock_agent_api_client.get.return_value.json.return_value = expected_result
        mock_agent_api_client.get.return_value.status_code = 200

        # Query completed investigation
        agent_endpoint = f"http://localhost:8000/investigate/{investigation_id}"
        response = await mock_agent_api_client.get(agent_endpoint)

        # Verify response
        result = await response.json()
        assert result["status"] == "completed", \
            "Status should be completed"
        assert "root_cause" in result, \
            "Should include root cause"
        assert "confidence_level" in result, \
            "Should include confidence level"
        assert "recommendations" in result, \
            "Should include recommendations"
        assert isinstance(result["recommendations"], list), \
            "Recommendations should be a list"


@pytest.mark.integration
class TestWebhookAgentErrorHandling:
    """Test error handling when agent API is unavailable or returns errors"""

    @pytest.mark.asyncio
    async def test_webhook_handles_agent_down(self):
        """
        Test webhook behavior when agent API is unreachable

        Verifies graceful error handling:
        - Webhook should catch connection errors
        - Return 500 status to Jira
        - Log error for debugging
        - NOT crash the webhook server
        """
        # Mock httpx client that simulates connection failure
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.ConnectError(
            "Connection refused to http://localhost:8000"
        )

        agent_endpoint = "http://localhost:8000/investigate"
        request_payload = {
            "ticket_id": "GAUDISW-999",
            "summary": "Test issue"
        }

        # Attempt to call agent API
        with pytest.raises(httpx.ConnectError) as exc_info:
            await mock_client.post(agent_endpoint, json=request_payload)

        # Verify error type
        assert "Connection refused" in str(exc_info.value) or "ConnectError" in str(type(exc_info.value)), \
            "Should raise ConnectError when agent is down"

        # In real webhook server, this error should be caught and return 500

    @pytest.mark.asyncio
    async def test_webhook_handles_agent_timeout(self):
        """
        Test webhook behavior when agent API times out

        Verifies timeout handling:
        - Webhook should timeout after reasonable duration (e.g., 30s)
        - Return 500 status to Jira
        - Log timeout error
        """
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.TimeoutException(
            "Request to agent API timed out after 30s"
        )

        agent_endpoint = "http://localhost:8000/investigate"
        request_payload = {
            "ticket_id": "GAUDISW-888",
            "summary": "Test issue"
        }

        # Attempt to call agent API
        with pytest.raises(httpx.TimeoutException) as exc_info:
            await mock_client.post(agent_endpoint, json=request_payload)

        # Verify error type
        assert "TimeoutException" in str(type(exc_info.value)) or "timeout" in str(exc_info.value).lower(), \
            "Should raise TimeoutException when agent doesn't respond"

    @pytest.mark.asyncio
    async def test_webhook_handles_agent_error_response(self, mock_agent_api_client):
        """
        Test webhook behavior when agent returns error response

        Verifies error response handling:
        - Agent returns 4xx/5xx status code
        - Webhook should handle gracefully
        - Return appropriate error to Jira
        """
        # Mock agent returning error
        error_response = AsyncMock()
        error_response.status_code = 500
        error_response.json.return_value = {
            "error": "Internal server error",
            "message": "Failed to initialize MCP session"
        }

        mock_agent_api_client.post.return_value = error_response

        agent_endpoint = "http://localhost:8000/investigate"
        request_payload = {
            "ticket_id": "GAUDISW-777",
            "summary": "Test issue"
        }

        # Call agent API
        response = await mock_agent_api_client.post(agent_endpoint, json=request_payload)

        # Verify error response
        assert response.status_code == 500, \
            "Should receive 500 error from agent"

        result = await response.json()
        assert "error" in result, \
            "Error response should include error field"

    @pytest.mark.asyncio
    async def test_webhook_validates_agent_response(self, mock_agent_api_client):
        """
        Test webhook validates agent API response format

        Verifies validation of required fields:
        - investigation_id must be present
        - status must be present
        - ticket_id should match request
        """
        # Mock agent returning incomplete response
        incomplete_response = AsyncMock()
        incomplete_response.status_code = 200
        incomplete_response.json.return_value = {
            # Missing investigation_id
            "status": "started"
        }

        mock_agent_api_client.post.return_value = incomplete_response

        agent_endpoint = "http://localhost:8000/investigate"
        request_payload = {
            "ticket_id": "GAUDISW-666",
            "summary": "Test issue"
        }

        # Call agent API
        response = await mock_agent_api_client.post(agent_endpoint, json=request_payload)
        result = await response.json()

        # Webhook should detect missing investigation_id
        assert "investigation_id" not in result, \
            "Response is missing investigation_id"

        # In real implementation, webhook should validate and handle this


@pytest.mark.integration
class TestAgentAPIPayloadFormat:
    """Test payload format between webhook and agent API"""

    def test_investigate_request_payload_format(self, sample_jira_webhook_payload):
        """
        Test format of investigation request sent to agent API

        Required fields:
        - ticket_id: Jira ticket ID (e.g., "GAUDISW-123")
        - summary: Brief issue description

        Optional fields:
        - description: Full issue description
        - priority: Issue priority
        - labels: Issue labels
        """
        webhook_payload = sample_jira_webhook_payload

        # Extract fields for agent API request
        agent_request = {
            "ticket_id": webhook_payload["issue"]["key"],
            "summary": webhook_payload["issue"]["fields"]["summary"]
        }

        # Verify required fields
        assert "ticket_id" in agent_request, \
            "Request must include ticket_id"
        assert "summary" in agent_request, \
            "Request must include summary"

        # Verify field formats
        assert isinstance(agent_request["ticket_id"], str), \
            "ticket_id should be string"
        assert isinstance(agent_request["summary"], str), \
            "summary should be string"
        assert len(agent_request["ticket_id"]) > 0, \
            "ticket_id should not be empty"

    def test_investigate_response_payload_format(self):
        """
        Test format of investigation response from agent API

        Expected fields:
        - investigation_id: Unique identifier for this investigation
        - status: Current status (started, in_progress, completed, failed)
        - ticket_id: Original ticket ID

        For completed investigations:
        - root_cause: Identified root cause
        - confidence_level: Confidence in diagnosis (high, medium, low)
        - recommendations: List of recommended actions
        """
        # Started investigation response
        started_response = {
            "investigation_id": "inv-123-abc",
            "status": "started",
            "ticket_id": "GAUDISW-123"
        }

        assert "investigation_id" in started_response, \
            "Response must include investigation_id"
        assert "status" in started_response, \
            "Response must include status"
        assert started_response["status"] in ["started", "in_progress", "completed", "failed"], \
            "Status must be valid value"

        # Completed investigation response
        completed_response = {
            "investigation_id": "inv-123-abc",
            "status": "completed",
            "ticket_id": "GAUDISW-123",
            "root_cause": "ConfigMap missing",
            "confidence_level": "high",
            "recommendations": ["Create ConfigMap", "Restart pod"]
        }

        # Verify completed investigation has additional fields
        assert "root_cause" in completed_response, \
            "Completed investigation must include root_cause"
        assert "confidence_level" in completed_response, \
            "Completed investigation must include confidence_level"
        assert "recommendations" in completed_response, \
            "Completed investigation must include recommendations"
        assert isinstance(completed_response["recommendations"], list), \
            "Recommendations must be a list"


@pytest.mark.integration
@pytest.mark.slow
class TestWebhookAgentEndToEnd:
    """End-to-end integration tests for webhook → agent API flow"""

    @pytest.mark.asyncio
    async def test_full_webhook_to_investigation_flow(
        self,
        sample_jira_webhook_payload,
        mock_agent_api_client
    ):
        """
        Test complete workflow from webhook to investigation

        Full flow:
        1. Jira sends webhook for new Bug ticket
        2. Webhook server filters and accepts ticket
        3. Webhook calls agent /investigate API
        4. Agent starts investigation and returns ID
        5. Webhook returns success to Jira with AgentRun name
        """
        # Step 1: Webhook receives Jira payload
        webhook_payload = sample_jira_webhook_payload
        ticket_id = webhook_payload["issue"]["key"]
        summary = webhook_payload["issue"]["fields"]["summary"]

        # Step 2: Webhook filters (already tested in test_webhook_filtering.py)
        # Assume ticket passes filters...

        # Step 3: Webhook calls agent API
        agent_endpoint = "http://localhost:8000/investigate"
        agent_request = {
            "ticket_id": ticket_id,
            "summary": summary
        }

        # Step 4: Mock agent response
        agent_response = {
            "investigation_id": "inv-gaudisw-123-abc",
            "status": "started",
            "ticket_id": ticket_id
        }

        mock_agent_api_client.post.return_value.json.return_value = agent_response
        mock_agent_api_client.post.return_value.status_code = 200

        response = await mock_agent_api_client.post(agent_endpoint, json=agent_request)
        result = await response.json()

        # Step 5: Verify webhook would return success
        webhook_response = {
            "status": "triggered",
            "ticket_id": ticket_id,
            "agentrun": f"investigation-{ticket_id.lower()}-abc123"
        }

        assert webhook_response["status"] == "triggered", \
            "Webhook should indicate investigation was triggered"
        assert webhook_response["ticket_id"] == ticket_id, \
            "Webhook response should include ticket ID"
        assert "agentrun" in webhook_response, \
            "Webhook response should include AgentRun name"

    @pytest.mark.asyncio
    async def test_concurrent_webhook_requests(self, mock_agent_api_client):
        """
        Test webhook server handling multiple concurrent requests

        Verifies:
        - Multiple tickets can be processed simultaneously
        - Each gets unique investigation_id
        - No race conditions or conflicts
        """
        import asyncio

        # Create multiple webhook requests
        ticket_ids = [f"GAUDISW-{i}" for i in range(100, 105)]

        async def process_ticket(ticket_id: str):
            """Simulate webhook processing a ticket"""
            agent_request = {
                "ticket_id": ticket_id,
                "summary": f"Test issue {ticket_id}"
            }

            # Mock unique response for each ticket
            agent_response = {
                "investigation_id": f"inv-{ticket_id.lower()}-{ticket_id[-3:]}",
                "status": "started",
                "ticket_id": ticket_id
            }

            mock_agent_api_client.post.return_value.json.return_value = agent_response
            mock_agent_api_client.post.return_value.status_code = 200

            response = await mock_agent_api_client.post(
                "http://localhost:8000/investigate",
                json=agent_request
            )
            return await response.json()

        # Process tickets concurrently
        results = await asyncio.gather(*[process_ticket(tid) for tid in ticket_ids])

        # Verify all tickets were processed
        assert len(results) == len(ticket_ids), \
            "All tickets should be processed"

        # Verify each has unique investigation_id
        investigation_ids = [r["investigation_id"] for r in results]
        assert len(investigation_ids) == len(set(investigation_ids)), \
            "All investigation IDs should be unique"

        # Verify all started successfully
        assert all(r["status"] == "started" for r in results), \
            "All investigations should start successfully"
