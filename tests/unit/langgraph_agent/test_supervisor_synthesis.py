"""Tests for fallback synthesis routing and node when no K8s resources identified.

Tests the supervisor's ability to synthesize value from history/Jenkins data
when a ticket has no K8s resource names (e.g., hlctl CLI bugs, Jenkins failures).
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Routing tests for should_continue_after_ticket_read
# ---------------------------------------------------------------------------

class TestSynthesisRouting:
    """Test routing logic: when to synthesize vs skip."""

    def test_no_resources_no_context_routes_to_post_comment(self):
        """No resources AND no useful context -> post_comment (existing behavior)."""
        from src.supervisor import should_continue_after_ticket_read

        state = {
            "ticket_id": "TEST-100",
            "namespace": "default",
            "affected_resources": {},
            "similar_tickets": [],
            "jenkins_urls": [],
            "symptoms": None,
            "error_messages": [],
        }
        result = should_continue_after_ticket_read(state)
        assert result == "post_comment"

    def test_no_resources_with_similar_tickets_routes_to_synthesize(self):
        """No K8s resources but similar tickets found -> synthesize_from_context."""
        from src.supervisor import should_continue_after_ticket_read

        state = {
            "ticket_id": "TEST-101",
            "namespace": "default",
            "affected_resources": {},
            "similar_tickets": [{"key": "PREV-1", "summary": "Similar issue"}],
            "jenkins_urls": [],
            "symptoms": None,
            "error_messages": [],
        }
        result = should_continue_after_ticket_read(state)
        assert result == "synthesize_from_context"

    def test_no_resources_with_jenkins_urls_routes_to_synthesize(self):
        """No K8s resources but Jenkins URLs present -> synthesize_from_context."""
        from src.supervisor import should_continue_after_ticket_read

        state = {
            "ticket_id": "TEST-102",
            "namespace": "default",
            "affected_resources": {},
            "similar_tickets": [],
            "jenkins_urls": ["https://jenkins.example.com/job/build/123"],
            "symptoms": None,
            "error_messages": [],
        }
        result = should_continue_after_ticket_read(state)
        assert result == "synthesize_from_context"

    def test_no_resources_with_symptoms_routes_to_synthesize(self):
        """No K8s resources but symptoms populated -> synthesize_from_context."""
        from src.supervisor import should_continue_after_ticket_read

        state = {
            "ticket_id": "TEST-103",
            "namespace": "default",
            "affected_resources": {},
            "similar_tickets": [],
            "jenkins_urls": [],
            "symptoms": "hlctl command fails with timeout error",
            "error_messages": [],
        }
        result = should_continue_after_ticket_read(state)
        assert result == "synthesize_from_context"

    def test_no_resources_with_error_messages_routes_to_synthesize(self):
        """No K8s resources but error messages populated -> synthesize_from_context."""
        from src.supervisor import should_continue_after_ticket_read

        state = {
            "ticket_id": "TEST-104",
            "namespace": "default",
            "affected_resources": {},
            "similar_tickets": [],
            "jenkins_urls": [],
            "symptoms": None,
            "error_messages": ["Error: image pull backoff for registry.example.com/app:latest"],
        }
        result = should_continue_after_ticket_read(state)
        assert result == "synthesize_from_context"

    def test_has_resources_routes_to_investigate_cluster(self):
        """Has K8s resources -> investigate_cluster (existing behavior unchanged)."""
        from src.supervisor import should_continue_after_ticket_read

        state = {
            "ticket_id": "TEST-105",
            "namespace": "default",
            "affected_resources": {"deployments": ["api-server"]},
            "similar_tickets": [{"key": "PREV-1"}],
            "jenkins_urls": ["https://jenkins.example.com/job/build/1"],
        }
        result = should_continue_after_ticket_read(state)
        assert result == "investigate_cluster"

    def test_no_resources_empty_lists_no_context_routes_to_post_comment(self):
        """Empty resource lists AND no context -> post_comment."""
        from src.supervisor import should_continue_after_ticket_read

        state = {
            "ticket_id": "TEST-106",
            "namespace": "default",
            "affected_resources": {
                "deployments": [],
                "services": [],
                "pods": [],
            },
            "similar_tickets": [],
            "jenkins_urls": [],
            "symptoms": "",
            "error_messages": [],
        }
        result = should_continue_after_ticket_read(state)
        assert result == "post_comment"

    def test_no_resources_empty_symptoms_string_not_counted(self):
        """Empty string symptoms should not trigger synthesis."""
        from src.supervisor import should_continue_after_ticket_read

        state = {
            "ticket_id": "TEST-107",
            "namespace": "default",
            "affected_resources": {},
            "similar_tickets": [],
            "jenkins_urls": [],
            "symptoms": "",
            "error_messages": [],
        }
        result = should_continue_after_ticket_read(state)
        assert result == "post_comment"


# ---------------------------------------------------------------------------
# Synthesis node tests
# ---------------------------------------------------------------------------

class TestSynthesizeFromContextNode:
    """Test the synthesize_from_context node behavior."""

    @pytest.mark.asyncio
    async def test_produces_root_cause_from_similar_tickets(self):
        """Should set root_cause and recommended_action from context."""
        from src.supervisor import create_synthesize_from_context_node

        mock_llm_response = MagicMock()
        mock_llm_response.content = json.dumps({
            "root_cause": "hlctl CLI timeout due to API server overload",
            "recommended_actions": ["Restart the API gateway", "Check rate limits"],
            "confidence_level": "low",
        })

        with patch("src.supervisor.create_extraction_llm") as mock_create_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_llm_response
            mock_create_llm.return_value = mock_llm

            node_fn = create_synthesize_from_context_node(jenkins_investigator=None)
            state = {
                "ticket_id": "TEST-200",
                "ticket_summary": "hlctl command fails with timeout",
                "ticket_description": "Running hlctl deploy times out after 30s",
                "symptoms": "timeout on hlctl deploy",
                "error_messages": ["Error: context deadline exceeded"],
                "similar_tickets": [
                    {"key": "PREV-50", "summary": "hlctl timeout", "resolution": "Restarted API gateway"}
                ],
                "past_resolutions": ["Restarted API gateway"],
                "jenkins_urls": [],
                "jenkins_findings": {},
            }

            result = await node_fn(state)

            assert result["root_cause"] is not None
            assert "hlctl" in result["root_cause"].lower() or len(result["root_cause"]) > 0
            assert result["recommended_action"] is not None
            assert result["confidence_level"] == "Low"

    @pytest.mark.asyncio
    async def test_runs_jenkins_investigation_when_available(self):
        """Should call jenkins_investigator.run when available and urls exist."""
        from src.supervisor import create_synthesize_from_context_node

        mock_jenkins = AsyncMock()
        mock_jenkins.run.return_value = {
            "ticket_id": "TEST-201",
            "jenkins_findings": {"failure_type": "compilation_error", "root_cause": "Missing dep"},
            "jenkins_urls": ["https://jenkins.example.com/job/1"],
            "ticket_summary": "Build fails",
            "ticket_description": "Build broken",
            "symptoms": None,
            "error_messages": [],
            "similar_tickets": [],
            "past_resolutions": [],
        }

        mock_llm_response = MagicMock()
        mock_llm_response.content = json.dumps({
            "root_cause": "Missing dependency in build",
            "recommended_actions": ["Add missing dep to pom.xml"],
            "confidence_level": "low",
        })

        with patch("src.supervisor.create_extraction_llm") as mock_create_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_llm_response
            mock_create_llm.return_value = mock_llm

            node_fn = create_synthesize_from_context_node(jenkins_investigator=mock_jenkins)
            state = {
                "ticket_id": "TEST-201",
                "ticket_summary": "Build fails",
                "ticket_description": "Build broken",
                "symptoms": None,
                "error_messages": [],
                "similar_tickets": [],
                "past_resolutions": [],
                "jenkins_urls": ["https://jenkins.example.com/job/1"],
                "jenkins_findings": {},
            }

            result = await node_fn(state)

            mock_jenkins.run.assert_called_once()
            assert result["root_cause"] is not None

    @pytest.mark.asyncio
    async def test_handles_llm_failure_gracefully(self):
        """Should fall back to rule-based synthesis when LLM fails."""
        from src.supervisor import create_synthesize_from_context_node

        with patch("src.supervisor.create_extraction_llm") as mock_create_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.side_effect = Exception("LLM unavailable")
            mock_create_llm.return_value = mock_llm

            node_fn = create_synthesize_from_context_node(jenkins_investigator=None)
            state = {
                "ticket_id": "TEST-202",
                "ticket_summary": "Jenkins build failure",
                "ticket_description": "Nightly build failed",
                "symptoms": "Build timeout",
                "error_messages": ["Error: build timed out"],
                "similar_tickets": [
                    {"key": "PREV-99", "summary": "Build timeout last week"}
                ],
                "past_resolutions": ["Increased build timeout"],
                "jenkins_urls": [],
                "jenkins_findings": {},
            }

            result = await node_fn(state)

            # Should still produce a root_cause (rule-based fallback)
            assert result["root_cause"] is not None
            assert len(result["root_cause"]) > 0
            assert result["recommended_action"] is not None
            assert result["confidence_level"] == "Low"

    @pytest.mark.asyncio
    async def test_skips_jenkins_when_no_urls(self):
        """Should NOT call jenkins_investigator when no jenkins_urls."""
        from src.supervisor import create_synthesize_from_context_node

        mock_jenkins = AsyncMock()

        mock_llm_response = MagicMock()
        mock_llm_response.content = json.dumps({
            "root_cause": "Issue identified from history",
            "recommended_actions": ["Check logs"],
            "confidence_level": "low",
        })

        with patch("src.supervisor.create_extraction_llm") as mock_create_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_llm_response
            mock_create_llm.return_value = mock_llm

            node_fn = create_synthesize_from_context_node(jenkins_investigator=mock_jenkins)
            state = {
                "ticket_id": "TEST-203",
                "ticket_summary": "Some issue",
                "ticket_description": "Details",
                "symptoms": "Something wrong",
                "error_messages": [],
                "similar_tickets": [{"key": "PREV-1", "summary": "Similar"}],
                "past_resolutions": [],
                "jenkins_urls": [],
                "jenkins_findings": {},
            }

            await node_fn(state)

            mock_jenkins.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_jenkins_failure_gracefully(self):
        """Should continue synthesis even when Jenkins investigation fails."""
        from src.supervisor import create_synthesize_from_context_node

        mock_jenkins = AsyncMock()
        mock_jenkins.run.side_effect = Exception("Jenkins unreachable")

        mock_llm_response = MagicMock()
        mock_llm_response.content = json.dumps({
            "root_cause": "Diagnosis from available context",
            "recommended_actions": ["Investigate manually"],
            "confidence_level": "low",
        })

        with patch("src.supervisor.create_extraction_llm") as mock_create_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_llm_response
            mock_create_llm.return_value = mock_llm

            node_fn = create_synthesize_from_context_node(jenkins_investigator=mock_jenkins)
            state = {
                "ticket_id": "TEST-204",
                "ticket_summary": "Build issue",
                "ticket_description": "Build broken",
                "symptoms": None,
                "error_messages": [],
                "similar_tickets": [],
                "past_resolutions": [],
                "jenkins_urls": ["https://jenkins.example.com/job/1"],
                "jenkins_findings": {},
            }

            result = await node_fn(state)

            # Should still produce results despite Jenkins failure
            assert result["root_cause"] is not None
            assert result["confidence_level"] == "Low"
