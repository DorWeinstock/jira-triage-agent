"""Tests for LLM re-ranking in history agent."""

import json
import pytest
from unittest.mock import AsyncMock, Mock, MagicMock


class TestLLMReranking:
    """Tests for _llm_rerank_candidates method."""

    @pytest.fixture
    def history_agent(self):
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.history_agent import HistoryAgent

        mock_tools = Mock()
        agent = HistoryAgent(mock_tools)
        return agent

    @pytest.fixture
    def sample_candidates(self):
        return [
            {"key": "SP-100", "summary": "order-service CrashLoopBackOff in prod", "status": "Done", "updated": "2026-01-15", "components": ["order-service"]},
            {"key": "SP-101", "summary": "payment timeout errors", "status": "Open", "updated": "2026-01-10", "components": ["payments"]},
            {"key": "SP-102", "summary": "order-service OOMKilled", "status": "Resolved", "updated": "2025-12-01", "components": ["order-service"]},
        ]

    @pytest.fixture
    def sample_state(self):
        return {
            "ticket_summary": "order-service pods in CrashLoopBackOff",
            "ticket_id": "SP-999",
        }

    @pytest.mark.asyncio
    async def test_rerank_returns_scores(self, history_agent, sample_candidates, sample_state):
        """LLM reranking should return similarity scores for each candidate."""
        llm_response = json.dumps({
            "scores": [
                {"key": "SP-100", "similarity": 90},
                {"key": "SP-101", "similarity": 30},
                {"key": "SP-102", "similarity": 75},
            ]
        })
        mock_response = MagicMock()
        mock_response.content = llm_response
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_response)

        scores = await history_agent._llm_rerank_candidates(sample_candidates, sample_state)

        assert scores == {"SP-100": 90, "SP-101": 30, "SP-102": 75}

    @pytest.mark.asyncio
    async def test_rerank_llm_failure_returns_defaults(self, history_agent, sample_candidates, sample_state):
        """If LLM fails, all candidates get default score of 50."""
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(side_effect=Exception("LLM timeout"))

        scores = await history_agent._llm_rerank_candidates(sample_candidates, sample_state)

        assert all(v == 50 for v in scores.values())
        assert len(scores) == 3

    @pytest.mark.asyncio
    async def test_rerank_malformed_json_returns_defaults(self, history_agent, sample_candidates, sample_state):
        """If LLM returns invalid JSON, all candidates get default score."""
        mock_response = MagicMock()
        mock_response.content = "This is not JSON"
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_response)

        scores = await history_agent._llm_rerank_candidates(sample_candidates, sample_state)

        assert all(v == 50 for v in scores.values())

    @pytest.mark.asyncio
    async def test_rerank_partial_results(self, history_agent, sample_candidates, sample_state):
        """If LLM only scores some candidates, missing ones get default."""
        llm_response = json.dumps({
            "scores": [
                {"key": "SP-100", "similarity": 90},
                # SP-101 and SP-102 missing
            ]
        })
        mock_response = MagicMock()
        mock_response.content = llm_response
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_response)

        scores = await history_agent._llm_rerank_candidates(sample_candidates, sample_state)

        assert scores["SP-100"] == 90
        assert scores["SP-101"] == 50  # Default
        assert scores["SP-102"] == 50  # Default

    @pytest.mark.asyncio
    async def test_rerank_clamps_scores(self, history_agent, sample_candidates, sample_state):
        """Scores outside 0-100 should be clamped."""
        llm_response = json.dumps({
            "scores": [
                {"key": "SP-100", "similarity": 150},
                {"key": "SP-101", "similarity": -20},
                {"key": "SP-102", "similarity": 75},
            ]
        })
        mock_response = MagicMock()
        mock_response.content = llm_response
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_response)

        scores = await history_agent._llm_rerank_candidates(sample_candidates, sample_state)

        assert scores["SP-100"] == 100
        assert scores["SP-101"] == 0
        assert scores["SP-102"] == 75

    @pytest.mark.asyncio
    async def test_rerank_empty_candidates(self, history_agent, sample_state):
        """Empty candidate list should return empty dict."""
        scores = await history_agent._llm_rerank_candidates([], sample_state)
        assert scores == {}

    @pytest.mark.asyncio
    async def test_rerank_markdown_wrapped_json(self, history_agent, sample_candidates, sample_state):
        """LLM may wrap JSON in markdown code blocks; should extract and parse."""
        llm_response = """Here are the similarity scores:
```json
{
    "scores": [
        {"key": "SP-100", "similarity": 90},
        {"key": "SP-101", "similarity": 30},
        {"key": "SP-102", "similarity": 75}
    ]
}
```
Done analyzing."""
        mock_response = MagicMock()
        mock_response.content = llm_response
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_response)

        scores = await history_agent._llm_rerank_candidates(sample_candidates, sample_state)

        # Should extract JSON from markdown and parse successfully
        assert scores == {"SP-100": 90, "SP-101": 30, "SP-102": 75}

    @pytest.mark.asyncio
    async def test_rerank_markdown_wrapped_json_no_language(self, history_agent, sample_candidates, sample_state):
        """Handle markdown JSON without language specifier."""
        llm_response = """```
{
    "scores": [
        {"key": "SP-100", "similarity": 85}
    ]
}
```"""
        mock_response = MagicMock()
        mock_response.content = llm_response
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_response)

        scores = await history_agent._llm_rerank_candidates(sample_candidates, sample_state)

        assert scores["SP-100"] == 85

