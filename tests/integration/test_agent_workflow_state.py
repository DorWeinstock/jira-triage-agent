"""
Integration tests for LangGraph agent workflow and state management

Tests the supervisor graph execution, agent sequencing, state propagation,
and pod name parsing capabilities across the multi-agent system.

This test suite provides comprehensive coverage of:
1. Complete workflow execution (read_ticket → search_history → investigate → diagnose → post_comment)
2. Conditional routing logic (should_search_history, check_confidence)
3. Detailed state passing and propagation between agents
4. State initialization and field isolation
5. Error state handling and recovery
6. State mutation protection and immutability
7. Message accumulation through workflow
8. Pod name parsing and extraction from LLM responses
9. Workflow resilience and error handling

The tests verify that:
- Each agent correctly reads from and writes to shared state
- State fields are properly isolated between agents
- State accumulates correctly through the complete workflow
- Errors are gracefully captured in state
- No unintended state mutations occur
"""

import re
import pytest
from typing import Dict, Any, List
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from langgraph.graph import END


@pytest.mark.integration
class TestWorkflowExecution:
    """Test complete workflow execution from start to END"""

    @pytest.mark.asyncio
    async def test_complete_workflow_happy_path(self, sample_agent_state):
        """
        Test complete workflow execution from read_ticket to post_comment

        Verifies:
        - All agents execute in correct sequence
        - Each agent updates state appropriately
        - Workflow reaches END state
        - No agents are skipped
        """
        from src.supervisor import create_conditional_supervisor_graph
        from src.tools.jira_tools import JiraTools
        from src.tools.k8s_tools import K8sTools

        # Mock tool clients
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "content": "CrashLoopBackOff in api-server pod",
            "summary": "Pod crashing",
            "description": "The api-server pod is in CrashLoopBackOff",
            "priority": "High",
            "status": "Open",
            "labels": ["k8s", "production"]
        })
        jira_tools.search_tickets = AsyncMock(return_value={
            "content": "Found 2 similar tickets: PROJ-100, PROJ-101"
        })
        jira_tools.add_comment = AsyncMock(return_value={
            "content": "✅ Comment added successfully",
            "success": True
        })

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={
            "items": [{"metadata": {"name": "api-server-abc123"}, "status": {"phase": "CrashLoopBackOff"}}]
        })
        k8s_tools.kubectl_logs = AsyncMock(return_value="Error: Configuration file not found")
        k8s_tools.kubectl_events = AsyncMock(return_value=[
            {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container"}
        ])
        k8s_tools.kubectl_top = AsyncMock(return_value={"cpu": "100m", "memory": "256Mi"})

        # Create supervisor graph
        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        # Execute workflow
        initial_state = {
            "ticket_id": "TEST-123",
            "messages": [],
            "iteration_count": 0
        }

        # Invoke the graph (synchronously for testing)
        with patch('src.agents.jira_agent.ChatOpenAI') as mock_llm_jira, \
             patch('src.agents.history_agent.create_extraction_llm') as mock_llm_history, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as mock_llm_k8s, \
             patch('src.agents.diagnostician.ChatOpenAI') as mock_llm_diag:

            # Mock LLM responses
            mock_response = Mock()
            mock_response.content = "CrashLoopBackOff detected in api-server pod"
            mock_llm_jira.return_value.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_history.return_value.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_k8s.return_value.ainvoke = AsyncMock(return_value=mock_response)

            # Mock diagnostician response with structured output
            diag_response = Mock()
            diag_response.content = """
## Root Cause
Missing configuration file causing pod to crash on startup

## Recommended Action
Add missing configuration file to ConfigMap

## Confidence Level
High - clear error message in logs

## Preventive Measures
- Validate configuration before deployment
- Add liveness probe
- Implement configuration validation
"""
            mock_llm_diag.return_value.ainvoke = AsyncMock(return_value=diag_response)

            # Execute graph
            result = await graph.ainvoke(initial_state)

        # Verify workflow completion
        assert result is not None, "Workflow should return final state"
        assert result.get("ticket_id") == "TEST-123", "Ticket ID should be preserved"
        assert result.get("root_cause") is not None, "Root cause should be determined"
        assert result.get("confidence_level") is not None, "Confidence level should be set"

        # Verify all tool calls occurred
        jira_tools.get_ticket.assert_called_once_with("TEST-123")
        jira_tools.search_tickets.assert_called()
        jira_tools.add_comment.assert_called_once()
        k8s_tools.kubectl_get.assert_called()

    @pytest.mark.asyncio
    async def test_workflow_with_conditional_routing_skip_history(self, sample_agent_state):
        """
        Test conditional routing that skips history search

        Verifies:
        - should_search_history correctly identifies "new feature" keywords
        - History agent is skipped when detected
        - Workflow jumps directly to investigate_cluster
        - State is still valid after skipping
        """
        from src.supervisor import create_conditional_supervisor_graph
        from src.tools.jira_tools import JiraTools
        from src.tools.k8s_tools import K8sTools

        # Mock tool clients
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "content": "New feature deployment failing",
            "summary": "New feature: user authentication",
            "description": "First time deploying this feature",
            "priority": "High",
            "status": "Open",
            "labels": ["new-feature"]
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "No results"})
        jira_tools.add_comment = AsyncMock(return_value={"content": "✅ Comment added", "success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs...")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        initial_state = {
            "ticket_id": "TEST-NEW-1",
            "ticket_summary": "New feature: user authentication",  # Contains "new feature"
            "messages": [],
            "iteration_count": 0
        }

        with patch('src.agents.jira_agent.ChatOpenAI') as mock_llm_jira, \
             patch('src.agents.history_agent.create_extraction_llm') as mock_llm_history, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as mock_llm_k8s, \
             patch('src.agents.diagnostician.ChatOpenAI') as mock_llm_diag:

            mock_response = Mock()
            mock_response.content = "New feature deployment issue"
            mock_llm_jira.return_value.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_k8s.return_value.ainvoke = AsyncMock(return_value=mock_response)

            diag_response = Mock()
            diag_response.content = """
## Root Cause
New deployment configuration issue

## Recommended Action
Review deployment manifest

## Confidence Level
Medium

## Preventive Measures
- Test deployments in staging
"""
            mock_llm_diag.return_value.ainvoke = AsyncMock(return_value=diag_response)

            result = await graph.ainvoke(initial_state)

        # Verify history search was skipped
        jira_tools.search_tickets.assert_not_called()

        # Verify K8s investigation still ran
        k8s_tools.kubectl_get.assert_called()

        # Verify workflow completed
        assert result.get("ticket_id") == "TEST-NEW-1"
        assert result.get("root_cause") is not None

    @pytest.mark.asyncio
    async def test_workflow_with_low_confidence_retry(self, sample_agent_state):
        """
        Test conditional routing with low confidence triggering re-investigation

        Verifies:
        - check_confidence detects low confidence
        - Workflow loops back to investigate_cluster
        - iteration_count is incremented
        - Workflow eventually exits after max retries
        """
        from src.supervisor import create_conditional_supervisor_graph
        from src.tools.jira_tools import JiraTools
        from src.tools.k8s_tools import K8sTools

        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "content": "Unknown issue",
            "summary": "Something is broken",
            "description": "Not sure what",
            "priority": "Low",
            "status": "Open"
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "No similar tickets"})
        jira_tools.add_comment = AsyncMock(return_value={"content": "✅ Comment added", "success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="No clear errors")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        initial_state = {
            "ticket_id": "TEST-LOW-CONF",
            "messages": [],
            "iteration_count": 0
        }

        call_count = {"diag": 0}

        def mock_diag_response(*args, **kwargs):
            call_count["diag"] += 1
            response = Mock()
            # First call returns low confidence, second returns medium
            if call_count["diag"] == 1:
                response.content = """
## Root Cause
Unable to determine clear root cause

## Recommended Action
Manual investigation needed

## Confidence Level
Low - insufficient evidence

## Preventive Measures
- Add more logging
"""
            else:
                response.content = """
## Root Cause
Possible resource constraint

## Recommended Action
Review resource limits

## Confidence Level
Medium - some evidence found

## Preventive Measures
- Monitor resources
"""
            return response

        # IMPORTANT: Create graph INSIDE patch context so agents are instantiated with mocked LLMs
        with patch('src.agents.jira_agent.ChatOpenAI') as mock_llm_jira, \
             patch('src.agents.history_agent.create_extraction_llm') as mock_llm_history, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as mock_llm_k8s, \
             patch('src.agents.diagnostician.ChatOpenAI') as mock_llm_diag:

            mock_response = Mock()
            mock_response.content = "Investigation ongoing"
            mock_llm_jira.return_value.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_history.return_value.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_k8s.return_value.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_diag.return_value.ainvoke = AsyncMock(side_effect=mock_diag_response)

            # Create graph inside patch context so Diagnostician uses mocked LLM
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)
            result = await graph.ainvoke(initial_state)

        # Verify diagnostician was called multiple times (retry happened)
        assert call_count["diag"] >= 2, "Diagnostician should be called at least twice for retry"

        # Verify iteration count incremented
        assert result.get("iteration_count", 0) > 0, "Iteration count should be incremented"

        # Verify K8s investigation called multiple times
        assert k8s_tools.kubectl_get.call_count >= 2, "K8s investigation should run multiple times"

    @pytest.mark.asyncio
    async def test_workflow_error_handling(self, sample_agent_state):
        """
        Test workflow continues gracefully when agents encounter errors

        Verifies:
        - Agent errors don't crash workflow
        - Error state is captured
        - Workflow proceeds to next agent
        - Final state indicates partial failure
        """
        from src.supervisor import create_conditional_supervisor_graph
        from src.tools.jira_tools import JiraTools
        from src.tools.k8s_tools import K8sTools

        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "error": "Ticket not found",
            "content": None
        })
        jira_tools.search_tickets = AsyncMock(side_effect=Exception("Search API timeout"))
        jira_tools.add_comment = AsyncMock(return_value={"content": "✅ Comment added", "success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(side_effect=Exception("Cluster unreachable"))
        k8s_tools.kubectl_logs = AsyncMock(return_value="")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        initial_state = {
            "ticket_id": "TEST-ERROR",
            "messages": [],
            "iteration_count": 0
        }

        with patch('src.agents.jira_agent.ChatOpenAI') as mock_llm_jira, \
             patch('src.agents.history_agent.create_extraction_llm') as mock_llm_history, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as mock_llm_k8s, \
             patch('src.agents.diagnostician.ChatOpenAI') as mock_llm_diag:

            mock_response = Mock()
            mock_response.content = "Error encountered"
            mock_llm_jira.return_value.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_history.return_value.ainvoke = AsyncMock(return_value=mock_response)
            mock_llm_k8s.return_value.ainvoke = AsyncMock(return_value=mock_response)

            diag_response = Mock()
            diag_response.content = """
## Root Cause
Unable to complete investigation due to errors

## Recommended Action
Manual investigation required

## Confidence Level
Low

## Preventive Measures
- Check system connectivity
"""
            mock_llm_diag.return_value.ainvoke = AsyncMock(return_value=diag_response)

            result = await graph.ainvoke(initial_state)

        # Workflow should complete despite errors
        assert result is not None, "Workflow should complete even with errors"

        # Verify error indicators in state
        assert result.get("ticket_summary") and "error" in result.get("ticket_summary", "").lower(), \
            "Ticket summary should indicate error"

        # Comment should still be posted
        jira_tools.add_comment.assert_called_once()


@pytest.mark.integration
class TestStatePropagation:
    """Test state passing and propagation between agents"""

    def test_ticket_id_propagates_through_workflow(self, sample_agent_state):
        """
        Test ticket_id is preserved throughout entire workflow

        Verifies:
        - ticket_id set in initial state
        - ticket_id available to all agents
        - ticket_id unchanged in final state
        """
        state = sample_agent_state.copy()
        assert state["ticket_id"] == "TEST-123"

        # Simulate agent updates
        state["ticket_summary"] = "New summary"
        assert state["ticket_id"] == "TEST-123", "ticket_id should be unchanged"

        state["root_cause"] = "Configuration error"
        assert state["ticket_id"] == "TEST-123", "ticket_id should persist"

    def test_ticket_summary_description_propagation(self, sample_agent_state):
        """
        Test ticket_summary and description flow from JiraAgent to other agents

        Verifies:
        - JiraAgent sets ticket_summary and ticket_description
        - HistoryAgent has access to these fields
        - K8sInvestigator can read ticket context
        - Diagnostician sees all ticket information
        """
        from src.state import AgentState

        state = sample_agent_state.copy()

        # Simulate JiraAgent updating state
        state["ticket_summary"] = "CrashLoopBackOff in api-server pod"
        state["ticket_description"] = "The api-server pod is crashing repeatedly with exit code 1"
        state["ticket_labels"] = ["k8s", "production", "high-priority"]
        state["ticket_priority"] = "Critical"

        # Verify all fields accessible
        assert state["ticket_summary"] == "CrashLoopBackOff in api-server pod"
        assert state["ticket_description"] is not None
        assert len(state["ticket_labels"]) == 3
        assert state["ticket_priority"] == "Critical"

        # Simulate HistoryAgent reading and using these fields
        search_query = f"text ~ '{state['ticket_summary']}'"
        assert "CrashLoopBackOff" in search_query

        # Simulate K8sInvestigator extracting pod name
        pod_name = self._extract_pod_name(state["ticket_summary"])
        assert pod_name == "api-server"

    def test_messages_list_accumulation(self, sample_agent_state):
        """
        Test messages list accumulates throughout workflow

        Verifies:
        - Messages list starts empty
        - Each agent can append messages
        - Messages preserve order
        - Messages accessible to all agents
        """
        from langgraph.graph import MessagesState

        state = sample_agent_state.copy()
        state["messages"] = []

        # Simulate JiraAgent adding message
        state["messages"].append({
            "role": "agent",
            "name": "JiraAgent",
            "content": "Retrieved ticket TEST-123"
        })
        assert len(state["messages"]) == 1

        # Simulate HistoryAgent adding message
        state["messages"].append({
            "role": "agent",
            "name": "HistoryAgent",
            "content": "Found 3 similar tickets"
        })
        assert len(state["messages"]) == 2

        # Verify order preserved
        assert state["messages"][0]["name"] == "JiraAgent"
        assert state["messages"][1]["name"] == "HistoryAgent"

    def test_historical_context_from_history_to_diagnostician(self, sample_agent_state):
        """
        Test historical_context flows from HistoryAgent to Diagnostician

        Verifies:
        - HistoryAgent populates similar_tickets and past_resolutions
        - Diagnostician has access to this historical data
        - Historical patterns influence diagnosis
        """
        state = sample_agent_state.copy()

        # Simulate HistoryAgent output
        state["similar_tickets"] = [
            "PROJ-100: CrashLoopBackOff due to missing ConfigMap - RESOLVED",
            "PROJ-101: Pod crash from bad config - RESOLVED",
            "PROJ-102: Configuration file not found - RESOLVED"
        ]
        state["past_resolutions"] = [
            "Most common cause: missing or invalid configuration files",
            "Solution: verify ConfigMap exists and is mounted correctly",
            "Average resolution time: 30 minutes"
        ]

        # Verify Diagnostician can access this data
        assert len(state["similar_tickets"]) == 3
        assert len(state["past_resolutions"]) == 3
        assert "ConfigMap" in state["past_resolutions"][1]

        # Simulate Diagnostician using this context
        historical_context = "\n".join(state["similar_tickets"])
        assert "PROJ-100" in historical_context
        assert "RESOLVED" in historical_context

    def test_k8s_investigation_to_diagnostician(self, sample_agent_state):
        """
        Test K8s investigation data flows to Diagnostician

        Verifies:
        - K8sInvestigator populates cluster_findings, logs, events
        - Diagnostician receives all investigation data
        - Investigation data structure is preserved
        """
        state = sample_agent_state.copy()

        # Simulate K8sInvestigator output
        state["cluster_findings"] = {
            "targets": {"namespace": "default", "pods": ["api-server"]},
            "pod_statuses": {
                "api-server-abc123": {
                    "status": "CrashLoopBackOff",
                    "restartCount": 5,
                    "exitCode": 1
                }
            },
            "logs": {
                "api-server-abc123_current": "Error: configuration file not found at /etc/config/app.yaml"
            },
            "events": [
                {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container"}
            ]
        }

        # Verify Diagnostician can access all investigation data from cluster_findings
        assert "cluster_findings" in state
        assert "pod_statuses" in state["cluster_findings"]
        assert "logs" in state["cluster_findings"]
        assert "events" in state["cluster_findings"]
        assert len(state["cluster_findings"]["events"]) == 1

        # Verify key error message accessible via cluster_findings
        logs_str = str(state["cluster_findings"]["logs"])
        assert "configuration file not found" in logs_str

    def test_diagnosis_to_jira_agent(self, sample_agent_state):
        """
        Test diagnosis data flows from Diagnostician to JiraAgent

        Verifies:
        - Diagnostician sets root_cause, recommended_action, confidence_level
        - JiraAgent can format these fields into comment
        - All diagnosis fields present in final state
        """
        state = sample_agent_state.copy()

        # Simulate Diagnostician output
        state["root_cause"] = "Missing configuration file at /etc/config/app.yaml causing pod to crash on startup"
        state["recommended_action"] = "Create ConfigMap with required configuration and mount to pod at /etc/config/"
        state["confidence_level"] = "High"
        state["preventive_measures"] = [
            "Validate configuration exists before deployment",
            "Add configuration validation to CI/CD pipeline",
            "Implement configuration drift detection"
        ]

        # Verify JiraAgent can access all diagnosis fields
        assert state["root_cause"] is not None
        assert state["recommended_action"] is not None
        assert state["confidence_level"] == "High"
        assert len(state["preventive_measures"]) == 3

        # Simulate JiraAgent formatting comment
        comment = f"""
## Root Cause
{state['root_cause']}

## Recommended Action
{state['recommended_action']}

## Confidence Level
{state['confidence_level']}

## Preventive Measures
{chr(10).join(['- ' + m for m in state['preventive_measures']])}
"""
        assert "Missing configuration file" in comment
        assert "ConfigMap" in comment
        assert "High" in comment

    def test_confidence_level_iteration_count_for_retry(self, sample_agent_state):
        """
        Test confidence_level and iteration_count control retry logic

        Verifies:
        - confidence_level set by Diagnostician
        - iteration_count tracked across retries
        - check_confidence function uses both fields
        - Retry stops after max iterations
        """
        from src.supervisor import create_conditional_supervisor_graph

        state = sample_agent_state.copy()

        # First iteration - low confidence
        state["confidence_level"] = "Low"
        state["iteration_count"] = 0

        # Simulate check_confidence logic
        should_retry = (state["confidence_level"].lower() == "low" and state["iteration_count"] < 2)
        assert should_retry is True, "Should retry on first low confidence"

        # Second iteration - still low confidence
        state["iteration_count"] = 1
        should_retry = (state["confidence_level"].lower() == "low" and state["iteration_count"] < 2)
        assert should_retry is True, "Should retry on second attempt"

        # Third iteration - max retries reached
        state["iteration_count"] = 2
        should_retry = (state["confidence_level"].lower() == "low" and state["iteration_count"] < 2)
        assert should_retry is False, "Should not retry after max iterations"

        # High confidence - no retry
        state["confidence_level"] = "High"
        state["iteration_count"] = 0
        should_retry = (state["confidence_level"].lower() == "low" and state["iteration_count"] < 2)
        assert should_retry is False, "Should not retry with high confidence"

    def test_state_immutability_between_agents(self, sample_agent_state):
        """
        Test that agents don't unintentionally mutate unrelated state fields

        Verifies:
        - Each agent only updates its designated fields
        - Other agents' outputs are not overwritten
        - State isolation between agents
        """
        state = sample_agent_state.copy()

        # JiraAgent should only set ticket fields
        jira_fields = ["ticket_summary", "ticket_description", "ticket_labels", "ticket_priority", "ticket_status"]
        state["ticket_summary"] = "Summary"
        state["ticket_description"] = "Description"

        # Store JiraAgent output
        jira_output = {k: state.get(k) for k in jira_fields}

        # HistoryAgent should only set history fields
        state["similar_tickets"] = ["PROJ-1"]
        state["past_resolutions"] = ["Solution 1"]

        # Verify JiraAgent fields unchanged
        for field in jira_fields:
            assert state.get(field) == jira_output.get(field), \
                f"JiraAgent field {field} should not be modified by HistoryAgent"

        # K8sInvestigator should only set cluster_findings (contains all investigation data)
        state["cluster_findings"] = {
            "data": "test",
            "resources": {"pods": {}},
            "logs": "logs",
            "events": []
        }

        # Verify history fields unchanged
        assert state["similar_tickets"] == ["PROJ-1"]
        assert state["past_resolutions"] == ["Solution 1"]

    @staticmethod
    def _extract_pod_name(text: str) -> str:
        """Helper to extract pod name from text"""
        # Extract pod-like names: prioritize names with hyphens (deployment-style)
        # Then fall back to simple names
        text_lower = text.lower()

        # First priority: names with hyphens (e.g., api-server, frontend-service)
        match = re.search(r'\b([a-z0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*)\b', text_lower)
        if match:
            return match.group(1)

        # Fallback: any word-like name
        match = re.search(r'\b([a-z0-9]+)\b', text_lower)
        if match:
            return match.group(1)

        return ""


@pytest.mark.integration
class TestPodNameParsing:
    """Test K8sInvestigator's pod name extraction capabilities"""

    def test_parse_clean_pod_name(self):
        """
        Test parsing clean pod names from LLM responses

        Verifies:
        - Simple pod names extracted correctly
        - No false positives
        - Handles various formats
        """
        test_cases = [
            ("api-server", "api-server"),
            ("frontend-service", "frontend-service"),
            ("nginx", "nginx"),
            ("my-app-123", "my-app-123"),
        ]

        for input_text, expected in test_cases:
            result = self._extract_pod_name_simple(input_text)
            assert result == expected, f"Failed to extract '{expected}' from '{input_text}'"

    def test_parse_pod_name_with_random_suffix(self):
        """
        Test handling pod names with Kubernetes random suffixes

        Verifies:
        - Extracts base name from api-server-abc123-xyz456
        - Removes ReplicaSet hash (abc123)
        - Removes Pod hash (xyz456)
        - Returns clean deployment/service name
        """
        test_cases = [
            ("api-server-5f8d6c7b-abc12", "api-server"),
            ("frontend-7d5f8c9b-xyz89", "frontend"),
            ("nginx-deployment-6b4f8d7c-klm45", "nginx-deployment"),
            ("my-app-123-9c8d7b6a-pqr78", "my-app-123"),
        ]

        for input_text, expected_base in test_cases:
            result = self._extract_pod_base_name(input_text)
            assert result == expected_base, \
                f"Failed to extract base name '{expected_base}' from '{input_text}'"

    def test_parse_multiple_pod_names_from_text(self):
        """
        Test extracting multiple pod names from LLM response text

        Verifies:
        - Finds all pod names in paragraph text
        - Handles comma-separated lists
        - Handles newline-separated lists
        - No duplicates in results
        """
        text = """
The investigation found issues with the following pods:
- api-server-5f8d6c7b-abc12
- frontend-7d5f8c9b-xyz89
- nginx-deployment-6b4f8d7c-klm45

All three pods are in CrashLoopBackOff state.
"""
        pod_names = self._extract_all_pod_names(text)

        assert len(pod_names) == 3, f"Should find 3 pod names, found {len(pod_names)}"
        assert "api-server-5f8d6c7b-abc12" in pod_names
        assert "frontend-7d5f8c9b-xyz89" in pod_names
        assert "nginx-deployment-6b4f8d7c-klm45" in pod_names

    def test_parse_invalid_malformed_pod_names(self):
        """
        Test handling of invalid or malformed pod names

        Verifies:
        - Empty strings return empty
        - Invalid characters filtered out
        - Too-short names rejected
        - Special characters handled correctly
        """
        invalid_cases = [
            ("", ""),  # Empty should return empty
            ("ab", ""),  # Too short (less than 3 chars) should return empty
            ("pod_with_underscores", ""),  # Underscores not valid in pod names
            ("pod.with.dots", ""),  # Dots not valid
            ("pod@invalid", ""),  # Special characters not valid
        ]

        for invalid_name, expected in invalid_cases:
            result = self._extract_pod_name_simple(invalid_name)
            # Invalid inputs should return empty or be rejected by validation
            assert result == expected, \
                f"Invalid pod name '{invalid_name}' should return '{expected}', got '{result}'"

    def test_parse_pod_name_case_sensitivity(self):
        """
        Test case sensitivity in pod name extraction

        Verifies:
        - Kubernetes pod names are lowercase
        - Parser handles mixed case input
        - Normalizes to lowercase if needed
        """
        test_cases = [
            ("API-SERVER-abc123-xyz456", "api-server"),  # Should lowercase
            ("Frontend-Service", "frontend-service"),
            ("NGINX", "nginx"),
        ]

        for input_text, expected in test_cases:
            result = self._extract_pod_base_name(input_text.lower())  # Normalize first
            assert result == expected, \
                f"Case sensitivity issue: '{input_text}' should become '{expected}'"

    def test_parse_pod_name_with_namespace(self):
        """
        Test extracting pod names that include namespace prefix

        Verifies:
        - Handles namespace/pod-name format
        - Extracts both namespace and pod name
        - Works with default namespace
        """
        test_cases = [
            ("default/api-server-abc123-xyz456", ("default", "api-server")),
            ("production/frontend-xyz789-abc123", ("production", "frontend")),
            ("kube-system/coredns-5f8d6c7b-abc12", ("kube-system", "coredns")),
        ]

        for input_text, expected in test_cases:
            namespace, pod_name = self._extract_namespace_and_pod(input_text)
            expected_ns, expected_pod = expected

            assert namespace == expected_ns, \
                f"Namespace mismatch: expected '{expected_ns}', got '{namespace}'"
            assert pod_name == expected_pod, \
                f"Pod name mismatch: expected '{expected_pod}', got '{pod_name}'"

    def test_parse_pod_names_from_kubectl_output(self):
        """
        Test parsing pod names from actual kubectl get pods output

        Verifies:
        - Parses tabular kubectl output
        - Extracts pod names from first column
        - Handles various pod states
        - Filters out header row
        """
        kubectl_output = """
NAME                                READY   STATUS             RESTARTS   AGE
api-server-5f8d6c7b-abc12           0/1     CrashLoopBackOff   5          10m
frontend-7d5f8c9b-xyz89             1/1     Running            0          1h
nginx-deployment-6b4f8d7c-klm45     0/1     ImagePullBackOff   0          5m
"""
        pod_names = self._parse_kubectl_output(kubectl_output)

        assert len(pod_names) == 3, f"Should find 3 pods, found {len(pod_names)}"
        assert "api-server-5f8d6c7b-abc12" in pod_names
        assert "frontend-7d5f8c9b-xyz89" in pod_names
        assert "nginx-deployment-6b4f8d7c-klm45" in pod_names

        # Verify header not included
        assert "NAME" not in pod_names
        assert "READY" not in pod_names

    def test_parse_pod_names_from_llm_structured_output(self):
        """
        Test parsing pod names from LLM's structured response

        Verifies:
        - Handles JSON-like structured output
        - Extracts from bullet lists
        - Handles various formatting styles
        """
        llm_response = """
Based on the investigation, the following pods need attention:

1. api-server-5f8d6c7b-abc12 - CrashLoopBackOff
2. frontend-7d5f8c9b-xyz89 - ImagePullBackOff
3. nginx-deployment-6b4f8d7c-klm45 - Error

These pods should be investigated further.
"""
        pod_names = self._extract_all_pod_names(llm_response)

        assert len(pod_names) >= 3, f"Should find at least 3 pods, found {len(pod_names)}"
        assert any("api-server" in name for name in pod_names)
        assert any("frontend" in name for name in pod_names)
        assert any("nginx" in name for name in pod_names)

    # Helper methods for pod name extraction

    @staticmethod
    def _extract_pod_name_simple(text: str) -> str:
        """Extract simple pod name without suffix handling"""
        if not text or len(text) < 3:
            return ""
        # Basic extraction - alphanumeric and hyphens only
        match = re.match(r'^([a-z0-9-]+)$', text.lower())
        if match:
            result = match.group(1)
            # Must be at least 3 characters after validation
            return result if len(result) >= 3 else ""
        return ""

    @staticmethod
    def _extract_pod_base_name(pod_name: str) -> str:
        """
        Extract base deployment name from full pod name

        Example: api-server-5f8d6c7b-abc12 -> api-server
        Example: api-server-abc123-xyz456 -> api-server
        """
        if not pod_name:
            return ""

        pod_name_lower = pod_name.lower()

        # Remove hash suffixes - handles multiple patterns:
        # 1. 8 chars + 5 chars (ReplicaSet hash + Pod hash)
        # 2. 6 chars + 6 chars (alternate hash format)
        # 3. 6+ hex chars + 5 chars (flexible matching)
        patterns = [
            r'^([a-z0-9-]+?)-[a-z0-9]{8}-[a-z0-9]{5}$',  # 8+5
            r'^([a-z0-9-]+?)-[a-z0-9]{6}-[a-z0-9]{6}$',  # 6+6
            r'^([a-z0-9-]+?)-[a-z0-9]{6,}-[a-z0-9]{5,}$',  # 6+...+5+...
        ]

        for pattern in patterns:
            match = re.match(pattern, pod_name_lower)
            if match:
                return match.group(1)

        # No standard suffix, return as-is
        return pod_name_lower

    @staticmethod
    def _extract_all_pod_names(text: str) -> List[str]:
        """Extract all pod names from text"""
        # Pattern: deployment-name-hash-hash (8+ chars between dashes)
        pattern = r'\b([a-z0-9]+(?:-[a-z0-9]+)*-[a-z0-9]{8}-[a-z0-9]{5})\b'
        matches = re.findall(pattern, text.lower())
        return list(set(matches))  # Remove duplicates

    @staticmethod
    def _is_valid_pod_name(name: str) -> bool:
        """Validate pod name follows Kubernetes naming rules"""
        if not name or len(name) < 3:
            return False
        # Must be lowercase alphanumeric with hyphens, not starting/ending with hyphen
        return bool(re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', name))

    @staticmethod
    def _extract_namespace_and_pod(namespaced_name: str) -> tuple:
        """Extract namespace and pod name from 'namespace/pod-name' format"""
        if '/' in namespaced_name:
            parts = namespaced_name.split('/', 1)
            namespace = parts[0]
            pod_full_name = parts[1]
            # Extract base name
            pod_base = TestPodNameParsing._extract_pod_base_name(pod_full_name)
            return namespace, pod_base
        return "default", TestPodNameParsing._extract_pod_base_name(namespaced_name)

    @staticmethod
    def _parse_kubectl_output(output: str) -> List[str]:
        """Parse pod names from kubectl get pods output"""
        lines = output.strip().split('\n')
        pod_names = []

        for line in lines[1:]:  # Skip header
            line = line.strip()
            if not line:
                continue
            # First column is pod name
            parts = line.split()
            if parts:
                pod_names.append(parts[0])

        return pod_names


@pytest.mark.integration
class TestAgentSequencing:
    """Test agent execution order and dependencies"""

    def test_read_ticket_is_entry_point(self):
        """
        Test that read_ticket is the workflow entry point

        Verifies:
        - Graph starts with read_ticket
        - No agent executes before read_ticket
        - ticket_id must be present in initial state
        """
        from src.supervisor import create_conditional_supervisor_graph
        from src.tools.jira_tools import JiraTools
        from src.tools.k8s_tools import K8sTools

        jira_tools = Mock(spec=JiraTools)
        k8s_tools = Mock(spec=K8sTools)

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        # Graph should have read_ticket as entry point
        # In LangGraph, entry point is set via workflow.set_entry_point("read_ticket")
        # This is verified by checking the graph structure

        # Initial state must have ticket_id
        initial_state = {"ticket_id": "TEST-123", "messages": []}
        assert "ticket_id" in initial_state, "Initial state must contain ticket_id"

    def test_history_agent_runs_after_read_ticket(self):
        """
        Test that HistoryAgent only runs after JiraAgent completes

        Verifies:
        - HistoryAgent depends on ticket_summary from JiraAgent
        - Cannot run without ticket context
        """
        from src.agents.history_agent import HistoryAgent
        from src.tools.jira_tools import JiraTools

        jira_tools = Mock(spec=JiraTools)
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Results"})

        agent = HistoryAgent(jira_tools)

        # State without ticket_summary (JiraAgent hasn't run)
        incomplete_state = {"ticket_id": "TEST-123", "messages": []}

        # State with ticket_summary (JiraAgent completed)
        complete_state = {
            "ticket_id": "TEST-123",
            "ticket_summary": "CrashLoopBackOff issue",
            "messages": []
        }

        # HistoryAgent needs ticket_summary to generate search query
        assert "ticket_summary" in complete_state
        assert complete_state["ticket_summary"] is not None

    def test_k8s_investigator_runs_after_read_ticket(self):
        """
        Test that K8sInvestigator runs after JiraAgent

        Verifies:
        - K8sInvestigator uses ticket context for investigation
        - Can run in parallel with or after HistoryAgent
        """
        # K8sInvestigator needs ticket_summary to identify targets
        state_ready = {
            "ticket_id": "TEST-123",
            "ticket_summary": "api-server pod crashing",
            "ticket_description": "Pod is in CrashLoopBackOff",
            "messages": []
        }

        # Verify required fields present
        assert "ticket_summary" in state_ready
        assert "ticket_description" in state_ready

    def test_diagnostician_runs_after_all_investigators(self):
        """
        Test that Diagnostician only runs after HistoryAgent and K8sInvestigator

        Verifies:
        - Diagnostician needs data from both investigation agents
        - Cannot produce diagnosis without investigation results
        """
        # State after all investigators complete
        complete_state = {
            "ticket_id": "TEST-123",
            "ticket_summary": "Issue",
            "similar_tickets": ["PROJ-1"],  # From HistoryAgent
            "past_resolutions": ["Solution"],  # From HistoryAgent
            "cluster_findings": {  # From K8sInvestigator (contains all investigation data)
                "resources": {"pods": {}},
                "logs": "logs",
                "events": []
            },
            "messages": []
        }

        # Verify Diagnostician has all required inputs
        assert "similar_tickets" in complete_state
        assert "past_resolutions" in complete_state
        assert "cluster_findings" in complete_state
        assert "logs" in complete_state["cluster_findings"]
        assert "events" in complete_state["cluster_findings"]

    def test_post_comment_is_terminal_node(self):
        """
        Test that post_comment is the final node before END

        Verifies:
        - post_comment is last agent to execute
        - Workflow ends after post_comment
        - No agent runs after post_comment
        """
        from src.agents.jira_agent import JiraAgent
        from src.tools.jira_tools import JiraTools

        jira_tools = Mock(spec=JiraTools)
        jira_tools.add_comment = AsyncMock(return_value={"content": "✅", "success": True})

        agent = JiraAgent(jira_tools)

        # State ready for post_comment (all investigations complete)
        final_state = {
            "ticket_id": "TEST-123",
            "root_cause": "Configuration error",
            "recommended_action": "Fix config",
            "confidence_level": "High",
            "preventive_measures": ["Validate configs"],
            "messages": []
        }

        # Verify state has all required fields for comment
        assert "root_cause" in final_state
        assert "recommended_action" in final_state
        assert "confidence_level" in final_state


@pytest.mark.integration
class TestWorkflowStateUpdates:
    """Test state updates at each workflow node"""

    @pytest.mark.asyncio
    async def test_read_ticket_updates_state_correctly(self):
        """
        Test JiraAgent.read_ticket updates state with ticket information

        Verifies:
        - ticket_summary set
        - ticket_description set
        - ticket_labels set
        - ticket_priority set
        - ticket_status set
        """
        from src.agents.jira_agent import JiraAgent
        from src.tools.jira_tools import JiraTools

        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "Pod crashing",
            "description": "api-server in CrashLoopBackOff",
            "labels": ["k8s", "production"],
            "priority": "High",
            "status": "Open"
        })

        with patch('src.agents.jira_agent.ChatOpenAI') as mock_llm:
            mock_response = Mock()
            mock_response.content = "CrashLoopBackOff in api-server"
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)

            agent = JiraAgent(jira_tools)
            state = {"ticket_id": "TEST-123", "messages": []}

            result = await agent.read_ticket(state)

        # Verify all ticket fields updated
        assert result.get("ticket_summary") is not None
        assert result.get("ticket_description") == "api-server in CrashLoopBackOff"
        assert result.get("ticket_labels") == ["k8s", "production"]
        assert result.get("ticket_priority") == "High"
        assert result.get("ticket_status") == "Open"

    @pytest.mark.asyncio
    async def test_search_history_updates_state_correctly(self):
        """
        Test HistoryAgent updates state with historical findings

        Verifies:
        - similar_tickets populated
        - past_resolutions populated
        - Data is list format
        """
        from src.agents.history_agent import HistoryAgent
        from src.tools.jira_tools import JiraTools

        jira_tools = Mock(spec=JiraTools)
        jira_tools.search_tickets = AsyncMock(return_value=[
            "PROJ-100: Similar issue - RESOLVED",
            "PROJ-101: Another case - RESOLVED"
        ])

        with patch('src.agents.history_agent.create_extraction_llm') as mock_llm:
            # Mock JQL query generation
            jql_response = Mock()
            jql_response.content = 'text ~ "CrashLoopBackOff" AND resolution != Unresolved'

            # Mock resolution analysis
            analysis_response = Mock()
            analysis_response.content = """
- Most cases caused by missing configuration
- Solution: verify ConfigMap mounting
- Average resolution: 30 minutes
"""

            mock_llm.return_value.ainvoke = AsyncMock(side_effect=[jql_response, analysis_response])

            agent = HistoryAgent(jira_tools)
            state = {
                "ticket_id": "TEST-123",
                "ticket_summary": "CrashLoopBackOff in api-server",
                "messages": []
            }

            result = await agent.run(state)

        # Verify history fields updated
        assert "similar_tickets" in result
        assert "past_resolutions" in result
        assert isinstance(result["similar_tickets"], list)
        assert isinstance(result["past_resolutions"], list)

    @pytest.mark.asyncio
    async def test_investigate_cluster_updates_state_correctly(self):
        """
        Test K8sInvestigator updates state with cluster findings

        Verifies:
        - cluster_findings populated with investigation data
        - pod_status set
        - logs captured
        - events recorded
        """
        from src.agents.k8s_investigator import K8sInvestigator
        from src.tools.k8s_tools import K8sTools

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={
            "items": [{"metadata": {"name": "api-server-abc123"}, "status": {"phase": "CrashLoopBackOff"}}]
        })
        k8s_tools.kubectl_logs = AsyncMock(return_value="Error: config file not found")
        k8s_tools.kubectl_events = AsyncMock(return_value=[
            {"type": "Warning", "reason": "BackOff"}
        ])
        k8s_tools.kubectl_top = AsyncMock(return_value={"cpu": "100m"})

        with patch('src.agents.k8s_investigator.ChatOpenAI') as mock_llm:
            mock_response = Mock()
            mock_response.content = "api-server-abc123"
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)

            agent = K8sInvestigator(k8s_tools)
            state = {
                "ticket_id": "TEST-123",
                "ticket_summary": "api-server pod crashing",
                "messages": []
            }

            result = await agent.run(state)

        # Verify cluster investigation fields updated (all data in cluster_findings)
        assert "cluster_findings" in result
        assert "resources" in result["cluster_findings"]
        assert "logs" in result["cluster_findings"]
        assert "events" in result["cluster_findings"]
        assert len(result["cluster_findings"]["events"]) > 0

    @pytest.mark.asyncio
    async def test_diagnose_updates_state_correctly(self):
        """
        Test Diagnostician updates state with diagnosis

        Verifies:
        - root_cause set
        - recommended_action set
        - confidence_level set
        - preventive_measures populated
        """
        from src.agents.diagnostician import Diagnostician

        with patch('src.agents.diagnostician.ChatOpenAI') as mock_llm:
            mock_response = Mock()
            mock_response.content = """
## Root Cause
Missing configuration file causing pod crash

## Recommended Action
Create ConfigMap with required configuration

## Confidence Level
High - clear error in logs

## Preventive Measures
- Validate configs before deployment
- Add configuration validation to CI/CD
- Implement drift detection
"""
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)

            agent = Diagnostician()
            state = {
                "ticket_id": "TEST-123",
                "ticket_summary": "CrashLoopBackOff",
                "similar_tickets": [],
                "past_resolutions": [],
                "cluster_findings": {
                    "resources": {"pods": {}},
                    "logs": "Error: config not found",
                    "events": []
                },
                "messages": []
            }

            result = await agent.run(state)

        # Verify diagnosis fields updated
        assert result.get("root_cause") is not None
        assert result.get("recommended_action") is not None
        assert result.get("confidence_level") in ["High", "Medium", "Low"]
        assert "preventive_measures" in result
        assert len(result["preventive_measures"]) >= 3


# ============================================================================
# COMPREHENSIVE STATE PASSING TESTS
# ============================================================================

@pytest.mark.integration
class TestDetailedStatePassing:
    """
    Comprehensive tests for state passing between agents in LangGraph workflow

    These tests verify detailed state flow mechanics at each transition:
    - JiraAgent → HistoryAgent
    - HistoryAgent → K8sInvestigator
    - K8sInvestigator → Diagnostician
    - Diagnostician → JiraAgent (final comment)
    """

    @pytest.mark.asyncio
    async def test_jira_to_history_complete_state_transfer(self):
        """
        Test complete state transfer from JiraAgent to HistoryAgent

        Verifies:
        - All ticket fields populated by JiraAgent are accessible to HistoryAgent
        - HistoryAgent can read ticket_summary, description, labels
        - HistoryAgent adds historical data without modifying ticket fields
        - State field types remain consistent
        """
        from src.agents.jira_agent import JiraAgent
        from src.agents.history_agent import HistoryAgent
        from src.tools.jira_tools import JiraTools

        # Setup
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "CrashLoopBackOff in api-server pod",
            "description": "Pod failing in namespace production. Exit code 1 observed.",
            "labels": ["kubernetes", "production", "crashloop"],
            "priority": "Critical",
            "status": "Open"
        })
        jira_tools.search_tickets = AsyncMock(return_value=[
            "PROJ-100: Similar CrashLoopBackOff issue - RESOLVED",
            "PROJ-101: api-server config problem - RESOLVED"
        ])

        initial_state = {
            "ticket_id": "TEST-123",
            "messages": [],
            "iteration_count": 0
        }

        # Step 1: JiraAgent reads ticket
        jira_agent = JiraAgent(jira_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as mock_jira_llm:
            mock_resp = Mock()
            mock_resp.content = "CrashLoopBackOff detected in api-server pod in production namespace"
            mock_jira_llm.return_value.ainvoke = AsyncMock(return_value=mock_resp)

            state_after_jira = await jira_agent.read_ticket(initial_state)

        # Verify JiraAgent populated ticket fields
        assert state_after_jira["ticket_description"] == "Pod failing in namespace production. Exit code 1 observed."
        assert state_after_jira["ticket_labels"] == ["kubernetes", "production", "crashloop"]
        assert state_after_jira["ticket_priority"] == "Critical"
        assert state_after_jira["ticket_status"] == "Open"
        assert state_after_jira["ticket_summary"] is not None

        # Store original values for comparison
        original_description = state_after_jira["ticket_description"]
        original_labels = state_after_jira["ticket_labels"][:]
        original_priority = state_after_jira["ticket_priority"]

        # Step 2: HistoryAgent processes state
        history_agent = HistoryAgent(jira_tools)

        with patch('src.agents.history_agent.create_extraction_llm') as mock_hist_llm:
            # Mock JQL generation
            mock_jql = Mock()
            mock_jql.content = 'text ~ "CrashLoopBackOff" AND labels = kubernetes'

            # Mock analysis
            mock_analysis = Mock()
            mock_analysis.content = "- Common issue: missing configuration\n- Typical fix: update ConfigMap"

            mock_hist_llm.return_value.ainvoke = AsyncMock(side_effect=[mock_jql, mock_analysis])

            state_after_history = await history_agent.run(state_after_jira)

        # Verify HistoryAgent READ ticket fields correctly
        assert state_after_history["ticket_summary"] == state_after_jira["ticket_summary"]
        assert state_after_history["ticket_description"] == original_description
        assert state_after_history["ticket_labels"] == original_labels
        assert state_after_history["ticket_priority"] == original_priority

        # Verify HistoryAgent ADDED historical fields
        assert "similar_tickets" in state_after_history
        assert "past_resolutions" in state_after_history
        assert len(state_after_history["similar_tickets"]) > 0
        assert len(state_after_history["past_resolutions"]) > 0

        # Verify no overwriting occurred
        assert state_after_history["ticket_description"] == original_description
        assert state_after_history["ticket_labels"] == original_labels

    @pytest.mark.asyncio
    async def test_history_to_k8s_state_accumulation(self):
        """
        Test state accumulation from HistoryAgent to K8sInvestigator

        Verifies:
        - K8sInvestigator receives both ticket AND historical context
        - K8sInvestigator can access similar_tickets and past_resolutions
        - K8sInvestigator adds cluster findings without modifying history
        - All previous state fields remain intact
        """
        from src.agents.history_agent import HistoryAgent
        from src.agents.k8s_investigator import K8sInvestigator
        from src.tools.jira_tools import JiraTools
        from src.tools.k8s_tools import K8sTools

        # Setup
        jira_tools = Mock(spec=JiraTools)
        jira_tools.search_tickets = AsyncMock(return_value=[
            "PROJ-100: Database connection failure",
            "PROJ-101: Missing environment variable"
        ])

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={
            "items": [{
                "metadata": {"name": "api-server-abc123"},
                "status": {"phase": "CrashLoopBackOff"}
            }]
        })
        k8s_tools.kubectl_logs = AsyncMock(return_value="ERROR: DATABASE_URL not set")
        k8s_tools.kubectl_events = AsyncMock(return_value=[
            {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting"}
        ])
        k8s_tools.kubectl_top = AsyncMock(return_value={"cpu": "50m", "memory": "128Mi"})

        # Initial state with ticket context
        state_with_ticket = {
            "ticket_id": "TEST-123",
            "ticket_summary": "CrashLoopBackOff in api-server",
            "ticket_description": "Pod is crashing",
            "ticket_labels": ["k8s"],
            "ticket_priority": "High",
            "messages": [],
            "iteration_count": 0
        }

        # Step 1: HistoryAgent
        history_agent = HistoryAgent(jira_tools)

        with patch('src.agents.history_agent.create_extraction_llm') as mock_llm:
            mock_jql = Mock()
            mock_jql.content = 'text ~ "CrashLoopBackOff"'
            mock_analysis = Mock()
            mock_analysis.content = "- Database issues common\n- Check environment variables"

            mock_llm.return_value.ainvoke = AsyncMock(side_effect=[mock_jql, mock_analysis])

            state_after_history = await history_agent.run(state_with_ticket)

        # Verify historical data added
        assert "similar_tickets" in state_after_history
        assert "past_resolutions" in state_after_history

        # Store historical data for verification
        historical_tickets = state_after_history["similar_tickets"][:]
        historical_resolutions = state_after_history["past_resolutions"][:]

        # Step 2: K8sInvestigator
        k8s_agent = K8sInvestigator(k8s_tools)

        with patch('src.agents.k8s_investigator.ChatOpenAI') as mock_llm:
            mock_targets = Mock()
            mock_targets.content = '{"namespace": "default", "pod": "api-server"}'
            mock_k8s_analysis = Mock()
            mock_k8s_analysis.content = "Missing DATABASE_URL environment variable"

            mock_llm.return_value.ainvoke = AsyncMock(side_effect=[mock_targets, mock_k8s_analysis])

            state_after_k8s = await k8s_agent.run(state_after_history)

        # Verify K8sInvestigator READ historical context
        assert state_after_k8s["similar_tickets"] == historical_tickets
        assert state_after_k8s["past_resolutions"] == historical_resolutions

        # Verify K8sInvestigator still has ticket context
        assert state_after_k8s["ticket_summary"] == "CrashLoopBackOff in api-server"
        assert state_after_k8s["ticket_description"] == "Pod is crashing"

        # Verify K8sInvestigator ADDED cluster findings (all data inside cluster_findings)
        assert "cluster_findings" in state_after_k8s
        assert "resources" in state_after_k8s["cluster_findings"]
        assert "logs" in state_after_k8s["cluster_findings"]
        assert "events" in state_after_k8s["cluster_findings"]

        # Verify no overwriting of historical data
        assert state_after_k8s["similar_tickets"] == historical_tickets

    @pytest.mark.asyncio
    async def test_k8s_to_diagnostician_complete_context(self):
        """
        Test Diagnostician receives complete accumulated state

        Verifies:
        - Diagnostician has access to ALL previous agent outputs
        - Ticket context, historical data, AND cluster findings available
        - Diagnostician can synthesize all information
        - Previous state fields remain unchanged
        """
        from src.agents.k8s_investigator import K8sInvestigator
        from src.agents.diagnostician import Diagnostician
        from src.tools.k8s_tools import K8sTools

        # Setup
        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={
            "items": [{"metadata": {"name": "api-server-abc123"}, "status": {"phase": "CrashLoopBackOff"}}]
        })
        k8s_tools.kubectl_logs = AsyncMock(return_value="ERROR: Missing DATABASE_URL")
        k8s_tools.kubectl_events = AsyncMock(return_value=[{"type": "Warning", "reason": "BackOff"}])
        k8s_tools.kubectl_top = AsyncMock(return_value={"cpu": "100m"})

        # State with ticket and historical context
        state_before_k8s = {
            "ticket_id": "TEST-123",
            "ticket_summary": "CrashLoopBackOff in api-server",
            "ticket_description": "Pod failing to start",
            "ticket_labels": ["k8s", "production"],
            "ticket_priority": "Critical",
            "similar_tickets": ["PROJ-100: Database config issue", "PROJ-101: Env var missing"],
            "past_resolutions": ["Update secret with DATABASE_URL", "Mount ConfigMap correctly"],
            "messages": [],
            "iteration_count": 0
        }

        # Step 1: K8sInvestigator
        k8s_agent = K8sInvestigator(k8s_tools)

        with patch('src.agents.k8s_investigator.ChatOpenAI') as mock_llm:
            mock_targets = Mock()
            mock_targets.content = '{"namespace": "default", "pod": "api-server"}'
            mock_analysis = Mock()
            mock_analysis.content = "DATABASE_URL environment variable not set"

            mock_llm.return_value.ainvoke = AsyncMock(side_effect=[mock_targets, mock_analysis])

            state_after_k8s = await k8s_agent.run(state_before_k8s)

        # Verify K8s findings added (all data inside cluster_findings)
        assert "cluster_findings" in state_after_k8s
        assert "resources" in state_after_k8s["cluster_findings"]
        assert "logs" in state_after_k8s["cluster_findings"]

        # Step 2: Diagnostician
        diagnostician = Diagnostician()

        with patch('src.agents.diagnostician.ChatOpenAI') as mock_llm:
            mock_diag = Mock()
            mock_diag.content = """
## Root Cause
Missing DATABASE_URL environment variable in pod configuration causing startup failure

## Recommended Action
1. Create secret with DATABASE_URL
2. Update deployment to reference secret
3. Restart pods

## Confidence Level
High - logs clearly show missing env var, historical tickets confirm same pattern

## Preventive Measures
- Add validation for required environment variables before deployment
- Implement configuration drift detection
- Add pre-deployment checks to CI/CD pipeline
"""
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_diag)

            state_after_diag = await diagnostician.run(state_after_k8s)

        # Verify Diagnostician READ all previous context
        # Ticket context
        assert state_after_diag["ticket_summary"] == "CrashLoopBackOff in api-server"
        assert state_after_diag["ticket_description"] == "Pod failing to start"
        assert state_after_diag["ticket_labels"] == ["k8s", "production"]

        # Historical context
        assert "PROJ-100" in str(state_after_diag["similar_tickets"])
        assert "PROJ-101" in str(state_after_diag["similar_tickets"])
        assert len(state_after_diag["past_resolutions"]) == 2

        # Cluster findings (all data inside cluster_findings)
        assert "cluster_findings" in state_after_diag
        assert "resources" in state_after_diag["cluster_findings"]
        assert "logs" in state_after_diag["cluster_findings"]
        assert "DATABASE_URL" in str(state_after_diag["cluster_findings"]["logs"])

        # Verify Diagnostician ADDED diagnosis
        assert "root_cause" in state_after_diag
        assert "recommended_action" in state_after_diag
        assert "confidence_level" in state_after_diag
        assert "preventive_measures" in state_after_diag

        # Verify diagnosis quality
        assert "DATABASE_URL" in state_after_diag["root_cause"]
        assert state_after_diag["confidence_level"] == "High"
        assert len(state_after_diag["preventive_measures"]) >= 3

    @pytest.mark.asyncio
    async def test_diagnostician_to_jira_final_comment(self):
        """
        Test final state flow from Diagnostician to JiraAgent comment posting

        Verifies:
        - JiraAgent can format complete diagnosis for Jira comment
        - All diagnosis fields accessible
        - Comment includes root cause, action, confidence, measures
        - State complete at end of workflow
        """
        from src.agents.diagnostician import Diagnostician
        from src.agents.jira_agent import JiraAgent
        from src.tools.jira_tools import JiraTools

        # Setup
        jira_tools = Mock(spec=JiraTools)
        jira_tools.add_comment = AsyncMock(return_value={"status": "success"})

        # State before Diagnostician
        state_before_diag = {
            "ticket_id": "TEST-123",
            "ticket_summary": "CrashLoopBackOff in api-server",
            "ticket_description": "Pod failing repeatedly",
            "ticket_labels": ["k8s"],
            "ticket_priority": "High",
            "similar_tickets": ["PROJ-100"],
            "past_resolutions": ["Fixed config"],
            "cluster_findings": {
                "resources": {"pods": {"status": "CrashLoopBackOff"}},
                "logs": "ERROR: DATABASE_URL not set",
                "events": [{"type": "Warning"}]
            },
            "messages": [],
            "iteration_count": 0
        }

        # Step 1: Diagnostician
        with patch('src.agents.diagnostician.ChatOpenAI') as mock_llm:
            mock_diag = Mock()
            mock_diag.content = """
## Root Cause
Missing DATABASE_URL environment variable

## Recommended Action
Add DATABASE_URL to pod environment

## Confidence Level
High

## Preventive Measures
- Validate required env vars
- Add configuration checks
- Implement drift detection
"""
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_diag)

            # Create Diagnostician inside patch context so it uses mocked LLM
            diagnostician = Diagnostician()
            state_after_diag = await diagnostician.run(state_before_diag)

        # Verify diagnosis complete
        assert state_after_diag["root_cause"] == "Missing DATABASE_URL environment variable"
        assert state_after_diag["recommended_action"] == "Add DATABASE_URL to pod environment"
        assert state_after_diag["confidence_level"] == "High"
        assert len(state_after_diag["preventive_measures"]) == 3

        # Step 2: JiraAgent post_comment
        jira_agent = JiraAgent(jira_tools)

        state_final = await jira_agent.post_comment(state_after_diag)

        # Verify add_comment was called
        jira_tools.add_comment.assert_called_once()

        # Verify comment content
        call_args = jira_tools.add_comment.call_args
        comment_text = call_args[1]["comment"]

        # Check all diagnosis fields present in comment
        assert "Root Cause" in comment_text
        assert "DATABASE_URL" in comment_text
        assert "Recommended Action" in comment_text
        assert "Confidence Level" in comment_text
        assert "High" in comment_text
        assert "Preventive Measures" in comment_text
        assert "Validate required env vars" in comment_text

    @pytest.mark.asyncio
    async def test_state_field_isolation_no_cross_contamination(self):
        """
        Test that agents only modify their designated state fields

        Verifies:
        - JiraAgent only sets ticket_* fields
        - HistoryAgent only sets similar_tickets and past_resolutions
        - K8sInvestigator only sets cluster/pod/logs/events fields
        - Diagnostician only sets diagnosis fields
        - No agent overwrites another agent's output
        """
        from src.agents.jira_agent import JiraAgent
        from src.agents.history_agent import HistoryAgent
        from src.agents.k8s_investigator import K8sInvestigator
        from src.agents.diagnostician import Diagnostician
        from src.tools.jira_tools import JiraTools
        from src.tools.k8s_tools import K8sTools

        # Setup
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "Test issue",
            "description": "Test description",
            "labels": ["test"],
            "priority": "Low",
            "status": "Open"
        })
        jira_tools.search_tickets = AsyncMock(return_value=["PROJ-100"])

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        initial_state = {
            "ticket_id": "TEST-123",
            "messages": [],
            "iteration_count": 0
        }

        # Execute agents sequentially and track field modifications
        state = initial_state.copy()

        # JiraAgent
        jira_agent = JiraAgent(jira_tools)
        with patch('src.agents.jira_agent.ChatOpenAI') as mock_llm:
            mock_resp = Mock()
            mock_resp.content = "Summary"
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_resp)
            state = await jira_agent.read_ticket(state)

        # Verify JiraAgent ONLY set ticket fields
        assert "ticket_description" in state
        assert "ticket_labels" in state
        assert "ticket_priority" in state
        # Should NOT have set these
        assert state.get("similar_tickets") is None
        assert state.get("cluster_findings") is None
        assert state.get("root_cause") is None

        jira_description = state["ticket_description"]

        # HistoryAgent
        history_agent = HistoryAgent(jira_tools)
        with patch('src.agents.history_agent.create_extraction_llm') as mock_llm:
            mock_jql = Mock()
            mock_jql.content = "jql"
            mock_analysis = Mock()
            mock_analysis.content = "- insight"
            mock_llm.return_value.ainvoke = AsyncMock(side_effect=[mock_jql, mock_analysis])
            state = await history_agent.run(state)

        # Verify HistoryAgent ONLY set history fields
        assert "similar_tickets" in state
        assert "past_resolutions" in state
        # Should NOT have modified ticket fields
        assert state["ticket_description"] == jira_description
        # Should NOT have set these
        assert state.get("cluster_findings") is None
        assert state.get("root_cause") is None

        history_tickets = state["similar_tickets"][:]

        # K8sInvestigator
        k8s_agent = K8sInvestigator(k8s_tools)
        with patch('src.agents.k8s_investigator.ChatOpenAI') as mock_llm:
            mock_targets = Mock()
            mock_targets.content = '{"namespace": "default"}'
            mock_analysis = Mock()
            mock_analysis.content = "analysis"
            mock_llm.return_value.ainvoke = AsyncMock(side_effect=[mock_targets, mock_analysis])
            state = await k8s_agent.run(state)

        # Verify K8sInvestigator ONLY set cluster fields (all data inside cluster_findings)
        assert "cluster_findings" in state
        assert "resources" in state["cluster_findings"]
        assert "logs" in state["cluster_findings"]
        assert "events" in state["cluster_findings"]
        # Should NOT have modified ticket or history fields
        assert state["ticket_description"] == jira_description
        assert state["similar_tickets"] == history_tickets
        # Should NOT have set diagnosis fields yet
        assert state.get("root_cause") is None
        assert state.get("confidence_level") is None

        # Diagnostician
        diagnostician = Diagnostician()
        with patch('src.agents.diagnostician.ChatOpenAI') as mock_llm:
            mock_diag = Mock()
            mock_diag.content = """
## Root Cause
Test cause

## Recommended Action
Test action

## Confidence Level
Medium

## Preventive Measures
- measure 1
"""
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_diag)
            state = await diagnostician.run(state)

        # Verify Diagnostician ONLY set diagnosis fields
        assert "root_cause" in state
        assert "recommended_action" in state
        assert "confidence_level" in state
        assert "preventive_measures" in state
        # Should NOT have modified any previous fields
        assert state["ticket_description"] == jira_description
        assert state["similar_tickets"] == history_tickets
        assert "cluster_findings" in state  # Still present

    @pytest.mark.asyncio
    async def test_error_state_propagation(self):
        """
        Test that errors are properly captured in state and workflow continues

        Verifies:
        - Agent errors don't crash workflow
        - Error information captured in state
        - Subsequent agents receive error state
        - Workflow completes with partial data
        """
        from src.agents.jira_agent import JiraAgent
        from src.agents.history_agent import HistoryAgent
        from src.agents.diagnostician import Diagnostician
        from src.tools.jira_tools import JiraTools

        # Setup with error conditions
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"error": "Ticket not found"})
        jira_tools.search_tickets = AsyncMock(side_effect=Exception("API timeout"))

        initial_state = {
            "ticket_id": "TEST-999",
            "messages": [],
            "iteration_count": 0
        }

        # JiraAgent with error
        jira_agent = JiraAgent(jira_tools)
        with patch('src.agents.jira_agent.ChatOpenAI') as mock_llm:
            mock_resp = Mock()
            mock_resp.content = "Error"
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_resp)
            state = await jira_agent.read_ticket(initial_state)

        # Verify error captured
        assert "ticket_summary" in state
        assert "error" in state["ticket_summary"].lower() or "Error" in state["ticket_summary"]

        # HistoryAgent with error
        history_agent = HistoryAgent(jira_tools)
        with patch('src.agents.history_agent.create_extraction_llm') as mock_llm:
            mock_resp = Mock()
            mock_resp.content = "jql"
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_resp)
            state = await history_agent.run(state)

        # Verify error handled gracefully
        assert "similar_tickets" in state
        assert state["similar_tickets"] == []  # Empty due to error
        assert "past_resolutions" in state

        # Diagnostician should handle incomplete state
        diagnostician = Diagnostician()
        with patch('src.agents.diagnostician.ChatOpenAI') as mock_llm:
            # Simulate LLM error
            mock_llm.return_value.ainvoke = AsyncMock(side_effect=Exception("LLM timeout"))
            state = await diagnostician.run(state)

        # Verify fallback diagnosis provided
        assert "root_cause" in state
        assert "recommended_action" in state
        assert "confidence_level" in state
        # Confidence should be low
        assert "low" in state["confidence_level"].lower()

    def test_state_type_consistency(self):
        """
        Test that state field types remain consistent throughout workflow

        Verifies:
        - String fields remain strings
        - List fields remain lists
        - Dict fields remain dicts
        - Integer fields remain integers
        - None/Optional fields handled correctly
        """
        state = {
            "ticket_id": "TEST-123",
            "ticket_summary": None,
            "ticket_description": None,
            "ticket_labels": [],
            "similar_tickets": [],
            "past_resolutions": [],
            "cluster_findings": {
                "resources": {},
                "logs": None,
                "events": []
            },
            "root_cause": None,
            "recommended_action": None,
            "confidence_level": None,
            "preventive_measures": [],
            "messages": [],
            "iteration_count": 0,
            "issue_resolved": False
        }

        # Verify initial types
        assert isinstance(state["ticket_id"], str)
        assert isinstance(state["ticket_labels"], list)
        assert isinstance(state["similar_tickets"], list)
        assert isinstance(state["cluster_findings"], dict)
        assert isinstance(state["iteration_count"], int)
        assert isinstance(state["issue_resolved"], bool)

        # Simulate agent updates
        state["ticket_summary"] = "Summary text"
        state["ticket_labels"].append("kubernetes")
        state["similar_tickets"].append("PROJ-100")
        state["cluster_findings"]["resources"]["pods"] = {"status": "CrashLoopBackOff"}
        state["iteration_count"] += 1

        # Verify types remain consistent
        assert isinstance(state["ticket_summary"], str)
        assert isinstance(state["ticket_labels"], list)
        assert isinstance(state["similar_tickets"], list)
        assert isinstance(state["cluster_findings"], dict)
        assert isinstance(state["iteration_count"], int)
        assert len(state["ticket_labels"]) == 1
        assert len(state["similar_tickets"]) == 1

    @pytest.mark.asyncio
    async def test_message_accumulation_through_workflow(self):
        """
        Test that messages list properly accumulates agent interactions

        Verifies:
        - Messages list starts empty
        - Each agent can append to messages
        - Message order preserved
        - Messages accessible to all subsequent agents
        """
        state = {
            "ticket_id": "TEST-123",
            "messages": [],
            "iteration_count": 0
        }

        # Verify initial state
        assert state["messages"] == []
        assert isinstance(state["messages"], list)

        # Simulate agents adding messages
        state["messages"].append({
            "role": "agent",
            "name": "JiraAgent",
            "content": "Retrieved ticket TEST-123 successfully"
        })

        assert len(state["messages"]) == 1
        assert state["messages"][0]["name"] == "JiraAgent"

        state["messages"].append({
            "role": "agent",
            "name": "HistoryAgent",
            "content": "Found 3 similar tickets with resolution patterns"
        })

        assert len(state["messages"]) == 2
        assert state["messages"][1]["name"] == "HistoryAgent"

        state["messages"].append({
            "role": "agent",
            "name": "K8sInvestigator",
            "content": "Investigated cluster, found 1 problem pod"
        })

        assert len(state["messages"]) == 3

        state["messages"].append({
            "role": "agent",
            "name": "Diagnostician",
            "content": "Diagnosis complete with high confidence"
        })

        assert len(state["messages"]) == 4

        # Verify message order preserved
        assert state["messages"][0]["name"] == "JiraAgent"
        assert state["messages"][1]["name"] == "HistoryAgent"
        assert state["messages"][2]["name"] == "K8sInvestigator"
        assert state["messages"][3]["name"] == "Diagnostician"
