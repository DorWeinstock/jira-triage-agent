"""Tests for the full composite scoring pipeline in history agent."""

import json
import pytest
from unittest.mock import AsyncMock, Mock, MagicMock
from datetime import datetime, timezone, timedelta


class TestCompositeRankedSearch:
    """Tests for _composite_ranked_search replacing _status_ranked_search."""

    @pytest.fixture
    def history_agent(self):
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.history_agent import HistoryAgent

        mock_tools = Mock()
        mock_tools.search_tickets = AsyncMock()
        mock_tools.get_ticket = AsyncMock()
        agent = HistoryAgent(mock_tools)
        return agent

    @pytest.fixture
    def keywords(self):
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.history_agent import SearchKeywords
        return SearchKeywords(
            error_patterns=["CrashLoopBackOff"],
            components=["order-service"],
        )

    @pytest.fixture
    def state_with_components(self):
        return {
            "ticket_id": "SP-999",
            "ticket_summary": "order-service CrashLoopBackOff in production",
            "ticket_components": ["order-service"],
        }

    @pytest.fixture
    def recent_date(self):
        return (datetime.now(timezone.utc) - timedelta(days=10)).strftime(
            "%Y-%m-%dT%H:%M:%S.000+0000"
        )

    @pytest.fixture
    def old_date(self):
        return (datetime.now(timezone.utc) - timedelta(days=300)).strftime(
            "%Y-%m-%dT%H:%M:%S.000+0000"
        )

    @pytest.mark.asyncio
    async def test_component_match_boosts_ranking(
        self, history_agent, keywords, state_with_components, recent_date
    ):
        """Ticket with matching component should rank higher than one without."""
        search_response = {
            "content": f"""Found 2 tickets:
- SP-100 [{recent_date}] (Done) {{order-service}}: same service crash
- SP-101 [{recent_date}] (Done) {{payments}}: different service crash"""
        }
        history_agent.tools.search_tickets = AsyncMock(return_value=search_response)
        history_agent.tools.get_ticket = AsyncMock(return_value={
            "content": "Summary: test\nResolution: Fixed"
        })

        # LLM gives both same similarity score
        llm_response = json.dumps({
            "scores": [
                {"key": "SP-100", "similarity": 80},
                {"key": "SP-101", "similarity": 80},
            ]
        })
        mock_resp = MagicMock()
        mock_resp.content = llm_response
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_resp)

        result = await history_agent._composite_ranked_search(keywords, state_with_components)

        assert len(result) >= 1
        # SP-100 should rank first (component match boost)
        assert result[0]["key"] == "SP-100"

    @pytest.mark.asyncio
    async def test_filters_current_ticket(
        self, history_agent, keywords, state_with_components, recent_date
    ):
        """Current ticket should be excluded from results."""
        search_response = {
            "content": f"""Found 2 tickets:
- SP-999 [{recent_date}] (OPEN): current ticket
- SP-100 [{recent_date}] (Done) {{order-service}}: other ticket"""
        }
        history_agent.tools.search_tickets = AsyncMock(return_value=search_response)
        history_agent.tools.get_ticket = AsyncMock(return_value={
            "content": "Summary: test\nResolution: Fixed"
        })

        llm_response = json.dumps({"scores": [{"key": "SP-100", "similarity": 80}]})
        mock_resp = MagicMock()
        mock_resp.content = llm_response
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_resp)

        result = await history_agent._composite_ranked_search(keywords, state_with_components)

        keys = [t["key"] for t in result]
        assert "SP-999" not in keys

    @pytest.mark.asyncio
    async def test_recency_breaks_tie(
        self, history_agent, keywords, state_with_components, recent_date, old_date
    ):
        """When LLM similarity, component, and status are equal, recency breaks tie."""
        search_response = {
            "content": f"""Found 2 tickets:
- SP-200 [{old_date}] (Done) {{order-service}}: old crash
- SP-201 [{recent_date}] (Done) {{order-service}}: recent crash"""
        }
        history_agent.tools.search_tickets = AsyncMock(return_value=search_response)
        history_agent.tools.get_ticket = AsyncMock(return_value={
            "content": "Summary: test\nResolution: Fixed"
        })

        # Same similarity
        llm_response = json.dumps({
            "scores": [
                {"key": "SP-200", "similarity": 80},
                {"key": "SP-201", "similarity": 80},
            ]
        })
        mock_resp = MagicMock()
        mock_resp.content = llm_response
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_resp)

        result = await history_agent._composite_ranked_search(keywords, state_with_components)

        assert len(result) >= 2
        # SP-201 (recent) should rank above SP-200 (old)
        assert result[0]["key"] == "SP-201"

    @pytest.mark.asyncio
    async def test_empty_search_returns_empty(
        self, history_agent, keywords, state_with_components
    ):
        """No search results should return empty list."""
        search_response = {"content": "No tickets found matching criteria"}
        history_agent.tools.search_tickets = AsyncMock(return_value=search_response)

        result = await history_agent._composite_ranked_search(keywords, state_with_components)

        assert result == []

    @pytest.mark.asyncio
    async def test_scored_tickets_include_score_breakdown(
        self, history_agent, keywords, state_with_components, recent_date
    ):
        """Result tickets should include score breakdown fields."""
        search_response = {
            "content": f"""Found 1 tickets:
- SP-100 [{recent_date}] (Done) {{order-service}}: crash"""
        }
        history_agent.tools.search_tickets = AsyncMock(return_value=search_response)
        history_agent.tools.get_ticket = AsyncMock(return_value={
            "content": "Summary: crash\nResolution: Fixed\nComponents: order-service"
        })

        llm_response = json.dumps({"scores": [{"key": "SP-100", "similarity": 85}]})
        mock_resp = MagicMock()
        mock_resp.content = llm_response
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_resp)

        result = await history_agent._composite_ranked_search(keywords, state_with_components)

        assert len(result) == 1
        ticket = result[0]
        assert "final_score" in ticket
        assert "llm_similarity" in ticket
        assert "component_match" in ticket
        assert "status_score" in ticket
        assert "recency_bonus" in ticket
        assert ticket["final_score"] > 0
