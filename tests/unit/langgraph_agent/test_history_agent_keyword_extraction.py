"""Unit tests for keyword extraction in HistoryAgent.

These tests verify that _extract_search_keywords and _fallback_keyword_extraction
correctly extract K8s error patterns and components for JQL search.
"""

import pytest
import json
from unittest.mock import AsyncMock, Mock, MagicMock


class TestKeywordExtraction:
    """Tests for keyword extraction methods."""

    @pytest.fixture
    def history_agent(self):
        """Create a HistoryAgent with mocked dependencies."""
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.history_agent import HistoryAgent

        mock_jira_tools = Mock()
        agent = HistoryAgent(mock_jira_tools)
        return agent

    @pytest.fixture
    def sample_state_with_errors(self):
        """State with clear K8s error patterns."""
        return {
            "ticket_id": "SP-999",
            "ticket_summary": "Pod CrashLoopBackOff in production - order-service",
            "error_messages": ["CrashLoopBackOff", "container failed"],
            "affected_services": ["order-service"],
            "affected_deployments": ["order-service-prod"],
        }

    @pytest.mark.asyncio
    async def test_extract_keywords_llm_success(self, history_agent, sample_state_with_errors):
        """LLM successfully extracts keywords from state."""
        llm_response = {
            "error_patterns": ["CrashLoopBackOff", "ImagePullBackOff"],
            "components": ["order-service", "payment-service"],
            "symptoms": ["container restart", "failed to pull image"]
        }
        mock_response = MagicMock()
        mock_response.content = json.dumps(llm_response)
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_response)

        keywords = await history_agent._extract_search_keywords(sample_state_with_errors)

        assert keywords.error_patterns == ["CrashLoopBackOff", "ImagePullBackOff"]
        assert keywords.components == ["order-service", "payment-service"]
        assert keywords.symptoms == ["container restart", "failed to pull image"]

    @pytest.mark.asyncio
    async def test_extract_keywords_llm_markdown_wrapped(self, history_agent, sample_state_with_errors):
        """LLM returns JSON wrapped in markdown code blocks (content starts with backticks)."""
        json_content = '{"error_patterns": ["CrashLoopBackOff"], "components": ["order-service"], "symptoms": ["restart loop"]}'
        # Content MUST start with ``` for the current implementation
        llm_response = f"```json\n{json_content}\n```"
        mock_response = MagicMock()
        mock_response.content = llm_response
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_response)

        keywords = await history_agent._extract_search_keywords(sample_state_with_errors)

        assert keywords.error_patterns == ["CrashLoopBackOff"]
        assert keywords.components == ["order-service"]
        assert keywords.symptoms == ["restart loop"]

    @pytest.mark.asyncio
    async def test_extract_keywords_llm_failure_fallback(self, history_agent, sample_state_with_errors):
        """LLM failure triggers fallback keyword extraction."""
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(side_effect=Exception("LLM timeout"))

        keywords = await history_agent._extract_search_keywords(sample_state_with_errors)

        # Should extract from affected_services and affected_deployments
        assert "order-service" in keywords.components
        assert "order-service-prod" in keywords.components
        # CrashLoopBackOff is in summary, should be matched
        assert "CrashLoopBackOff" in keywords.error_patterns

    def test_fallback_keyword_extraction_with_errors(self, history_agent, sample_state_with_errors):
        """Fallback extraction detects K8s errors from ticket summary."""
        keywords = history_agent._fallback_keyword_extraction(sample_state_with_errors)

        # CrashLoopBackOff is in the summary (case-insensitive match)
        assert "CrashLoopBackOff" in keywords.error_patterns
        # Components from state
        assert "order-service" in keywords.components
        assert "order-service-prod" in keywords.components

    def test_fallback_keyword_extraction_multiple_errors(self, history_agent):
        """Fallback extraction handles multiple K8s error patterns in summary."""
        state = {
            "ticket_summary": "Pod OOMKilled and ImagePullBackOff in staging",
            "affected_services": ["api-service"],
            "affected_deployments": [],
        }

        keywords = history_agent._fallback_keyword_extraction(state)

        # Both errors should be detected (case-insensitive)
        assert "OOMKilled" in keywords.error_patterns
        assert "ImagePullBackOff" in keywords.error_patterns
        assert "api-service" in keywords.components

    def test_fallback_keyword_extraction_empty_state(self, history_agent):
        """Fallback extraction handles empty or missing fields gracefully."""
        state = {}

        keywords = history_agent._fallback_keyword_extraction(state)

        # Should return empty SearchKeywords, not crash
        assert keywords.error_patterns == []
        assert keywords.components == []
        assert keywords.symptoms == []

    def test_fallback_keyword_extraction_component_limit(self, history_agent):
        """Fallback extraction limits components to top 5."""
        state = {
            "ticket_summary": "Multiple services down",
            "affected_services": [f"service-{i}" for i in range(10)],
            "affected_deployments": [],
        }

        keywords = history_agent._fallback_keyword_extraction(state)

        # Should be limited to 5 components
        assert len(keywords.components) == 5
        assert keywords.components[0] == "service-0"
        assert keywords.components[4] == "service-4"
