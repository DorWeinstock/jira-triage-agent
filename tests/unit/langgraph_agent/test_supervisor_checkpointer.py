"""Tests for supervisor checkpointer integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock


class TestSupervisorCheckpointerIntegration:
    """Test supervisor accepts checkpointer parameter."""

    def test_create_graph_accepts_checkpointer_parameter(self):
        """Should accept optional checkpointer parameter."""
        from langgraph.checkpoint.memory import MemorySaver
        from src.supervisor import create_conditional_supervisor_graph

        jira_tools = MagicMock()
        k8s_tools = MagicMock()
        checkpointer = MemorySaver()

        # Should not raise
        graph = create_conditional_supervisor_graph(
            jira_tools=jira_tools,
            k8s_tools=k8s_tools,
            checkpointer=checkpointer,
        )

        assert graph is not None

    def test_create_graph_works_without_checkpointer(self):
        """Should work without checkpointer (backward compatible)."""
        from src.supervisor import create_conditional_supervisor_graph

        jira_tools = MagicMock()
        k8s_tools = MagicMock()

        # Should not raise
        graph = create_conditional_supervisor_graph(
            jira_tools=jira_tools,
            k8s_tools=k8s_tools,
        )

        assert graph is not None
