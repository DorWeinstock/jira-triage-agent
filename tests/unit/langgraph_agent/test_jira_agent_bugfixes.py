"""Tests for Bug 1 (fallback resource extraction) and Bug 2 (description regex).

Bug 1: _extract_resources_with_llm except handlers don't set affected_resources,
       causing should_continue_after_ticket_read to route to post_comment.
Bug 2: _parse_mcp_formatted_response description regex requires newline after label,
       but Go formatTicketOutput outputs inline (Description: text).
"""

import re
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Bug 2: Description regex — _parse_mcp_formatted_response
# ---------------------------------------------------------------------------

class TestDescriptionRegexInline:
    """Go formatTicketOutput emits 'Description: text' inline (no newline).
    _parse_mcp_formatted_response must handle this.
    """

    def _make_agent(self):
        from src.agents.jira_agent import JiraAgent
        mock_tools = MagicMock()
        with patch("src.agents.jira_agent.create_extraction_llm"):
            agent = JiraAgent(jira_tools=mock_tools)
        return agent

    def test_inline_description_parsed(self):
        """Description: text (inline) must be captured."""
        agent = self._make_agent()
        content = (
            "**Ticket Information**\n\n"
            "Key: SP-100\n"
            "Summary: order-service CrashLoopBackOff\n"
            "Description: Pods are crashing in production namespace\n"
            "Resolution: Fixed"
        )
        result = agent._parse_mcp_formatted_response(content)
        assert result["description"] == "Pods are crashing in production namespace"

    def test_newline_description_still_works(self):
        """Description:\\ntext (newline) must still be captured (backward compat)."""
        agent = self._make_agent()
        content = (
            "**Ticket Information**\n\n"
            "Key: SP-100\n"
            "Summary: order-service CrashLoopBackOff\n"
            "Description:\n"
            "Pods are crashing in production namespace\n\n"
            "**Comments**"
        )
        result = agent._parse_mcp_formatted_response(content)
        assert result["description"] == "Pods are crashing in production namespace"

    def test_bold_inline_description(self):
        """**Description:** text (bold inline) must be captured."""
        agent = self._make_agent()
        content = (
            "**Ticket Information**\n\n"
            "Key: SP-100\n"
            "Summary: order-service down\n"
            "**Description:** Pods are crashing\n\n"
            "**Status:** Open"
        )
        result = agent._parse_mcp_formatted_response(content)
        assert result["description"] == "Pods are crashing"

    def test_bold_newline_description_still_works(self):
        """**Description:**\\ntext (bold newline) must still be captured."""
        agent = self._make_agent()
        content = (
            "**Ticket Information**\n\n"
            "Key: SP-100\n"
            "Summary: test\n"
            "**Description:**\n"
            "Multi-line description here\n"
            "with more details\n\n"
            "**Status:** Open"
        )
        result = agent._parse_mcp_formatted_response(content)
        assert "Multi-line description here" in result["description"]

    def test_multiline_inline_description(self):
        """Description: first line\\nsecond line must capture all lines."""
        agent = self._make_agent()
        content = (
            "**Ticket Information**\n\n"
            "Key: SP-100\n"
            "Summary: issue\n"
            "Description: First line of description\n"
            "Second line of description\n"
            "Resolution: Done"
        )
        result = agent._parse_mcp_formatted_response(content)
        assert "First line of description" in result["description"]

    def test_empty_description(self):
        """Description with no text — Go outputs empty string inline."""
        agent = self._make_agent()
        # Go fmt.Sprintf produces "Description: " when description is empty
        content = (
            "**Ticket Information**\n\n"
            "Key: SP-100\n"
            "Summary: issue\n"
            "Description: \n\n"
            "**Comments**"
        )
        result = agent._parse_mcp_formatted_response(content)
        # Empty or whitespace-only is acceptable
        assert result["description"].strip() == ""

    def test_go_format_exact(self):
        """Exact output from Go formatTicketOutput with description."""
        agent = self._make_agent()
        # Matches Go: fmt.Sprintf("**Ticket Information**\n\nKey: %s\nSummary: %s\nDescription: %s", ...)
        content = (
            "**Ticket Information**\n\n"
            "Key: SP-100\n"
            "Summary: order-service CrashLoopBackOff\n"
            "Description: Pods are crashing"
        )
        result = agent._parse_mcp_formatted_response(content)
        assert result["description"] == "Pods are crashing"


# ---------------------------------------------------------------------------
# Bug 1: Fallback resource extraction
# ---------------------------------------------------------------------------

class TestFallbackResourceExtraction:
    """When LLM/JSON parsing fails in _extract_resources_with_llm,
    affected_resources must still be populated via regex fallback.
    """

    def _make_agent(self):
        from src.agents.jira_agent import JiraAgent
        mock_tools = MagicMock()
        with patch("src.agents.jira_agent.create_extraction_llm"):
            agent = JiraAgent(jira_tools=mock_tools)
        return agent

    def test_fallback_method_exists(self):
        """_fallback_extract_resources must exist on JiraAgent."""
        agent = self._make_agent()
        assert hasattr(agent, "_fallback_extract_resources")

    def test_fallback_extracts_deployment_names(self):
        """Regex fallback extracts deployment-like names from text."""
        agent = self._make_agent()
        result = agent._fallback_extract_resources(
            raw_summary="order-service CrashLoopBackOff in production",
            raw_description="The order-service deployment is crashing."
        )
        assert isinstance(result, dict)
        assert "deployments" in result
        assert "order-service" in result["deployments"]

    def test_fallback_extracts_namespace(self):
        """Regex fallback extracts namespace from text."""
        agent = self._make_agent()
        result = agent._fallback_extract_resources(
            raw_summary="order-service down in staging namespace",
            raw_description=""
        )
        assert "namespaces" in result
        assert "staging" in result["namespaces"]

    def test_fallback_defaults_production_namespace(self):
        """When no namespace found, default to production."""
        agent = self._make_agent()
        result = agent._fallback_extract_resources(
            raw_summary="something is broken",
            raw_description=""
        )
        assert result["namespaces"] == ["production"]

    def test_fallback_extracts_service_names(self):
        """Regex fallback extracts service names (word-service pattern)."""
        agent = self._make_agent()
        result = agent._fallback_extract_resources(
            raw_summary="payment-api timeout",
            raw_description="The payment-api service is not responding."
        )
        assert "services" in result
        assert "payment-api" in result["services"] or "payment-api" in result["deployments"]

    def test_fallback_no_resources(self):
        """When no resources found, returns empty lists with default namespace."""
        agent = self._make_agent()
        result = agent._fallback_extract_resources(
            raw_summary="something happened",
            raw_description=""
        )
        assert result["deployments"] == []
        assert result["services"] == []
        assert result["namespaces"] == ["production"]


class TestExceptHandlersSetsResources:
    """Both except handlers in _extract_resources_with_llm must set
    affected_resources so should_continue_after_ticket_read routes correctly.
    """

    def _make_agent(self):
        from src.agents.jira_agent import JiraAgent
        mock_tools = MagicMock()
        with patch("src.agents.jira_agent.create_extraction_llm"):
            agent = JiraAgent(jira_tools=mock_tools)
        return agent

    @pytest.mark.asyncio
    async def test_inner_except_sets_affected_resources(self):
        """Inner except (JSON parse error) must set affected_resources."""
        agent = self._make_agent()

        # Make LLM return invalid JSON to trigger inner except
        agent.llm = AsyncMock()
        agent.llm.ainvoke = AsyncMock(
            side_effect=Exception("JSON parse failure")
        )

        state = {
            "ticket_id": "TEST-1",
            "ticket_summary": "",
            "namespace": "",
        }
        raw_fields = {
            "summary": "order-service CrashLoopBackOff in production",
            "description": "The order-service deployment is failing.",
        }

        result = await agent._extract_resources_with_llm(state, raw_fields)

        # Must have affected_resources set
        assert "affected_resources" in result
        resources = result["affected_resources"]
        assert isinstance(resources, dict)
        # Should have extracted order-service
        has_order_service = (
            "order-service" in resources.get("deployments", [])
            or "order-service" in resources.get("services", [])
        )
        assert has_order_service, f"Expected order-service in resources: {resources}"

    @pytest.mark.asyncio
    async def test_outer_except_sets_affected_resources(self):
        """Outer except (LLM invocation error) must set affected_resources."""
        agent = self._make_agent()

        # Raise before LLM call - mock the prompt builder to raise
        original_build = agent._build_extraction_prompt
        agent._build_extraction_prompt = MagicMock(
            side_effect=Exception("LLM connection failed")
        )

        state = {
            "ticket_id": "TEST-1",
            "ticket_summary": "",
            "namespace": "",
        }
        raw_fields = {
            "summary": "payment-api not responding in staging",
            "description": "The payment-api service is timing out.",
        }

        result = await agent._extract_resources_with_llm(state, raw_fields)

        # Must have affected_resources set
        assert "affected_resources" in result
        resources = result["affected_resources"]
        assert isinstance(resources, dict)
        has_payment = (
            "payment-api" in resources.get("deployments", [])
            or "payment-api" in resources.get("services", [])
        )
        assert has_payment, f"Expected payment-api in resources: {resources}"

    @pytest.mark.asyncio
    async def test_supervisor_routes_correctly_after_fallback(self):
        """After fallback, should_continue_after_ticket_read should route
        to investigate_cluster (not post_comment).
        """
        from src.supervisor import should_continue_after_ticket_read

        agent = self._make_agent()
        agent.llm = AsyncMock()
        agent.llm.ainvoke = AsyncMock(
            side_effect=Exception("LLM unavailable")
        )

        state = {
            "ticket_id": "TEST-1",
            "ticket_summary": "",
            "namespace": "",
        }
        raw_fields = {
            "summary": "order-service CrashLoopBackOff",
            "description": "Deployment order-service is crash looping.",
        }

        result = await agent._extract_resources_with_llm(state, raw_fields)
        route = should_continue_after_ticket_read(result)
        assert route == "investigate_cluster", (
            f"Expected investigate_cluster but got {route}. "
            f"affected_resources={result.get('affected_resources')}"
        )

    @pytest.mark.asyncio
    async def test_fallback_with_different_namespace(self):
        """Fallback must handle non-default namespace (variant coverage)."""
        agent = self._make_agent()
        agent.llm = AsyncMock()
        agent.llm.ainvoke = AsyncMock(
            side_effect=Exception("LLM failed")
        )

        state = {
            "ticket_id": "TEST-1",
            "ticket_summary": "",
            "namespace": "",
        }
        raw_fields = {
            "summary": "api-gateway failing in kube-system",
            "description": "The api-gateway is down in kube-system namespace.",
        }

        result = await agent._extract_resources_with_llm(state, raw_fields)
        assert result.get("namespace") in ("kube-system", "production")
        assert "affected_resources" in result
