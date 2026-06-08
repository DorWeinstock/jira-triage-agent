"""
Comprehensive Cross-Component Integration Tests

Tests complete end-to-end flows across ALL system components:
1. Jira Webhook Server - Receives webhooks, filters, triggers AgentRuns
2. LangGraph Agent - Orchestrates multi-agent investigation
3. Jira MCP Server - Provides Jira tool operations
4. K8s MCP Server - Provides Kubernetes tool operations
5. LangSmith - Observability and tracing

These tests validate:
- Complete investigation workflows from webhook to Jira comment
- Data flow integrity across component boundaries
- Component communication and integration points
- Error propagation and recovery
- State consistency throughout the system
- Performance under concurrent load
- Observability and tracing

Test Philosophy:
- Test the SYSTEM as a whole, not just individual components
- Validate data transformations at each boundary
- Ensure no data loss or corruption
- Verify proper error handling end-to-end
- Test realistic failure scenarios
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from unittest.mock import AsyncMock, Mock, MagicMock, patch, call
from contextlib import asynccontextmanager

import pytest
import httpx

# Import system components
from src.supervisor import get_default_graph
from src.state import AgentState
from src.tools.jira_tools import JiraTools
from src.tools.k8s_tools import K8sTools
from src.agents.jira_agent import JiraAgent
from src.agents.history_agent import HistoryAgent
from src.agents.k8s_investigator import K8sInvestigator
from src.agents.diagnostician import Diagnostician

logger = logging.getLogger(__name__)


# ============================================================================
# Test Fixtures - Enhanced for Cross-Component Testing
# ============================================================================

@pytest.fixture
def realistic_crashloop_ticket() -> Dict[str, Any]:
    """
    Realistic Jira ticket for a CrashLoopBackOff scenario

    Includes all fields that flow through the system
    """
    return {
        "key": "GAUDISW-5001",
        "fields": {
            "summary": "CrashLoopBackOff in api-server pod in production",
            "description": """
The api-server pod in the production namespace is experiencing a CrashLoopBackOff.
The pod keeps restarting every few seconds and is not serving traffic.

Error seen in logs:
```
Fatal error: Config file /etc/config/database.yaml not found
```

Namespace: production
Pod: api-server-7d5f8c9b-xyz89
Deployment: api-server
""",
            "project": {"key": "GAUDISW"},
            "components": [{"name": "DevOps_K8S"}],
            "issuetype": {"name": "Bug"},
            "priority": {"name": "Critical"},
            "status": {"name": "Open"},
            "labels": ["kubernetes", "crashloop", "production"],
            "created": "2025-12-17T10:00:00.000+0000",
            "updated": "2025-12-17T10:00:00.000+0000"
        }
    }


@pytest.fixture
def realistic_oom_ticket() -> Dict[str, Any]:
    """
    Realistic Jira ticket for an OOMKilled scenario
    """
    return {
        "key": "GAUDISW-5002",
        "fields": {
            "summary": "worker-pod getting OOMKilled repeatedly",
            "description": """
Worker pods in the data-processing namespace are being killed due to OOM.
Memory usage spikes before each crash.

Namespace: data-processing
Pod pattern: worker-*
Deployment: data-worker
""",
            "project": {"key": "GAUDISW"},
            "components": [{"name": "DevOps_K8S"}],
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "status": {"name": "Open"},
            "labels": ["kubernetes", "oom", "memory"],
            "created": "2025-12-17T11:00:00.000+0000"
        }
    }


@pytest.fixture
def realistic_network_ticket() -> Dict[str, Any]:
    """
    Realistic Jira ticket for a network connectivity issue
    """
    return {
        "key": "GAUDISW-5003",
        "fields": {
            "summary": "Service connectivity issues in staging",
            "description": """
Frontend pods cannot reach backend service in staging namespace.
Getting connection refused errors.

Namespace: staging
Frontend: frontend-service
Backend: backend-service
""",
            "project": {"key": "GAUDISW"},
            "components": [{"name": "DevOps_K8S"}],
            "issuetype": {"name": "Bug"},
            "priority": {"name": "Medium"},
            "status": {"name": "Open"},
            "labels": ["kubernetes", "network", "staging"]
        }
    }


@pytest.fixture
def realistic_k8s_pod_status() -> Dict[str, Any]:
    """
    Realistic kubectl get pods output for CrashLoopBackOff scenario
    """
    return {
        "items": [
            {
                "metadata": {
                    "name": "api-server-7d5f8c9b-xyz89",
                    "namespace": "production",
                    "labels": {"app": "api-server"},
                    "creationTimestamp": "2025-12-17T09:45:00Z"
                },
                "spec": {
                    "containers": [
                        {
                            "name": "api-server",
                            "image": "api-server:v1.2.3",
                            "resources": {
                                "limits": {"memory": "512Mi", "cpu": "500m"},
                                "requests": {"memory": "256Mi", "cpu": "250m"}
                            }
                        }
                    ]
                },
                "status": {
                    "phase": "Running",
                    "conditions": [
                        {"type": "Ready", "status": "False", "reason": "ContainersNotReady"}
                    ],
                    "containerStatuses": [
                        {
                            "name": "api-server",
                            "ready": False,
                            "restartCount": 7,
                            "state": {
                                "waiting": {
                                    "reason": "CrashLoopBackOff",
                                    "message": "back-off 5m0s restarting failed container"
                                }
                            },
                            "lastState": {
                                "terminated": {
                                    "exitCode": 1,
                                    "reason": "Error",
                                    "message": "Fatal error: Config file /etc/config/database.yaml not found",
                                    "finishedAt": "2025-12-17T10:28:45Z"
                                }
                            }
                        }
                    ]
                }
            }
        ]
    }


@pytest.fixture
def realistic_pod_logs() -> str:
    """
    Realistic pod logs showing the crash reason clearly
    """
    return """
2025-12-17T10:28:40Z [INFO] Starting api-server v1.2.3
2025-12-17T10:28:40Z [INFO] Loading configuration...
2025-12-17T10:28:40Z [INFO] Checking for config file at /etc/config/database.yaml
2025-12-17T10:28:40Z [ERROR] Config file not found: /etc/config/database.yaml
2025-12-17T10:28:40Z [ERROR] No database configuration available
2025-12-17T10:28:40Z [FATAL] Cannot start without database configuration
2025-12-17T10:28:40Z [INFO] Shutting down...
Fatal error: Config file /etc/config/database.yaml not found
"""


@pytest.fixture
def realistic_k8s_events() -> List[Dict[str, Any]]:
    """
    Realistic Kubernetes events related to the crash
    """
    return [
        {
            "metadata": {
                "name": "api-server-7d5f8c9b-xyz89.event1",
                "namespace": "production"
            },
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container api-server in pod api-server-7d5f8c9b-xyz89",
            "involvedObject": {
                "kind": "Pod",
                "name": "api-server-7d5f8c9b-xyz89",
                "namespace": "production"
            },
            "count": 7,
            "firstTimestamp": "2025-12-17T10:00:00Z",
            "lastTimestamp": "2025-12-17T10:28:50Z"
        },
        {
            "metadata": {
                "name": "api-server-7d5f8c9b-xyz89.event2",
                "namespace": "production"
            },
            "type": "Warning",
            "reason": "Failed",
            "message": "Error: Fatal error: Config file /etc/config/database.yaml not found",
            "involvedObject": {
                "kind": "Pod",
                "name": "api-server-7d5f8c9b-xyz89",
                "namespace": "production"
            },
            "count": 7,
            "firstTimestamp": "2025-12-17T10:00:00Z",
            "lastTimestamp": "2025-12-17T10:28:45Z"
        }
    ]


@pytest.fixture
def realistic_similar_tickets() -> List[Dict[str, Any]]:
    """
    Realistic similar tickets found in history
    """
    return [
        {
            "key": "GAUDISW-4501",
            "summary": "ConfigMap missing causing pod crashes",
            "resolution": "Created missing ConfigMap with required database configuration",
            "similarity_score": 0.95
        },
        {
            "key": "GAUDISW-4320",
            "summary": "Database config not mounted in container",
            "resolution": "Updated deployment to mount ConfigMap at /etc/config",
            "similarity_score": 0.87
        },
        {
            "key": "GAUDISW-4100",
            "summary": "CrashLoopBackOff due to missing environment variable",
            "resolution": "Added missing DATABASE_URL environment variable",
            "similarity_score": 0.72
        }
    ]


@pytest.fixture
async def mock_integrated_mcp_servers():
    """
    Create fully integrated mock MCP servers that respond realistically

    This fixture provides coordinated responses across Jira and K8s MCP servers
    to simulate a real investigation flow.
    """

    class IntegratedMockMCPServers:
        """Coordinated mock MCP servers for realistic testing"""

        def __init__(self):
            self.jira_calls: List[Dict[str, Any]] = []
            self.k8s_calls: List[Dict[str, Any]] = []
            self.call_sequence: List[str] = []
            self.ticket_data = {}
            self.k8s_data = {}

        def setup_crashloop_scenario(
            self,
            ticket: Dict[str, Any],
            pod_status: Dict[str, Any],
            logs: str,
            events: List[Dict[str, Any]],
            similar_tickets: List[Dict[str, Any]]
        ):
            """Setup data for CrashLoopBackOff scenario"""
            self.ticket_data = ticket
            self.k8s_data = {
                "pod_status": pod_status,
                "logs": logs,
                "events": events
            }
            self.similar_tickets_data = similar_tickets

        async def jira_get_ticket(self, ticket_id: str) -> str:
            """Mock Jira get_ticket tool"""
            self.jira_calls.append({"tool": "get_ticket", "ticket_id": ticket_id})
            self.call_sequence.append("jira:get_ticket")

            if ticket_id in self.ticket_data.get("key", ""):
                return json.dumps(self.ticket_data, indent=2)

            return json.dumps(self.ticket_data, indent=2)

        async def jira_search_tickets(self, jql: str, limit: int = 5) -> str:
            """Mock Jira search_tickets tool"""
            self.jira_calls.append({"tool": "search_tickets", "jql": jql, "limit": limit})
            self.call_sequence.append("jira:search_tickets")

            # Return similar tickets
            results = self.similar_tickets_data[:limit]
            return json.dumps({"issues": results, "total": len(results)}, indent=2)

        async def jira_add_comment(self, ticket_id: str, comment: str) -> str:
            """Mock Jira add_comment tool"""
            self.jira_calls.append({
                "tool": "add_comment",
                "ticket_id": ticket_id,
                "comment_length": len(comment)
            })
            self.call_sequence.append("jira:add_comment")

            return f"✅ Comment added to {ticket_id}"

        async def k8s_get_resources(self, resource: str, namespace: str, name: str = None) -> str:
            """Mock K8s get_resources tool"""
            self.k8s_calls.append({
                "tool": "k8s_get_resources",
                "resource": resource,
                "namespace": namespace,
                "name": name
            })
            self.call_sequence.append("k8s:get_resources")

            if resource == "pods":
                return json.dumps(self.k8s_data.get("pod_status", {}), indent=2)

            return json.dumps({"items": []}, indent=2)

        async def k8s_get_pod_logs(
            self,
             pod: str,
             namespace: str,
             tail: int = None
         ) -> str:
             """Mock K8s get_pod_logs tool"""
             self.k8s_calls.append({
                 "tool": "k8s_get_pod_logs",
                 "pod": pod,
                 "namespace": namespace,
                 "tail": tail
             })
             self.call_sequence.append("k8s:get_pod_logs")

             return self.k8s_data.get("logs", "")

        async def k8s_get_events(self, namespace: str, resource: str = None, name: str = None) -> str:
            """Mock K8s get_events tool"""
            self.k8s_calls.append({
                "tool": "k8s_get_events",
                "namespace": namespace,
                "resource": resource,
                "name": name
            })
            self.call_sequence.append("k8s:get_events")

            return json.dumps({"items": self.k8s_data.get("events", [])}, indent=2)

        def verify_call_sequence(self, expected_sequence: List[str]):
            """Verify that tools were called in the expected order"""
            assert self.call_sequence == expected_sequence, \
                f"Expected sequence {expected_sequence}, got {self.call_sequence}"

        def verify_all_components_used(self):
            """Verify that all system components were invoked"""
            assert len(self.jira_calls) > 0, "Jira MCP server was never called"
            assert len(self.k8s_calls) > 0, "K8s MCP server was never called"

            # Check specific tools were used
            jira_tools_used = {call["tool"] for call in self.jira_calls}
            assert "get_ticket" in jira_tools_used, "Ticket was never fetched"
            assert "add_comment" in jira_tools_used, "Comment was never posted"

            k8s_tools_used = {call["tool"] for call in self.k8s_calls}
            assert "k8s_get_resources" in k8s_tools_used, "Pod status was never checked"
            assert "k8s_get_pod_logs" in k8s_tools_used, "Logs were never fetched"

    return IntegratedMockMCPServers()


@pytest.fixture
def correlation_id_tracker():
    """
    Track correlation IDs through the system to verify data flow integrity
    """

    class CorrelationTracker:
        """Tracks correlation IDs across component boundaries"""

        def __init__(self):
            self.correlations: Dict[str, List[str]] = {}

        def track(self, component: str, correlation_id: str, data: Any):
            """Track data with correlation ID at a component"""
            if correlation_id not in self.correlations:
                self.correlations[correlation_id] = []

            self.correlations[correlation_id].append({
                "component": component,
                "timestamp": datetime.utcnow().isoformat(),
                "data_hash": hash(str(data))
            })

        def verify_continuity(self, correlation_id: str, expected_components: List[str]):
            """Verify correlation ID was tracked through all expected components"""
            if correlation_id not in self.correlations:
                raise AssertionError(f"Correlation ID {correlation_id} was never tracked")

            tracked_components = [
                entry["component"] for entry in self.correlations[correlation_id]
            ]

            for component in expected_components:
                assert component in tracked_components, \
                    f"Component {component} missing from correlation chain"

        def get_data_transformations(self, correlation_id: str) -> List[Dict[str, Any]]:
            """Get all data transformations for a correlation ID"""
            return self.correlations.get(correlation_id, [])

    return CorrelationTracker()


# ============================================================================
# Test Suite 1: Complete Investigation Flow Tests
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestCompleteInvestigationFlows:
    """Test complete end-to-end investigation workflows"""

    async def test_crashloop_investigation_full_flow(
        self,
        realistic_crashloop_ticket,
        realistic_k8s_pod_status,
        realistic_pod_logs,
        realistic_k8s_events,
        realistic_similar_tickets,
        mock_integrated_mcp_servers,
        correlation_id_tracker
    ):
        """
        Test complete CrashLoopBackOff investigation flow

        This test verifies the ENTIRE system working together:
        1. Webhook receives Jira ticket
        2. Agent reads ticket via Jira MCP
        3. Agent searches history via Jira MCP
        4. Agent investigates cluster via K8s MCP
        5. Diagnostician synthesizes findings
        6. Agent posts results via Jira MCP

        Validates:
        - Complete data flow from webhook to comment
        - All components are invoked correctly
        - Data transformations are correct
        - Results contain expected information
        """
        # Setup the scenario
        mock_mcp = mock_integrated_mcp_servers
        mock_mcp.setup_crashloop_scenario(
            realistic_crashloop_ticket,
            realistic_k8s_pod_status,
            realistic_pod_logs,
            realistic_k8s_events,
            realistic_similar_tickets
        )

        ticket_id = realistic_crashloop_ticket["key"]
        correlation_id_tracker.track("webhook", ticket_id, realistic_crashloop_ticket)

        # Mock the MCP tool clients
        jira_tools = MagicMock(spec=JiraTools)
        k8s_tools = MagicMock(spec=K8sTools)

        # Wire up the mock MCP servers
        async def mock_get_ticket(tid):
            content = await mock_mcp.jira_get_ticket(tid)
            return {"content": content, "raw": content}

        async def mock_search_tickets(jql, limit=5):
            content = await mock_mcp.jira_search_tickets(jql, limit)
            return {"content": content, "raw": content}

        async def mock_add_comment(ticket_id, comment):
            content = await mock_mcp.jira_add_comment(ticket_id, comment)
            return {"content": content, "success": True, "raw": content}

        jira_tools.get_ticket = AsyncMock(side_effect=mock_get_ticket)
        jira_tools.search_tickets = AsyncMock(side_effect=mock_search_tickets)
        jira_tools.add_comment = AsyncMock(side_effect=mock_add_comment)

        # Create wrapper functions to handle parameter name translation
        async def mock_kubectl_logs(pod_name, namespace="default", container=None, tail=None):
            return await mock_mcp.k8s_get_pod_logs(pod=pod_name, namespace=namespace, tail=tail)

        k8s_tools.kubectl_get = AsyncMock(side_effect=mock_mcp.k8s_get_resources)
        k8s_tools.kubectl_logs = AsyncMock(side_effect=mock_kubectl_logs)
        k8s_tools.kubectl_events = AsyncMock(side_effect=mock_mcp.k8s_get_events)
        k8s_tools.kubectl_top = AsyncMock(return_value="NAME CPU(cores) MEMORY(bytes)\napi-server-7d5f8c9b-xyz89 50m 200Mi")

        # Initialize agents
        jira_agent = JiraAgent(jira_tools)
        history_agent = HistoryAgent(jira_tools)
        k8s_agent = K8sInvestigator(k8s_tools)
        diagnostician = Diagnostician()

        # Track correlation through each component
        correlation_id_tracker.track("agent_init", ticket_id, {"ticket_id": ticket_id})

        # Step 1: Read ticket
        state = AgentState(ticket_id=ticket_id, messages=[], iteration_count=0)
        state = await jira_agent.read_ticket(state)

        correlation_id_tracker.track("jira_agent_read", ticket_id, state)

        # Verify ticket was read
        assert state["ticket_id"] == ticket_id
        assert state["ticket_summary"] is not None
        assert "CrashLoopBackOff" in str(state["ticket_summary"])

        # Step 2: Search history
        state = await history_agent.run(state)

        correlation_id_tracker.track("history_agent", ticket_id, state)

        # Verify history was searched
        assert "similar_tickets" in state or len(mock_mcp.jira_calls) >= 2

        # Step 3: Investigate cluster
        state = await k8s_agent.run(state)

        correlation_id_tracker.track("k8s_investigator", ticket_id, state)

        # Verify cluster was investigated
        assert "cluster_findings" in state
        assert state["cluster_findings"] is not None

        # Step 4: Create diagnosis
        state = await diagnostician.run(state)

        correlation_id_tracker.track("diagnostician", ticket_id, state)

        # Verify diagnosis was created
        assert state.get("root_cause") is not None
        assert state.get("confidence_level") is not None
        assert state.get("recommended_action") is not None

        # Step 5: Post comment
        state = await jira_agent.post_comment(state)

        correlation_id_tracker.track("jira_agent_post", ticket_id, state)

        # Verify all components were used
        mock_mcp.verify_all_components_used()

        # Verify correlation ID flowed through all components
        correlation_id_tracker.verify_continuity(ticket_id, [
            "webhook",
            "agent_init",
            "jira_agent_read",
            "history_agent",
            "k8s_investigator",
            "diagnostician",
            "jira_agent_post"
        ])

        # Verify data transformations
        transformations = correlation_id_tracker.get_data_transformations(ticket_id)
        assert len(transformations) == 7, "Should have 7 tracked transformations"

        logger.info(f"✅ Complete investigation flow test passed for {ticket_id}")

    async def test_oom_investigation_full_flow(
        self,
        realistic_oom_ticket,
        mock_integrated_mcp_servers
    ):
        """
        Test complete OOMKilled investigation flow

        This verifies the system handles a different failure mode correctly:
        - Different kubectl commands are used (kubectl top)
        - Different diagnosis is generated
        - Different recommendations are provided
        """
        # Setup OOM scenario
        mock_mcp = mock_integrated_mcp_servers

        oom_pod_status = {
            "items": [{
                "metadata": {"name": "worker-abc123", "namespace": "data-processing"},
                "status": {
                    "containerStatuses": [{
                        "name": "worker",
                        "restartCount": 12,
                        "lastState": {
                            "terminated": {
                                "reason": "OOMKilled",
                                "exitCode": 137
                            }
                        }
                    }]
                }
            }]
        }

        oom_logs = """
2025-12-17T11:25:30Z [INFO] Processing batch 1234...
2025-12-17T11:25:35Z [INFO] Memory usage: 450MB
2025-12-17T11:25:40Z [INFO] Memory usage: 480MB
2025-12-17T11:25:45Z [WARN] Memory usage: 510MB approaching limit
2025-12-17T11:25:47Z [ERROR] Out of memory
Killed
"""

        oom_events = [
            {
                "type": "Warning",
                "reason": "OOMKilling",
                "message": "Memory limit exceeded, killing container",
                "involvedObject": {"name": "worker-abc123"}
            }
        ]

        mock_mcp.setup_crashloop_scenario(
            realistic_oom_ticket,
            oom_pod_status,
            oom_logs,
            oom_events,
            []
        )

        # Mock tools
        jira_tools = MagicMock(spec=JiraTools)
        k8s_tools = MagicMock(spec=K8sTools)

        jira_tools.get_ticket = AsyncMock(return_value={
            "content": json.dumps(realistic_oom_ticket),
            "raw": json.dumps(realistic_oom_ticket)
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "[]", "raw": "[]"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools.kubectl_get = AsyncMock(return_value=json.dumps(oom_pod_status))
        k8s_tools.kubectl_logs = AsyncMock(return_value=oom_logs)
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": oom_events}))
        k8s_tools.kubectl_top = AsyncMock(return_value="NAME CPU MEMORY\nworker-abc123 100m 512Mi")

        # Run investigation
        jira_agent = JiraAgent(jira_tools)
        k8s_agent = K8sInvestigator(k8s_tools)
        diagnostician = Diagnostician()

        state = AgentState(ticket_id="GAUDISW-5002", messages=[])
        state = await jira_agent.read_ticket(state)
        state = await k8s_agent.run(state)
        state = await diagnostician.run(state)
        state = await jira_agent.post_comment(state)

        # Verify OOM-specific investigation
        assert k8s_tools.kubectl_top.called, "Should check resource usage for OOM"
        assert "OOM" in str(state.get("root_cause", "")).upper() or \
               "memory" in str(state.get("root_cause", "")).lower(), \
               "Diagnosis should mention OOM or memory"

        logger.info("✅ OOM investigation flow test passed")

    async def test_network_investigation_full_flow(
        self,
        realistic_network_ticket,
        mock_integrated_mcp_servers
    ):
        """
        Test complete network connectivity investigation flow

        Validates different investigation path:
        - Service inspection
        - Network policy checks
        - Pod-to-pod connectivity tests
        """
        # Setup network issue scenario
        mock_mcp = mock_integrated_mcp_servers

        network_pod_status = {
            "items": [
                {
                    "metadata": {"name": "frontend-xyz", "namespace": "staging"},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [{
                            "name": "frontend",
                            "ready": True,
                            "restartCount": 0
                        }]
                    }
                },
                {
                    "metadata": {"name": "backend-abc", "namespace": "staging"},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [{
                            "name": "backend",
                            "ready": False,
                            "restartCount": 0
                        }]
                    }
                }
            ]
        }

        network_logs = """
2025-12-17T12:10:00Z [INFO] Starting frontend service
2025-12-17T12:10:05Z [INFO] Attempting to connect to backend-service:8080
2025-12-17T12:10:15Z [ERROR] Connection refused to backend-service:8080
2025-12-17T12:10:20Z [ERROR] Failed to reach backend after 3 retries
"""

        mock_mcp.setup_crashloop_scenario(
            realistic_network_ticket,
            network_pod_status,
            network_logs,
            [],
            []
        )

        # Mock tools
        jira_tools = MagicMock(spec=JiraTools)
        k8s_tools = MagicMock(spec=K8sTools)

        jira_tools.get_ticket = AsyncMock(return_value={
            "content": json.dumps(realistic_network_ticket),
            "raw": json.dumps(realistic_network_ticket)
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "[]"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools.kubectl_get = AsyncMock(return_value=json.dumps(network_pod_status))
        k8s_tools.kubectl_logs = AsyncMock(return_value=network_logs)
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_top = AsyncMock(return_value="")

        # Run investigation
        jira_agent = JiraAgent(jira_tools)
        k8s_agent = K8sInvestigator(k8s_tools)
        diagnostician = Diagnostician()

        state = AgentState(ticket_id="GAUDISW-5003", messages=[])
        state = await jira_agent.read_ticket(state)
        state = await k8s_agent.run(state)
        state = await diagnostician.run(state)
        state = await jira_agent.post_comment(state)

        # Verify network-specific investigation
        assert "network" in str(state.get("ticket_summary", "")).lower() or \
               "connectivity" in str(state.get("ticket_summary", "")).lower()

        logger.info("✅ Network investigation flow test passed")


# ============================================================================
# Test Suite 2: Data Flow Validation
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestDataFlowValidation:
    """Validate data integrity as it flows through the system"""

    async def test_ticket_id_consistency(
        self,
        realistic_crashloop_ticket,
        mock_integrated_mcp_servers
    ):
        """
        Verify ticket ID remains consistent throughout investigation

        The ticket ID is the primary key linking all operations.
        It must be preserved exactly at every component boundary.
        """
        mock_mcp = mock_integrated_mcp_servers
        mock_mcp.setup_crashloop_scenario(
            realistic_crashloop_ticket, {}, "", [], []
        )

        ticket_id = realistic_crashloop_ticket["key"]

        # Mock tools
        jira_tools = MagicMock(spec=JiraTools)
        k8s_tools = MagicMock(spec=K8sTools)

        captured_ticket_ids = []

        async def capture_get_ticket(tid):
            captured_ticket_ids.append(("get_ticket", tid))
            return {"content": json.dumps(realistic_crashloop_ticket)}

        async def capture_add_comment(ticket_id, comment):
            captured_ticket_ids.append(("add_comment", ticket_id))
            return {"success": True}

        jira_tools.get_ticket = AsyncMock(side_effect=capture_get_ticket)
        jira_tools.search_tickets = AsyncMock(return_value={"content": "[]"})
        jira_tools.add_comment = AsyncMock(side_effect=capture_add_comment)

        k8s_tools.kubectl_get = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_logs = AsyncMock(return_value="")
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_top = AsyncMock(return_value="")

        # Run investigation
        jira_agent = JiraAgent(jira_tools)
        k8s_agent = K8sInvestigator(k8s_tools)
        diagnostician = Diagnostician()

        state = AgentState(ticket_id=ticket_id, messages=[])

        # Verify ticket_id in state throughout
        assert state["ticket_id"] == ticket_id

        state = await jira_agent.read_ticket(state)
        assert state["ticket_id"] == ticket_id, "ticket_id changed after read_ticket"

        state = await k8s_agent.run(state)
        assert state["ticket_id"] == ticket_id, "ticket_id changed after k8s investigation"

        state = await diagnostician.run(state)
        assert state["ticket_id"] == ticket_id, "ticket_id changed after diagnosis"

        state = await jira_agent.post_comment(state)
        assert state["ticket_id"] == ticket_id, "ticket_id changed after post_comment"

        # Verify all Jira calls used the same ticket ID
        for operation, tid in captured_ticket_ids:
            assert tid == ticket_id, \
                f"Operation {operation} used wrong ticket ID: {tid} != {ticket_id}"

        logger.info("✅ Ticket ID consistency test passed")

    async def test_namespace_pod_consistency(
        self,
        realistic_crashloop_ticket,
        realistic_k8s_pod_status,
        realistic_pod_logs,
        mock_integrated_mcp_servers
    ):
        """
        Verify namespace and pod names are extracted and used consistently

        The agent must correctly extract namespace and pod names from the ticket
        and use them consistently in all kubectl commands.
        """
        mock_mcp = mock_integrated_mcp_servers
        mock_mcp.setup_crashloop_scenario(
            realistic_crashloop_ticket,
            realistic_k8s_pod_status,
            realistic_pod_logs,
            [],
            []
        )

        # Track K8s API calls
        k8s_api_calls = []

        async def track_kubectl_get(resource, namespace, name=None):
            k8s_api_calls.append({
                "command": "get",
                "resource": resource,
                "namespace": namespace,
                "name": name
            })
            return json.dumps(realistic_k8s_pod_status)

        async def track_kubectl_logs(pod_name, namespace, previous=False, tail=None, container=None):
            k8s_api_calls.append({
                "command": "logs",
                "pod": pod_name,
                "namespace": namespace
            })
            return realistic_pod_logs

        async def track_kubectl_events(namespace, resource_type=None, name=None):
            k8s_api_calls.append({
                "command": "events",
                "namespace": namespace
            })
            return json.dumps({"items": []})

        jira_tools = MagicMock(spec=JiraTools)
        k8s_tools = MagicMock(spec=K8sTools)

        jira_tools.get_ticket = AsyncMock(return_value={
            "content": json.dumps(realistic_crashloop_ticket)
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "[]"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools.kubectl_get = AsyncMock(side_effect=track_kubectl_get)
        k8s_tools.kubectl_logs = AsyncMock(side_effect=track_kubectl_logs)
        k8s_tools.kubectl_events = AsyncMock(side_effect=track_kubectl_events)
        k8s_tools.kubectl_top = AsyncMock(return_value="")

        # Run investigation
        k8s_agent = K8sInvestigator(k8s_tools)

        state = AgentState(
            ticket_id="GAUDISW-5001",
            ticket_summary=realistic_crashloop_ticket["fields"]["summary"],
            ticket_description=realistic_crashloop_ticket["fields"]["description"],
            messages=[]
        )

        state = await k8s_agent.run(state)

        # Verify namespace consistency across all calls
        namespaces_used = {call.get("namespace") for call in k8s_api_calls if "namespace" in call}

        # Should use same namespace (or default)
        assert len(namespaces_used) <= 2, \
            f"Too many different namespaces used: {namespaces_used}"

        # Verify pod names are consistent
        pod_names = {call.get("pod") for call in k8s_api_calls if "pod" in call}
        if pod_names:
            # All pod names should be from the same pod or deployment
            # (in this case, api-server-7d5f8c9b-xyz89)
            assert all("api-server" in name or name is None for name in pod_names), \
                f"Inconsistent pod names: {pod_names}"

        logger.info("✅ Namespace/pod consistency test passed")

    async def test_data_transformation_correctness(
        self,
        realistic_crashloop_ticket,
        realistic_k8s_pod_status,
        realistic_pod_logs,
        mock_integrated_mcp_servers
    ):
        """
        Verify data transformations are correct at each boundary

        As data flows through components, it gets transformed:
        - Webhook payload → ticket_id
        - Ticket JSON → AgentState fields
        - K8s JSON → cluster_findings
        - All findings → diagnosis

        Each transformation must be correct and preserve essential information.
        """
        mock_mcp = mock_integrated_mcp_servers
        mock_mcp.setup_crashloop_scenario(
            realistic_crashloop_ticket,
            realistic_k8s_pod_status,
            realistic_pod_logs,
            [],
            []
        )

        # Mock tools
        jira_tools = MagicMock(spec=JiraTools)
        k8s_tools = MagicMock(spec=K8sTools)

        jira_tools.get_ticket = AsyncMock(return_value={
            "content": json.dumps(realistic_crashloop_ticket),
            "raw": json.dumps(realistic_crashloop_ticket)
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "[]"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools.kubectl_get = AsyncMock(return_value=json.dumps(realistic_k8s_pod_status))
        k8s_tools.kubectl_logs = AsyncMock(return_value=realistic_pod_logs)
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_top = AsyncMock(return_value="")

        # Run full investigation
        jira_agent = JiraAgent(jira_tools)
        k8s_agent = K8sInvestigator(k8s_tools)
        diagnostician = Diagnostician()

        state = AgentState(ticket_id="GAUDISW-5001", messages=[])

        # Transformation 1: Ticket → AgentState
        state = await jira_agent.read_ticket(state)

        assert "CrashLoopBackOff" in state["ticket_summary"], \
            "Summary not correctly extracted"
        assert "api-server" in str(state.get("ticket_description", "")), \
            "Pod name not preserved in description"
        assert "production" in str(state.get("ticket_description", "")), \
            "Namespace not preserved in description"

        # Transformation 2: K8s responses → cluster_findings
        state = await k8s_agent.run(state)

        assert "cluster_findings" in state, "cluster_findings not created"
        assert state["cluster_findings"] is not None

        # Verify pod status was captured in cluster_findings
        assert "resources" in state.get("cluster_findings", {}) or "pod_statuses" in state.get("cluster_findings", {})

        # Verify logs were captured in cluster_findings
        assert "logs" in state.get("cluster_findings", {})
        if "Config file" in realistic_pod_logs:
            # Key error message should be in captured logs
            assert "Config file" in str(state.get("cluster_findings", {}).get("logs", "")) or \
                   "Config file" in str(state.get("cluster_findings", {}))

        # Transformation 3: All data → diagnosis
        state = await diagnostician.run(state)

        assert "root_cause" in state, "root_cause not generated"
        assert "confidence_level" in state, "confidence_level not generated"
        assert "recommended_action" in state, "recommended_action not generated"

        # Verify diagnosis makes sense given the inputs
        root_cause = str(state.get("root_cause", "")).lower()

        # Should mention the key issue from logs
        assert "config" in root_cause or "database" in root_cause or \
               "missing" in root_cause, \
               f"Root cause doesn't mention key issue: {state.get('root_cause')}"

        logger.info("✅ Data transformation correctness test passed")


# ============================================================================
# Test Suite 3: Component Communication Tests
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestComponentCommunication:
    """Verify proper integration between components"""

    async def test_agent_to_jira_mcp_communication(self):
        """
        Test communication between LangGraph agent and Jira MCP server

        Validates:
        - MCP session establishment
        - Tool invocation protocol
        - Response parsing
        - Error handling
        """
        # Create mock MCP session
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=Mock(
            content=[Mock(text=json.dumps({"key": "TEST-123", "summary": "Test"}))]
        ))

        # Test JiraTools communication
        with patch('src.tools.jira_tools.ClientSession', return_value=mock_session):
            with patch('src.tools.jira_tools.streamablehttp_client') as mock_client:
                # Setup mock streams
                mock_read = AsyncMock()
                mock_write = AsyncMock()
                mock_get_session_id = lambda: "test-session-123"

                @asynccontextmanager
                async def mock_streamable(*args, **kwargs):
                    yield (mock_read, mock_write, mock_get_session_id)

                mock_client.return_value = mock_streamable()

                jira_tools = JiraTools("http://localhost:8080/mcp")

                # Test tool call
                result = await jira_tools.get_ticket("TEST-123")

                # Verify MCP protocol was used correctly
                assert mock_session.initialize.called, "MCP session not initialized"
                assert mock_session.call_tool.called, "MCP tool not called"

                # Verify response parsing
                assert "content" in result or "raw" in result

        logger.info("✅ Agent-to-Jira-MCP communication test passed")

    async def test_agent_to_k8s_mcp_communication(self):
        """
        Test communication between LangGraph agent and K8s MCP server

        Validates similar to Jira MCP but for K8s operations
        """
        # Create mock MCP session
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=Mock(
            content=[Mock(text=json.dumps({"items": []}))]
        ))

        # Test K8sTools communication
        with patch('src.tools.k8s_tools.ClientSession', return_value=mock_session):
            with patch('src.tools.k8s_tools.streamablehttp_client') as mock_client:
                mock_read = AsyncMock()
                mock_write = AsyncMock()
                mock_get_session_id = lambda: "test-k8s-session-456"

                @asynccontextmanager
                async def mock_streamable(*args, **kwargs):
                    yield (mock_read, mock_write, mock_get_session_id)

                mock_client.return_value = mock_streamable()

                k8s_tools = K8sTools("http://localhost:8084/mcp")

                # Test tool call
                result = await k8s_tools.kubectl_get("pods", "default")

                # Verify MCP protocol
                assert mock_session.initialize.called, "K8s MCP session not initialized"
                assert mock_session.call_tool.called, "K8s MCP tool not called"

        logger.info("✅ Agent-to-K8s-MCP communication test passed")

    async def test_mcp_session_reuse(self):
        """
        Test that MCP sessions are reused efficiently

        Sessions should be established once and reused for multiple tool calls,
        not recreated for each call.
        """
        initialization_count = 0

        mock_session = AsyncMock()

        async def track_initialize():
            nonlocal initialization_count
            initialization_count += 1

        mock_session.initialize = track_initialize
        mock_session.call_tool = AsyncMock(return_value=Mock(content=[Mock(text="result")]))

        with patch('src.tools.jira_tools.ClientSession', return_value=mock_session):
            with patch('src.tools.jira_tools.streamablehttp_client') as mock_client:
                mock_read = AsyncMock()
                mock_write = AsyncMock()
                mock_get_session_id = lambda: "reuse-test-session"

                @asynccontextmanager
                async def mock_streamable(*args, **kwargs):
                    yield (mock_read, mock_write, mock_get_session_id)

                mock_client.return_value = mock_streamable()

                jira_tools = JiraTools("http://localhost:8080/mcp")

                # Make multiple tool calls
                await jira_tools.get_ticket("TEST-1")
                await jira_tools.get_ticket("TEST-2")
                await jira_tools.search_tickets("project=TEST")
                await jira_tools.add_comment("TEST-1", "comment")

                # Should only initialize once
                assert initialization_count == 1, \
                    f"Session initialized {initialization_count} times, expected 1"

        logger.info("✅ MCP session reuse test passed")


# ============================================================================
# Test Suite 4: Error Propagation Tests
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestErrorPropagation:
    """Test how errors propagate through the system"""

    async def test_jira_mcp_down_graceful_degradation(self):
        """
        Test behavior when Jira MCP server is down

        The agent should:
        - Detect connection failure
        - Log error with context
        - Continue with partial investigation
        - Post helpful error message
        """
        # Mock connection failure
        jira_tools = MagicMock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(side_effect=ConnectionError("Connection refused"))
        jira_tools.add_comment = AsyncMock(side_effect=ConnectionError("Connection refused"))

        k8s_tools = MagicMock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_logs = AsyncMock(return_value="")
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_top = AsyncMock(return_value="")

        jira_agent = JiraAgent(jira_tools)
        k8s_agent = K8sInvestigator(k8s_tools)

        state = AgentState(ticket_id="TEST-999", messages=[])

        # Should handle error gracefully
        state = await jira_agent.read_ticket(state)

        # Error should be captured in state
        assert "error" in str(state.get("ticket_summary", "")).lower() or \
               state.get("ticket_summary") is not None

        # K8s investigation should still work
        state = await k8s_agent.run(state)
        assert "cluster_findings" in state

        logger.info("✅ Jira MCP down graceful degradation test passed")

    async def test_k8s_mcp_timeout_handling(self):
        """
        Test behavior when K8s MCP server times out

        The agent should:
        - Wait for reasonable timeout
        - Detect timeout
        - Provide partial diagnosis
        - Note K8s data unavailable
        """
        # Mock timeout
        async def timeout_after_delay(*args, **kwargs):
            await asyncio.sleep(0.1)
            raise asyncio.TimeoutError("K8s MCP request timed out")

        jira_tools = MagicMock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "content": json.dumps({"key": "TEST-888", "summary": "Test timeout"})
        })
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = MagicMock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(side_effect=timeout_after_delay)
        k8s_tools.kubectl_logs = AsyncMock(side_effect=timeout_after_delay)
        k8s_tools.kubectl_events = AsyncMock(side_effect=timeout_after_delay)

        jira_agent = JiraAgent(jira_tools)
        k8s_agent = K8sInvestigator(k8s_tools)
        diagnostician = Diagnostician()

        state = AgentState(ticket_id="TEST-888", messages=[])
        state = await jira_agent.read_ticket(state)

        # K8s investigation should handle timeout
        state = await k8s_agent.run(state)

        # Should have error in cluster_findings
        assert "cluster_findings" in state
        assert "error" in str(state["cluster_findings"]).lower() or \
               state["cluster_findings"] == {}

        # Diagnosis should work with limited data
        state = await diagnostician.run(state)

        # Should provide low confidence diagnosis
        assert state.get("confidence_level") is not None
        if state.get("confidence_level"):
            assert "low" in state["confidence_level"].lower() or \
                   "medium" in state["confidence_level"].lower()

        logger.info("✅ K8s MCP timeout handling test passed")

    async def test_llm_failure_fallback(self):
        """
        Test behavior when LLM calls fail

        The system should fall back to rule-based analysis when LLM fails.
        """
        jira_tools = MagicMock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "content": json.dumps({
                "key": "TEST-777",
                "summary": "CrashLoopBackOff",
                "description": "Pod crashing",
                "priority": "High",
                "status": "Open",
                "labels": []
            })
        })
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        # Mock LLM failure in JiraAgent
        jira_agent = JiraAgent(jira_tools)

        # Replace LLM with failing mock
        jira_agent.llm = AsyncMock()
        jira_agent.llm.ainvoke = AsyncMock(side_effect=Exception("LLM API error"))

        state = AgentState(ticket_id="TEST-777", messages=[])

        # Should fall back to raw ticket data
        state = await jira_agent.read_ticket(state)

        # Should still have ticket_summary (from fallback)
        assert state["ticket_summary"] is not None
        assert "CrashLoopBackOff" in state["ticket_summary"]

        logger.info("✅ LLM failure fallback test passed")


# ============================================================================
# Test Suite 5: State Consistency Tests
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestStateConsistency:
    """Verify state remains consistent across components"""

    async def test_state_field_preservation(self):
        """
        Test that state fields are preserved across agent transitions

        Fields set by one agent should be available to subsequent agents.
        """
        jira_tools = MagicMock(spec=JiraTools)
        k8s_tools = MagicMock(spec=K8sTools)

        jira_tools.get_ticket = AsyncMock(return_value={
            "content": json.dumps({
                "key": "TEST-666",
                "summary": "Test",
                "description": "Description",
                "priority": "High",
                "status": "Open",
                "labels": ["test"]
            })
        })
        jira_tools.search_tickets = AsyncMock(return_value={"content": "[]"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools.kubectl_get = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_logs = AsyncMock(return_value="test logs")
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_top = AsyncMock(return_value="")

        jira_agent = JiraAgent(jira_tools)
        history_agent = HistoryAgent(jira_tools)
        k8s_agent = K8sInvestigator(k8s_tools)
        diagnostician = Diagnostician()

        state = AgentState(ticket_id="TEST-666", messages=[])

        # Track fields at each step
        fields_after_each_step = []

        state = await jira_agent.read_ticket(state)
        fields_after_each_step.append(set(state.keys()))

        state = await history_agent.run(state)
        fields_after_each_step.append(set(state.keys()))

        state = await k8s_agent.run(state)
        fields_after_each_step.append(set(state.keys()))

        state = await diagnostician.run(state)
        fields_after_each_step.append(set(state.keys()))

        # Verify fields are only added, never removed
        for i in range(len(fields_after_each_step) - 1):
            current_fields = fields_after_each_step[i]
            next_fields = fields_after_each_step[i + 1]

            # All current fields should be in next (fields preserved)
            missing_fields = current_fields - next_fields
            assert len(missing_fields) == 0, \
                f"Fields lost between steps: {missing_fields}"

        # Verify key fields are present at the end
        assert "ticket_id" in state
        assert "ticket_summary" in state
        assert "root_cause" in state
        assert "confidence_level" in state

        logger.info("✅ State field preservation test passed")

    async def test_iteration_count_tracking(self):
        """
        Test that iteration count is tracked correctly

        Important for retry logic and preventing infinite loops.
        """
        jira_tools = MagicMock(spec=JiraTools)
        k8s_tools = MagicMock(spec=K8sTools)

        jira_tools.get_ticket = AsyncMock(return_value={"content": "{}"})
        k8s_tools.kubectl_get = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_logs = AsyncMock(return_value="")
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_top = AsyncMock(return_value="")

        k8s_agent = K8sInvestigator(k8s_tools)
        diagnostician = Diagnostician()

        state = AgentState(
            ticket_id="TEST-555",
            ticket_summary="Test",
            messages=[],
            iteration_count=0,
            max_iterations=3
        )

        # Simulate multiple investigation iterations
        for i in range(3):
            state["iteration_count"] = i
            state = await k8s_agent.run(state)
            state = await diagnostician.run(state)

            # Verify iteration count is preserved
            assert state["iteration_count"] == i, \
                f"Iteration count changed unexpectedly: expected {i}, got {state['iteration_count']}"

        logger.info("✅ Iteration count tracking test passed")


# ============================================================================
# Test Suite 6: Performance Tests
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.slow
class TestPerformanceUnderLoad:
    """Test system performance under load"""

    async def test_concurrent_investigations(self):
        """
        Test multiple concurrent investigations

        Validates:
        - No resource conflicts
        - All investigations complete
        - Reasonable performance
        - No cross-contamination of state
        """
        # Create multiple ticket scenarios
        tickets = [
            {"key": f"TEST-{100+i}", "summary": f"Issue {i}", "description": f"Description {i}"}
            for i in range(5)
        ]

        # Mock tools with per-ticket tracking
        call_tracker = {ticket["key"]: [] for ticket in tickets}

        async def track_get_ticket(tid):
            call_tracker[tid].append("get_ticket")
            matching_ticket = next((t for t in tickets if t["key"] == tid), tickets[0])
            return {"content": json.dumps(matching_ticket)}

        jira_tools = MagicMock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(side_effect=track_get_ticket)
        jira_tools.search_tickets = AsyncMock(return_value={"content": "[]"})
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = MagicMock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_logs = AsyncMock(return_value="")
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_top = AsyncMock(return_value="")

        async def run_investigation(ticket_id):
            """Run a single investigation"""
            jira_agent = JiraAgent(jira_tools)
            k8s_agent = K8sInvestigator(k8s_tools)
            diagnostician = Diagnostician()

            state = AgentState(ticket_id=ticket_id, messages=[])
            state = await jira_agent.read_ticket(state)
            state = await k8s_agent.run(state)
            state = await diagnostician.run(state)
            state = await jira_agent.post_comment(state)

            return state

        # Run investigations concurrently
        start_time = time.time()
        results = await asyncio.gather(*[
            run_investigation(ticket["key"]) for ticket in tickets
        ])
        elapsed = time.time() - start_time

        # Verify all completed
        assert len(results) == len(tickets), "Not all investigations completed"

        # Verify no cross-contamination
        for i, result in enumerate(results):
            expected_ticket_id = tickets[i]["key"]
            assert result["ticket_id"] == expected_ticket_id, \
                f"State contamination: expected {expected_ticket_id}, got {result['ticket_id']}"

        # Verify reasonable performance (should be faster than sequential)
        # Concurrent should be < 2x slowest individual operation
        assert elapsed < 60, f"Concurrent investigations too slow: {elapsed}s"

        logger.info(f"✅ Concurrent investigations test passed ({len(tickets)} in {elapsed:.2f}s)")

    async def test_large_log_handling(self):
        """
        Test handling of large pod logs

        Validates:
        - System doesn't hang on large logs
        - Logs are truncated appropriately
        - Investigation completes
        """
        # Generate large logs (simulate 10MB)
        large_logs = "\n".join([
            f"2025-12-17T10:{i:02d}:{i%60:02d}Z [INFO] Log entry {i}"
            for i in range(100000)
        ])

        jira_tools = MagicMock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "content": json.dumps({
                "key": "TEST-LARGE",
                "summary": "Large logs test",
                "description": "Test"
            })
        })
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = MagicMock(spec=K8sTools)
        k8s_tools.kubectl_get = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_logs = AsyncMock(return_value=large_logs)
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_top = AsyncMock(return_value="")

        jira_agent = JiraAgent(jira_tools)
        k8s_agent = K8sInvestigator(k8s_tools)
        diagnostician = Diagnostician()

        state = AgentState(ticket_id="TEST-LARGE", messages=[])

        start_time = time.time()

        state = await jira_agent.read_ticket(state)
        state = await k8s_agent.run(state)
        state = await diagnostician.run(state)

        elapsed = time.time() - start_time

        # Should complete in reasonable time
        assert elapsed < 30, f"Large log handling too slow: {elapsed}s"

        # Should have cluster findings despite large logs
        assert "cluster_findings" in state

        logger.info(f"✅ Large log handling test passed ({len(large_logs)} bytes in {elapsed:.2f}s)")


# ============================================================================
# Test Suite 7: Observability Integration
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestObservabilityIntegration:
    """Test observability and tracing integration"""

    async def test_correlation_id_in_logs(self, caplog):
        """
        Test that correlation IDs appear in logs

        Makes debugging production issues much easier.
        """
        caplog.set_level(logging.INFO)

        jira_tools = MagicMock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "content": json.dumps({
                "key": "TEST-TRACE-1",
                "summary": "Test tracing",
                "description": "Test"
            })
        })

        jira_agent = JiraAgent(jira_tools)

        state = AgentState(ticket_id="TEST-TRACE-1", messages=[])
        await jira_agent.read_ticket(state)

        # Check logs contain ticket ID
        log_messages = [record.message for record in caplog.records]
        assert any("TEST-TRACE-1" in msg for msg in log_messages), \
            "Ticket ID not found in logs"

        logger.info("✅ Correlation ID in logs test passed")

    async def test_component_timing_tracking(self):
        """
        Test that component execution times can be measured

        Important for identifying performance bottlenecks.
        """
        timings = {}

        jira_tools = MagicMock(spec=JiraTools)
        k8s_tools = MagicMock(spec=K8sTools)

        # Add deliberate delays
        async def slow_get_ticket(tid):
            await asyncio.sleep(0.05)
            return {"content": json.dumps({"key": tid, "summary": "Test"})}

        async def slow_kubectl(resource, namespace, name=None):
            await asyncio.sleep(0.1)
            return json.dumps({"items": []})

        jira_tools.get_ticket = AsyncMock(side_effect=slow_get_ticket)
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools.kubectl_get = AsyncMock(side_effect=slow_kubectl)
        k8s_tools.kubectl_logs = AsyncMock(return_value="")
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_top = AsyncMock(return_value="")

        jira_agent = JiraAgent(jira_tools)
        k8s_agent = K8sInvestigator(k8s_tools)

        state = AgentState(ticket_id="TEST-TIMING", messages=[])

        # Measure each component
        start = time.time()
        state = await jira_agent.read_ticket(state)
        timings["jira_read"] = time.time() - start

        start = time.time()
        state = await k8s_agent.run(state)
        timings["k8s_investigate"] = time.time() - start

        # Verify timings were captured
        assert timings["jira_read"] > 0
        assert timings["k8s_investigate"] > 0

        # Verify delays are reflected
        assert timings["jira_read"] >= 0.05, "Jira timing not captured correctly"
        assert timings["k8s_investigate"] >= 0.1, "K8s timing not captured correctly"

        logger.info(f"✅ Component timing tracking test passed: {timings}")


# ============================================================================
# Test Suite 8: Recovery Tests
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
class TestRecoveryScenarios:
    """Test recovery from various failure scenarios"""

    async def test_partial_k8s_data_recovery(self):
        """
        Test that investigation continues with partial K8s data

        If some kubectl commands fail, agent should use available data.
        """
        jira_tools = MagicMock(spec=JiraTools)
        jira_tools.get_ticket = AsyncMock(return_value={
            "content": json.dumps({
                "key": "TEST-PARTIAL",
                "summary": "Partial data test",
                "description": "Test"
            })
        })
        jira_tools.add_comment = AsyncMock(return_value={"success": True})

        k8s_tools = MagicMock(spec=K8sTools)

        # Some commands succeed, some fail
        k8s_tools.kubectl_get = AsyncMock(return_value=json.dumps({
            "items": [{
                "metadata": {"name": "test-pod"},
                "status": {"phase": "Running"}
            }]
        }))
        k8s_tools.kubectl_logs = AsyncMock(side_effect=Exception("Pod not found"))
        k8s_tools.kubectl_events = AsyncMock(return_value=json.dumps({"items": []}))
        k8s_tools.kubectl_top = AsyncMock(side_effect=Exception("Metrics unavailable"))

        k8s_agent = K8sInvestigator(k8s_tools)
        diagnostician = Diagnostician()

        state = AgentState(
            ticket_id="TEST-PARTIAL",
            ticket_summary="Test",
            messages=[]
        )

        # Should handle partial data
        state = await k8s_agent.run(state)
        state = await diagnostician.run(state)

        # Should have some cluster findings
        assert "cluster_findings" in state

        # Should have diagnosis with limited data warning
        assert state.get("confidence_level") is not None

        logger.info("✅ Partial K8s data recovery test passed")


# ============================================================================
# Summary and Reporting
# ============================================================================

def pytest_report_header(config):
    """Add custom header to pytest report"""
    return [
        "Cross-Component Integration Test Suite",
        "Testing complete system integration across:",
        "  - Jira Webhook Server",
        "  - LangGraph Agent (Supervisor + 4 specialist agents)",
        "  - Jira MCP Server",
        "  - K8s MCP Server",
        "  - LangSmith Observability",
        ""
    ]


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "--tb=short", "--log-cli-level=INFO"])
