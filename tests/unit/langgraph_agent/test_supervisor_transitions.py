"""Tests for Jira status transition integration in supervisor workflow."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.tools.jira_tools import JiraTools
from src.tools.k8s_tools import K8sTools


@pytest.fixture
def jira_tools():
    tools = JiraTools(mcp_endpoint="http://fake:8080/mcp/jira")
    tools.move_to_in_progress = AsyncMock(return_value={"success": True})
    tools.move_to_in_review = AsyncMock(return_value={"success": True})
    return tools


@pytest.fixture
def k8s_tools():
    return K8sTools(mcp_endpoint="http://fake:8080/mcp/k8s")


def _make_state(**overrides):
    """Create a minimal valid state for testing."""
    state = {
        "ticket_id": "TEST-123",
        "target_cluster": "local",
        "affected_resources": {"deployments": ["my-app"]},
        "namespace": "default",
        "cluster_findings": {},
        "issue_resolved": False,
        "verification_evidence": [],
    }
    state.update(overrides)
    return state


# ============================================================
# investigate_cluster_wrapper: "In Progress" transition
# ============================================================

@pytest.mark.asyncio
async def test_investigate_wrapper_calls_in_progress(jira_tools, k8s_tools):
    """investigate_cluster_wrapper calls move_to_in_progress before investigation."""
    from src.supervisor import create_conditional_supervisor_graph

    state = _make_state()

    with patch("src.supervisor.K8sInvestigator") as mock_cls:
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=state)
        mock_cls.return_value = mock_agent

        # Build graph to capture the wrapper closures, then invoke directly
        # We need to get the actual wrapper function that was registered
        with patch("src.supervisor.K8sTools"):
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        # Access the investigate_cluster node and invoke it
        node_fn = graph.nodes["investigate_cluster"]
        await node_fn.ainvoke(state)

        jira_tools.move_to_in_progress.assert_called_once_with("TEST-123")
        mock_agent.run.assert_called_once()


@pytest.mark.asyncio
async def test_investigate_wrapper_skips_transition_without_ticket_id(jira_tools, k8s_tools):
    """investigate_cluster_wrapper skips transition when ticket_id is missing."""
    from src.supervisor import create_conditional_supervisor_graph

    state = _make_state(ticket_id=None)

    with patch("src.supervisor.K8sInvestigator") as mock_cls:
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=state)
        mock_cls.return_value = mock_agent

        with patch("src.supervisor.K8sTools"):
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        node_fn = graph.nodes["investigate_cluster"]
        await node_fn.ainvoke(state)

        jira_tools.move_to_in_progress.assert_not_called()


@pytest.mark.asyncio
async def test_investigate_wrapper_continues_on_transition_failure(jira_tools, k8s_tools):
    """Investigation proceeds even if move_to_in_progress returns failure."""
    from src.supervisor import create_conditional_supervisor_graph

    jira_tools.move_to_in_progress = AsyncMock(
        return_value={"success": False, "error": "Jira unreachable"}
    )
    state = _make_state()

    with patch("src.supervisor.K8sInvestigator") as mock_cls:
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=state)
        mock_cls.return_value = mock_agent

        with patch("src.supervisor.K8sTools"):
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        node_fn = graph.nodes["investigate_cluster"]
        result = await node_fn.ainvoke(state)

        # Investigation still ran despite transition failure
        mock_agent.run.assert_called_once()


# ============================================================
# verify_fix_wrapper: "In Review" transition
# ============================================================

@pytest.mark.asyncio
async def test_verify_fix_wrapper_calls_in_review_on_resolved(jira_tools, k8s_tools):
    """verify_fix_wrapper calls move_to_in_review when issue_resolved=True."""
    from src.supervisor import create_conditional_supervisor_graph

    resolved_state = _make_state(issue_resolved=True)

    with patch("src.supervisor.K8sInvestigator") as mock_k8s_cls, \
         patch("src.supervisor.VerificationService") as mock_verify_cls:
        mock_verify = MagicMock()
        mock_verify.verify_fix = AsyncMock(return_value=resolved_state)
        mock_verify_cls.return_value = mock_verify

        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        with patch("src.supervisor.K8sTools"):
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        node_fn = graph.nodes["verify_fix"]
        await node_fn.ainvoke(resolved_state)

        jira_tools.move_to_in_review.assert_called_once_with("TEST-123")


@pytest.mark.asyncio
async def test_verify_fix_wrapper_skips_in_review_when_not_resolved(jira_tools, k8s_tools):
    """verify_fix_wrapper does NOT call move_to_in_review when issue_resolved=False."""
    from src.supervisor import create_conditional_supervisor_graph

    unresolved_state = _make_state(issue_resolved=False)

    with patch("src.supervisor.K8sInvestigator") as mock_k8s_cls, \
         patch("src.supervisor.VerificationService") as mock_verify_cls:
        mock_verify = MagicMock()
        mock_verify.verify_fix = AsyncMock(return_value=unresolved_state)
        mock_verify_cls.return_value = mock_verify

        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        with patch("src.supervisor.K8sTools"):
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        node_fn = graph.nodes["verify_fix"]
        await node_fn.ainvoke(unresolved_state)

        jira_tools.move_to_in_review.assert_not_called()


@pytest.mark.asyncio
async def test_verify_fix_wrapper_skips_in_review_when_resolved_but_no_ticket_id(jira_tools, k8s_tools):
    """verify_fix_wrapper does NOT call move_to_in_review when ticket_id is missing."""
    from src.supervisor import create_conditional_supervisor_graph

    resolved_no_ticket = _make_state(issue_resolved=True, ticket_id=None)

    with patch("src.supervisor.K8sInvestigator") as mock_k8s_cls, \
         patch("src.supervisor.VerificationService") as mock_verify_cls:
        mock_verify = MagicMock()
        mock_verify.verify_fix = AsyncMock(return_value=resolved_no_ticket)
        mock_verify_cls.return_value = mock_verify

        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        with patch("src.supervisor.K8sTools"):
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        node_fn = graph.nodes["verify_fix"]
        await node_fn.ainvoke(resolved_no_ticket)

        jira_tools.move_to_in_review.assert_not_called()


@pytest.mark.asyncio
async def test_verify_fix_wrapper_continues_on_transition_failure(jira_tools, k8s_tools):
    """Verification result is returned even if move_to_in_review returns failure."""
    from src.supervisor import create_conditional_supervisor_graph

    jira_tools.move_to_in_review = AsyncMock(
        return_value={"success": False, "error": "Jira unreachable"}
    )
    resolved_state = _make_state(issue_resolved=True)

    with patch("src.supervisor.K8sInvestigator") as mock_k8s_cls, \
         patch("src.supervisor.VerificationService") as mock_verify_cls:
        mock_verify = MagicMock()
        mock_verify.verify_fix = AsyncMock(return_value=resolved_state)
        mock_verify_cls.return_value = mock_verify

        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s

        with patch("src.supervisor.K8sTools"):
            graph = create_conditional_supervisor_graph(jira_tools, k8s_tools)

        node_fn = graph.nodes["verify_fix"]
        result = await node_fn.ainvoke(resolved_state)

        # Verification still completed despite transition failure
        mock_verify.verify_fix.assert_called_once()
