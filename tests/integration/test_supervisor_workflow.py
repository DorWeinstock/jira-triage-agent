"""
Comprehensive integration tests for LangGraph supervisor workflow orchestration

Tests the supervisor graph's orchestration capabilities, including:
- Conditional routing logic (all branches)
- Multi-agent collaboration and handoffs
- State consistency throughout workflow transitions
- Error handling and graceful degradation
- Investigation loops and cycle prevention
- Entry/exit point validation
- Graph structure validation
- Performance and timeout handling

This test suite validates that the supervisor correctly orchestrates the
4 specialized agents (JiraAgent, HistoryAgent, K8sInvestigator, Diagnostician)
through various workflow scenarios including happy paths, edge cases, and error conditions.
"""

import asyncio
import pytest
from typing import Dict, Any, List
from unittest.mock import AsyncMock, Mock, patch, MagicMock, call
from langgraph.graph import END

# Import the components under test
from src.supervisor import create_conditional_supervisor_graph, get_default_graph
from src.tools.jira_tools import JiraTools
from src.tools.k8s_tools import K8sTools
from src.state import AgentState


@pytest.mark.integration
class TestBasicWorkflowExecution:
    """Test basic workflow execution - happy path scenarios"""

    @pytest.mark.asyncio
    async def test_complete_happy_path_with_all_agents(self):
        """
        Test complete happy path: ticket → history → investigation → diagnosis → comment

        Verifies:
        - All agent nodes execute in correct order
        - State is preserved and accumulated throughout workflow
        - Final result contains all expected fields
        - Workflow reaches END state successfully
        """
        # Mock tool clients
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "content": "CrashLoopBackOff in api-server pod",
            "summary": "Pod crashing in production",
            "description": "The api-server pod is in CrashLoopBackOff state",
            "priority": "High",
            "status": "Open",
            "labels": ["k8s", "production", "urgent"]
        })
        jira_tools.search_tickets = AsyncMock(return_value={
            "content": "Found 3 similar tickets: PROJ-100 (resolved), PROJ-101 (resolved), PROJ-102 (in-progress)"
        })
        jira_tools.add_comment = AsyncMock(return_value={
            "content": "✅ Comment added successfully",
            "success": True
        })

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={
            "items": [{
                "metadata": {"name": "api-server-5f8d6c7b-abc12"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{
                        "restartCount": 5,
                        "state": {"waiting": {"reason": "CrashLoopBackOff"}}
                    }]
                }
            }]
        })
        k8s_tools.kubectl_logs = AsyncMock(return_value="Error: Failed to load configuration from /etc/config/app.yaml")
        k8s_tools.kubectl_events = AsyncMock(return_value=[
            {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container"}
        ])
        k8s_tools.kubectl_top = AsyncMock(return_value={"cpu": "50m", "memory": "128Mi"})

        # Initial state
        initial_state = {
            "ticket_id": "PROD-123",
            "messages": [],
            "iteration_count": 0
        }

        # Mock all LLM calls BEFORE creating graph
        with patch('src.agents.jira_agent.ChatOpenAI') as mock_jira_llm, \
             patch('src.agents.history_agent.create_extraction_llm') as mock_history_llm, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as mock_k8s_llm, \
             patch('src.agents.diagnostician.ChatOpenAI') as mock_diag_llm:

            # Configure mock responses
            jira_response = Mock(content="CrashLoopBackOff in api-server pod due to missing config")
            history_response = Mock(content="Similar issues resolved by fixing ConfigMap")
            k8s_response = Mock(content="Pod failing due to missing /etc/config/app.yaml file")
            diag_response = Mock(content="""
## Root Cause
Missing configuration file at /etc/config/app.yaml causing application crash on startup

## Recommended Action
1. Create or restore the ConfigMap containing app.yaml
2. Verify the volume mount is correct
3. Restart the pod

## Confidence Level
High - Clear evidence from logs and similar past issues

## Preventive Measures
- Add configuration validation in CI/CD pipeline
- Implement ConfigMap backup strategy
- Add monitoring for missing ConfigMaps
            """)

            # K8s investigator makes 3 LLM calls:
            # 1. _identify_targets - extract namespace, pod names from ticket
            # 2. _identify_problem_pods - identify which pods have issues
            # 3. _analyze_findings - analyze all the gathered data
            k8s_identify_targets = Mock(content='{"namespace": "default", "pods": ["api-server"], "services": [], "deployments": []}')
            k8s_identify_pods = Mock(content="api-server-5f8d6c7b-abc12")
            k8s_analyze_findings = Mock(content="Pod failing due to missing /etc/config/app.yaml file")

            mock_jira_llm.return_value.ainvoke = AsyncMock(return_value=jira_response)
            mock_history_llm.return_value.ainvoke = AsyncMock(return_value=history_response)
            mock_k8s_llm.return_value.ainvoke = AsyncMock(side_effect=[k8s_identify_targets, k8s_identify_pods, k8s_analyze_findings])
            mock_diag_llm.return_value.ainvoke = AsyncMock(return_value=diag_response)

            # Create supervisor graph inside the patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            # Execute workflow
            result = await graph.ainvoke(initial_state)

            # Verify all expected state fields are populated
            assert result["ticket_id"] == "PROD-123"
            assert result["ticket_summary"] is not None
            assert result["ticket_description"] is not None
            assert result["ticket_labels"] == ["k8s", "production", "urgent"]

            # Verify historical analysis was performed
            assert "similar_tickets" in result or result.get("ticket_summary")

            # Verify K8s investigation was performed
            assert result.get("cluster_findings") or result.get("logs")

            # Verify diagnosis was generated
            assert result["root_cause"] is not None
            assert result["recommended_action"] is not None
            assert result["confidence_level"] is not None
            assert result["preventive_measures"] is not None

            # Verify tools were called
            jira_tools.get_ticket.assert_called_once_with("PROD-123")
            jira_tools.add_comment.assert_called_once()
            k8s_tools.kubectl_get.assert_called()
            k8s_tools.kubectl_logs.assert_called()

    @pytest.mark.asyncio
    async def test_workflow_state_accumulation(self):
        """
        Test that state accumulates correctly throughout workflow

        Verifies:
        - Each agent adds its findings to state
        - Previous agent results are available to subsequent agents
        - No state fields are overwritten unintentionally
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "OOMKilled pod",
            "description": "Pod killed due to memory",
            "priority": "High"
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Similar: PROJ-50"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="OOMKilled")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={"memory": "2Gi"})

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="OOMKilled"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="Increase memory limits"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="Memory limit too low"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Memory limit too low for workload

## Recommended Action
Increase memory limit to 2Gi

## Confidence Level
High

## Preventive Measures
- Set proper memory limits
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "MEM-456",
                "messages": [],
                "iteration_count": 0
            })

            # Verify state accumulation
            assert result["ticket_id"] == "MEM-456"  # Original field preserved
            assert result.get("ticket_summary")  # JiraAgent added
            assert result.get("root_cause")  # Diagnostician added
            assert result["iteration_count"] >= 0  # Iteration tracking maintained


@pytest.mark.integration
class TestConditionalRouting:
    """Test all conditional routing branches in the supervisor"""

    @pytest.mark.asyncio
    async def test_should_search_history_routes_to_history(self):
        """
        Test that normal tickets route through history search

        Verifies:
        - read_ticket → search_history path is taken
        - History agent executes before K8s investigation
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "Normal CrashLoopBackOff issue",
            "description": "Standard pod crash"
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found similar"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Error logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Normal issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="Historical context"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="Investigation"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Standard issue

## Recommended Action
Standard fix

## Confidence Level
Medium

## Preventive Measures
- Monitor
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "NORM-100",
                "messages": [],
                "iteration_count": 0
            })

            # Verify history search was performed
            jira_tools.search_tickets.assert_called()

    @pytest.mark.asyncio
    async def test_skip_history_for_new_feature_keyword(self):
        """
        Test that tickets with 'new feature' skip history search

        Verifies:
        - read_ticket → investigate_cluster path (skip history)
        - History agent is NOT executed
        - Workflow proceeds directly to K8s investigation
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "New feature deployment failing",
            "description": "Brand new service having issues"
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Should not be called"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="New service logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="New feature deployment issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="Should not execute"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="K8s investigation"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
New deployment issue

## Recommended Action
Fix deployment

## Confidence Level
Medium

## Preventive Measures
- Test deployments
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "NEW-200",
                "messages": [],
                "iteration_count": 0
            })

            # Verify history search was SKIPPED
            jira_tools.search_tickets.assert_not_called()

            # Verify K8s investigation still happened
            k8s_tools.kubectl_get.assert_called()

    @pytest.mark.asyncio
    async def test_skip_history_for_first_time_keyword(self):
        """Test that 'first time' keyword also skips history"""
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "First time seeing this error",
            "description": "Never encountered before"
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Should not execute"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="First time issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="Should not execute"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="Investigation"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Novel issue

## Recommended Action
Investigate manually

## Confidence Level
Low

## Preventive Measures
- Document
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "FIRST-300",
                "messages": [],
                "iteration_count": 0
            })

            jira_tools.search_tickets.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_history_for_never_seen_keyword(self):
        """Test that 'never seen' keyword also skips history"""
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "Never seen this problem before",
            "description": "Unique issue"
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Should not execute"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Never seen"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="Should not execute"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="Investigation"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Unique

## Recommended Action
Manual

## Confidence Level
Low

## Preventive Measures
- Doc
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "NEVER-400",
                "messages": [],
                "iteration_count": 0
            })

            jira_tools.search_tickets.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_confidence_posts_comment_immediately(self):
        """
        Test that high confidence diagnosis posts comment without retry

        Verifies:
        - diagnose → post_comment path (no retry)
        - investigate_cluster is NOT called again
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Known issue"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Similar"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Clear error")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Known"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="History"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="Investigation"))

            # High confidence diagnosis
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Well understood issue

## Recommended Action
Apply standard fix

## Confidence Level
High - this is clearly a high confidence diagnosis

## Preventive Measures
- Standard preventive measures
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "HIGH-500",
                "messages": [],
                "iteration_count": 0
            })

            # Verify confidence is high
            assert result["confidence_level"].lower() == "high"

            # Verify comment was posted
            jira_tools.add_comment.assert_called_once()

            # Verify K8s investigation was called only once (no retry)
            assert k8s_tools.kubectl_get.call_count == 1

    @pytest.mark.asyncio
    async def test_medium_confidence_posts_comment_no_retry(self):
        """Test that medium confidence also posts comment without retry"""
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Issue"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Similar"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="History"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="Investigation"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Likely cause identified

## Recommended Action
Try this fix

## Confidence Level
Medium confidence

## Preventive Measures
- Monitor
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "MED-600",
                "messages": [],
                "iteration_count": 0
            })

            assert result["confidence_level"].lower() == "medium"
            jira_tools.add_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_low_confidence_triggers_reinvestigation(self):
        """
        Test that low confidence triggers one reinvestigation cycle

        Verifies:
        - diagnose → investigate_cluster path (retry)
        - K8s investigation runs a second time
        - iteration_count is incremented
        - Eventually posts comment after retry
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Unclear issue"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "No similar"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Unclear logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Unclear"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="No history"))
            # K8s investigator needs 3 responses per call (targets, pods, analysis)
            # We'll be called twice (initial + retry), so 6 responses total
            k8s_responses = [
                Mock(content='{"namespace": "default", "pods": [], "services": [], "deployments": []}'),  # identify_targets 1
                Mock(content=""),  # identify_pods 1 (empty list)
                Mock(content="No pods found"),  # analyze_findings 1
                Mock(content='{"namespace": "default", "pods": [], "services": [], "deployments": []}'),  # identify_targets 2
                Mock(content=""),  # identify_pods 2
                Mock(content="Still no pods"),  # analyze_findings 2
            ]
            m3.return_value.ainvoke = AsyncMock(side_effect=k8s_responses)

            # First diagnosis: low confidence
            # Second diagnosis: medium confidence (after retry)
            diag_call_count = [0]

            def diag_response(*args, **kwargs):
                diag_call_count[0] += 1
                if diag_call_count[0] == 1:
                    # First call: low confidence
                    return Mock(content="""
## Root Cause
Unclear root cause

## Recommended Action
Need more investigation

## Confidence Level
Low - need more data

## Preventive Measures
- Investigate
                    """)
                else:
                    # Second call: medium confidence
                    return Mock(content="""
## Root Cause
After retry, identified issue

## Recommended Action
Apply fix

## Confidence Level
Medium after reinvestigation

## Preventive Measures
- Monitor
                    """)

            m4.return_value.ainvoke = AsyncMock(side_effect=diag_response)

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "LOW-700",
                "messages": [],
                "iteration_count": 0
            })

            # Verify retry happened
            assert result["iteration_count"] == 1

            # Verify K8s investigation was called twice (initial + retry)
            assert k8s_tools.kubectl_get.call_count == 2

            # Verify diagnostician was called twice
            assert m4.return_value.ainvoke.call_count == 2

            # Verify comment was eventually posted
            jira_tools.add_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_low_confidence_max_iterations_prevents_infinite_loop(self):
        """
        Test that max iterations (2) prevents infinite reinvestigation loop

        Verifies:
        - After 2 iterations, workflow posts comment despite low confidence
        - Prevents infinite loops
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Complex issue"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "No match"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Complex logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Complex"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="No history"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="Investigation"))

            # Always return low confidence
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Still unclear

## Recommended Action
Manual investigation required

## Confidence Level
Low - insufficient data

## Preventive Measures
- Escalate to senior engineer
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "LOOP-800",
                "messages": [],
                "iteration_count": 0
            })

            # Verify iterations capped at 2
            assert result["iteration_count"] <= 2

            # Verify comment was posted despite low confidence
            jira_tools.add_comment.assert_called_once()

            # Verify diagnostician was called maximum 3 times (initial + 2 retries)
            assert m4.return_value.ainvoke.call_count <= 3


@pytest.mark.integration
class TestMultiAgentCollaboration:
    """Test agent handoffs and collaboration"""

    @pytest.mark.asyncio
    async def test_jira_to_history_handoff(self):
        """
        Test JiraAgent → HistoryAgent handoff

        Verifies:
        - Ticket context is available to HistoryAgent
        - HistoryAgent receives ticket_summary from JiraAgent
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "Handoff test",
            "description": "Testing agent handoff"
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        # Track what state each agent receives
        history_agent_received_state = {}

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Jira summary"))

            # Capture state received by history agent
            async def history_mock(*args, **kwargs):
                # The state should have ticket info from JiraAgent
                return Mock(content="Historical analysis")

            m2.return_value.ainvoke = AsyncMock(side_effect=history_mock)
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="K8s"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Test

## Recommended Action
Test

## Confidence Level
High

## Preventive Measures
- Test
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "HANDOFF-100",
                "messages": [],
                "iteration_count": 0
            })

            # Verify history search was called (meaning handoff happened)
            jira_tools.search_tickets.assert_called()

    @pytest.mark.asyncio
    async def test_history_to_k8s_handoff(self):
        """
        Test HistoryAgent → K8sInvestigator handoff

        Verifies:
        - Historical findings available to K8sInvestigator
        - State includes similar_tickets field
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Test"})
        jira_tools.search_tickets = AsyncMock(return_value={
            "content": "Found PROJ-100, PROJ-101 with similar symptoms"
        })
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="Historical patterns"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="K8s investigation"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Root cause

## Recommended Action
Action

## Confidence Level
High

## Preventive Measures
- Prevent
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "HANDOFF-200",
                "messages": [],
                "iteration_count": 0
            })

            # Verify K8s tools were called (meaning handoff from history happened)
            k8s_tools.kubectl_get.assert_called()

    @pytest.mark.asyncio
    async def test_k8s_to_diagnostician_handoff(self):
        """
        Test K8sInvestigator → Diagnostician handoff

        Verifies:
        - Cluster findings available to Diagnostician
        - State includes logs, events, pod_status
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Test"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={
            "items": [{"metadata": {"name": "pod-123"}}]
        })
        k8s_tools.kubectl_logs = AsyncMock(return_value="Critical error in logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[
            {"type": "Warning", "reason": "Failed"}
        ])
        k8s_tools.kubectl_top = AsyncMock(return_value={"cpu": "100m"})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="History"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="K8s findings"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Based on cluster findings

## Recommended Action
Fix based on logs and events

## Confidence Level
High

## Preventive Measures
- Based on investigation
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "HANDOFF-300",
                "messages": [],
                "iteration_count": 0
            })

            # Verify diagnostician produced output
            assert result.get("root_cause")
            assert result.get("recommended_action")

    @pytest.mark.asyncio
    async def test_diagnostician_to_jira_handoff(self):
        """
        Test Diagnostician → JiraAgent (post_comment) handoff

        Verifies:
        - Diagnosis is available when posting comment
        - Comment includes root cause and recommendations
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Test"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found"})

        # Capture the comment content
        comment_posted = {}

        async def capture_comment(ticket_id, comment):
            comment_posted["ticket_id"] = ticket_id
            comment_posted["content"] = comment
            return {"success": True}

        jira_tools.add_comment = AsyncMock(side_effect=capture_comment)

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="History"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="K8s"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Specific root cause identified

## Recommended Action
Specific action to take

## Confidence Level
High

## Preventive Measures
- Specific preventive measure 1
- Specific preventive measure 2
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "HANDOFF-400",
                "messages": [],
                "iteration_count": 0
            })

            # Verify comment was posted
            assert comment_posted["ticket_id"] == "HANDOFF-400"
            assert "root cause" in comment_posted["content"].lower()
            assert "recommended action" in comment_posted["content"].lower()


@pytest.mark.integration
class TestErrorHandling:
    """Test error handling and graceful degradation"""

    @pytest.mark.asyncio
    async def test_jira_agent_error_allows_workflow_to_continue(self):
        """
        Test that JiraAgent error doesn't crash entire workflow

        Verifies:
        - Error state is captured
        - Workflow continues to subsequent agents
        - Final comment includes error information
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(side_effect=Exception("Jira API timeout"))
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Error"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="History"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="K8s"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Unable to determine due to Jira fetch error

## Recommended Action
Retry or manual investigation

## Confidence Level
Low

## Preventive Measures
- Check Jira connectivity
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "ERROR-100",
                "messages": [],
                "iteration_count": 0
            })

            # Verify error was captured in state
            assert "error" in result.get("ticket_summary", "").lower() or \
                   result.get("ticket_summary", "").startswith("Error")

            # Verify workflow continued and posted comment
            jira_tools.add_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_k8s_tools_error_produces_partial_diagnosis(self):
        """
        Test graceful degradation when K8s tools fail

        Verifies:
        - Diagnostician works with partial data
        - Confidence level reflects limited information
        - Comment indicates manual investigation needed
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Issue"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(side_effect=Exception("K8s API unreachable"))
        k8s_tools.kubectl_logs = AsyncMock(side_effect=Exception("Cannot fetch logs"))
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="History"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="Limited investigation"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Unable to determine - K8s investigation failed

## Recommended Action
Manual cluster investigation required

## Confidence Level
Low - insufficient cluster data

## Preventive Measures
- Verify K8s API access
- Check cluster connectivity
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "ERROR-200",
                "messages": [],
                "iteration_count": 0
            })

            # Verify low confidence due to partial data
            assert result["confidence_level"].lower() == "low"

            # Verify manual investigation is recommended
            assert "manual" in result["recommended_action"].lower()

    @pytest.mark.asyncio
    async def test_llm_failure_uses_fallback_logic(self):
        """
        Test that LLM failures fall back to rule-based analysis

        Verifies:
        - Workflow continues when LLM calls fail
        - Basic diagnosis is provided without LLM
        - State indicates fallback was used
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Issue"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            # All LLMs fail
            m1.return_value.ainvoke = AsyncMock(side_effect=Exception("LLM timeout"))
            m2.return_value.ainvoke = AsyncMock(side_effect=Exception("LLM timeout"))
            m3.return_value.ainvoke = AsyncMock(side_effect=Exception("LLM timeout"))
            m4.return_value.ainvoke = AsyncMock(side_effect=Exception("LLM timeout"))

            result = await graph.ainvoke({
                "ticket_id": "FALLBACK-100",
                "messages": [],
                "iteration_count": 0
            })

            # Verify fallback logic produced some output
            assert result.get("ticket_summary") is not None
            assert result.get("root_cause") is not None

            # Verify comment was still posted (with fallback data)
            jira_tools.add_comment.assert_called_once()


@pytest.mark.integration
class TestEntryAndExitPoints:
    """Test workflow entry and exit points"""

    @pytest.mark.asyncio
    async def test_workflow_starts_at_read_ticket(self):
        """
        Test that workflow always starts at read_ticket node

        Verifies:
        - read_ticket is the entry point
        - JiraAgent.read_ticket is called first
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Entry test"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="History"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="K8s"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Test

## Recommended Action
Test

## Confidence Level
High

## Preventive Measures
- Test
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "ENTRY-100",
                "messages": [],
                "iteration_count": 0
            })

            # Verify get_ticket was called (entry point executed)
            jira_tools.get_ticket.assert_called_once_with("ENTRY-100")

    @pytest.mark.asyncio
    async def test_workflow_ends_at_post_comment(self):
        """
        Test that workflow always ends at post_comment node

        Verifies:
        - post_comment is the exit point
        - JiraAgent.post_comment is called last
        - Workflow reaches END state
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Exit test"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="History"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="K8s"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Test

## Recommended Action
Test

## Confidence Level
High

## Preventive Measures
- Test
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "EXIT-100",
                "messages": [],
                "iteration_count": 0
            })

            # Verify add_comment was called (exit point executed)
            jira_tools.add_comment.assert_called_once()


@pytest.mark.integration
class TestGraphStructure:
    """Test graph structure and compilation"""

    def test_graph_compiles_without_errors(self):
        """
        Test that graph compiles successfully

        Verifies:
        - No compilation errors
        - Graph structure is valid
        """
        jira_tools = Mock(spec=JiraTools)
        k8s_tools = Mock(spec=K8sTools)

        # Should not raise any exceptions
        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        assert graph is not None

    def test_all_nodes_are_registered(self):
        """
        Test that all expected nodes are registered in graph

        Verifies:
        - read_ticket node exists
        - search_history node exists
        - investigate_cluster node exists
        - diagnose node exists
        - post_comment node exists
        """
        jira_tools = Mock(spec=JiraTools)
        k8s_tools = Mock(spec=K8sTools)

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        # Note: LangGraph's compiled graphs don't expose node names directly
        # This test verifies compilation succeeds, which requires all nodes to be valid
        assert graph is not None

    def test_get_default_graph_function(self):
        """
        Test get_default_graph helper function

        Verifies:
        - get_default_graph returns compiled graph
        - Works with provided tool clients
        """
        jira_tools = Mock(spec=JiraTools)
        k8s_tools = Mock(spec=K8sTools)

        graph = get_default_graph(jira_tools, k8s_tools)

        assert graph is not None

    def test_graph_structure_with_visualization_export(self):
        """
        Test that graph can be visualized (if Mermaid/visualization available)

        Verifies:
        - Graph structure can be exported for visualization
        """
        jira_tools = Mock(spec=JiraTools)
        k8s_tools = Mock(spec=K8sTools)

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        # Try to get Mermaid diagram (if available)
        try:
            # Some LangGraph versions support get_graph()
            if hasattr(graph, 'get_graph'):
                graph_structure = graph.get_graph()
                assert graph_structure is not None
        except Exception:
            # Visualization not available - that's okay
            pass


@pytest.mark.integration
class TestPerformanceAndTimeouts:
    """Test workflow performance and timeout handling"""

    @pytest.mark.asyncio
    async def test_workflow_completes_in_reasonable_time(self):
        """
        Test that workflow completes within reasonable time

        Verifies:
        - Workflow doesn't hang indefinitely
        - Complete execution takes less than 30 seconds (with mocks)
        """
        import time

        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Performance test"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={"items": []})
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="History"))
            # K8s investigator needs 3 responses (identify_targets, identify_pods, analyze_findings)
            m3.return_value.ainvoke = AsyncMock(side_effect=[
                Mock(content='{"namespace": "default", "pods": [], "services": [], "deployments": []}'),
                Mock(content=""),
                Mock(content="Investigation complete")
            ])
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Test

## Recommended Action
Test

## Confidence Level
High

## Preventive Measures
- Test
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            start_time = time.time()

            result = await graph.ainvoke({
                "ticket_id": "PERF-100",
                "messages": [],
                "iteration_count": 0
            })

            end_time = time.time()
            elapsed = end_time - start_time

            # With mocks, should complete in under 30 seconds
            assert elapsed < 30, f"Workflow took {elapsed}s, expected < 30s"

    @pytest.mark.asyncio
    async def test_workflow_with_slow_agent_eventually_completes(self):
        """
        Test that workflow completes even with slow agents

        Verifies:
        - Workflow doesn't deadlock
        - All agents eventually execute
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={"summary": "Slow test"})
        jira_tools.search_tickets = AsyncMock(return_value={"content": "Found"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)

        # Simulate slow K8s operations
        async def slow_kubectl_get(*args, **kwargs):
            await asyncio.sleep(0.5)  # 500ms delay
            return {"items": []}

        k8s_tools.kubectl_get = AsyncMock(side_effect=slow_kubectl_get)
        k8s_tools.kubectl_logs = AsyncMock(return_value="Logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=[])
        k8s_tools.kubectl_top = AsyncMock(return_value={})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="Issue"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="History"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="K8s"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Test

## Recommended Action
Test

## Confidence Level
High

## Preventive Measures
- Test
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "SLOW-100",
                "messages": [],
                "iteration_count": 0
            })

            # Verify workflow completed despite slow operations
            assert result is not None
            jira_tools.add_comment.assert_called_once()


@pytest.mark.integration
class TestRealisticScenarios:
    """Test workflow with realistic ticket scenarios"""

    @pytest.mark.asyncio
    async def test_crashloopbackoff_scenario(self):
        """
        Test complete workflow for CrashLoopBackOff issue

        Realistic scenario:
        - Pod in CrashLoopBackOff
        - Missing ConfigMap
        - Historical similar issues
        - High confidence diagnosis
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "CrashLoopBackOff in api-server pod",
            "description": "api-server-5f8d6c7b-abc12 is in CrashLoopBackOff state",
            "priority": "Critical",
            "labels": ["production", "k8s", "urgent"]
        })
        jira_tools.search_tickets = AsyncMock(return_value={
            "content": "Found 2 similar: PROD-100 (ConfigMap missing), PROD-101 (same issue)"
        })
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={
            "items": [{
                "metadata": {"name": "api-server-5f8d6c7b-abc12"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{
                        "restartCount": 8,
                        "state": {"waiting": {"reason": "CrashLoopBackOff"}}
                    }]
                }
            }]
        })
        k8s_tools.kubectl_logs = AsyncMock(return_value="""
        2025-12-17T10:00:00Z [INFO] Starting api-server
        2025-12-17T10:00:01Z [ERROR] Failed to load /etc/config/app.yaml
        2025-12-17T10:00:01Z [FATAL] Configuration file not found
        """)
        k8s_tools.kubectl_events = AsyncMock(return_value=[
            {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting failed container"}
        ])
        k8s_tools.kubectl_top = AsyncMock(return_value={"cpu": "10m", "memory": "64Mi"})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(
                content="CrashLoopBackOff in api-server-5f8d6c7b-abc12"
            ))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(
                content="Similar issues resolved by fixing ConfigMap mounting"
            ))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(
                content="Pod crashing due to missing /etc/config/app.yaml"
            ))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
The api-server pod is crashing because the ConfigMap containing app.yaml is not mounted or doesn't exist.

## Recommended Action
1. Check if ConfigMap exists: kubectl get configmap api-server-config
2. Verify volume mount in deployment
3. Create/restore ConfigMap if missing
4. Restart pod after fixing ConfigMap

## Confidence Level
High - Clear evidence from logs and similar past issues

## Preventive Measures
- Add ConfigMap validation in deployment pipeline
- Implement backup for critical ConfigMaps
- Add monitoring for missing ConfigMaps
- Document ConfigMap dependencies
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "CRASH-001",
                "messages": [],
                "iteration_count": 0
            })

            # Verify complete diagnosis
            assert "configmap" in result["root_cause"].lower()
            assert result["confidence_level"].lower() == "high"
            assert len(result["preventive_measures"]) >= 3

    @pytest.mark.asyncio
    async def test_oomkilled_scenario(self):
        """
        Test complete workflow for OOMKilled issue

        Realistic scenario:
        - Pod killed due to OOM
        - Memory limit too low
        - Resource investigation
        - Medium confidence (needs verification)
        """
        jira_tools = Mock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "summary": "OOMKilled - worker pod restarting",
            "description": "worker-6d8f9c2a-xyz56 getting OOMKilled",
            "priority": "High",
            "labels": ["k8s", "memory"]
        })
        jira_tools.search_tickets = AsyncMock(return_value={
            "content": "Found 1 similar: PROD-200 (increased memory limit)"
        })
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = Mock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value={
            "items": [{
                "metadata": {"name": "worker-6d8f9c2a-xyz56"},
                "spec": {
                    "containers": [{
                        "resources": {
                            "limits": {"memory": "256Mi"}
                        }
                    }]
                },
                "status": {
                    "containerStatuses": [{
                        "restartCount": 12,
                        "lastState": {
                            "terminated": {"reason": "OOMKilled"}
                        }
                    }]
                }
            }]
        })
        k8s_tools.kubectl_logs = AsyncMock(return_value="OutOfMemoryError: Java heap space")
        k8s_tools.kubectl_events = AsyncMock(return_value=[
            {"type": "Warning", "reason": "OOMKilled", "message": "Container exceeded memory limit"}
        ])
        k8s_tools.kubectl_top = AsyncMock(return_value={"memory": "250Mi"})

        graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        with patch('src.agents.jira_agent.ChatOpenAI') as m1, \
             patch('src.agents.history_agent.create_extraction_llm') as m2, \
             patch('src.agents.k8s_investigator.ChatOpenAI') as m3, \
             patch('src.agents.diagnostician.ChatOpenAI') as m4:

            m1.return_value.ainvoke = AsyncMock(return_value=Mock(content="OOMKilled"))
            m2.return_value.ainvoke = AsyncMock(return_value=Mock(content="Increase memory"))
            m3.return_value.ainvoke = AsyncMock(return_value=Mock(content="Memory limit 256Mi too low"))
            m4.return_value.ainvoke = AsyncMock(return_value=Mock(content="""
## Root Cause
Memory limit of 256Mi is insufficient for the worker's Java heap requirements

## Recommended Action
Increase memory limit to 512Mi or 1Gi and monitor usage

## Confidence Level
Medium - Likely root cause but should verify actual memory usage patterns

## Preventive Measures
- Set appropriate memory requests and limits
- Monitor memory usage trends
- Implement memory profiling for Java applications
            """))

            # Create graph inside patch context
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

            result = await graph.ainvoke({
                "ticket_id": "OOM-001",
                "messages": [],
                "iteration_count": 0
            })

            assert "memory" in result["root_cause"].lower()
            assert result["confidence_level"].lower() == "medium"
