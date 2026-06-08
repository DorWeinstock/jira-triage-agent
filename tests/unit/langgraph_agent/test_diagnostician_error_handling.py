"""Unit tests for Diagnostician error handling and fallback behavior.

Tests verify that:
- ValidationError triggers fallback with low confidence
- ToolError triggers fallback with low confidence
- Unexpected exceptions trigger fallback with low confidence
- Fallback diagnosis sets all required state fields
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.diagnostician import Diagnostician
from src.exceptions import ToolError
from src.state import AgentState


def _make_diagnostician_with_mocked_llm(llm_mock=None):
    """Create Diagnostician with mocked LLM."""
    if llm_mock is None:
        llm_mock = MagicMock()
    with patch("src.agents.diagnostician.create_diagnosis_llm", return_value=llm_mock):
        return Diagnostician(remediation_agent=None)


def _base_state(**overrides):
    """Create minimal AgentState for testing."""
    defaults = dict(
        ticket_id="TEST-1",
        thread_id="t-1",
        namespace="production",
        affected_resources={"deployments": ["my-deploy"], "services": ["my-svc"]},
        cluster_findings={
            "resources": {"pods": "pod-a Running"},
            "logs": {},
            "events": [],
            "preliminary_findings": "Something is broken",
        },
    )
    defaults.update(overrides)
    return AgentState(**defaults)


class TestValidationErrorFallback:
    """ValidationError from LLM should trigger fallback."""

    @pytest.mark.asyncio
    async def test_validation_error_triggers_fallback(self):
        """LLM response fails Pydantic validation → fallback with low confidence."""
        llm_mock = MagicMock()
        llm_mock.ainvoke = AsyncMock(
            return_value=MagicMock(content='{"invalid": "schema"}')
        )
        diag = _make_diagnostician_with_mocked_llm(llm_mock)
        state = _base_state()

        result = await diag.run(state)

        # Should have applied fallback
        assert result["confidence_level"] == "Low"
        assert result["remediation_plan"]["remediation_possible"] is False
        assert result["root_cause"]  # Should be set
        assert result["recommended_action"]  # Should be set
        assert result["preventive_measures"]  # Should be set

    @pytest.mark.asyncio
    async def test_validation_error_state_complete(self):
        """After validation error, all state fields are populated."""
        llm_mock = MagicMock()
        llm_mock.ainvoke = AsyncMock(
            return_value=MagicMock(content='{"invalid": "data"}')
        )
        diag = _make_diagnostician_with_mocked_llm(llm_mock)
        state = _base_state()

        result = await diag.run(state)

        # All critical fields should be set
        assert "root_cause" in result
        assert "confidence_level" in result
        assert "remediation_plan" in result
        assert "recommended_action" in result
        assert "preventive_measures" in result
        # Remediation plan should explicitly be non-possible
        assert result["remediation_plan"]["remediation_possible"] is False


class TestToolErrorFallback:
    """ToolError from LLM should trigger fallback."""

    @pytest.mark.asyncio
    async def test_tool_error_triggers_fallback(self):
        """LLM call fails with ToolError → fallback with low confidence."""
        llm_mock = MagicMock()
        llm_mock.ainvoke = AsyncMock(
            side_effect=ToolError("LLM connection timeout", timeout=30)
        )
        diag = _make_diagnostician_with_mocked_llm(llm_mock)
        state = _base_state()

        result = await diag.run(state)

        assert result["confidence_level"] == "Low"
        assert result["remediation_plan"]["remediation_possible"] is False


class TestUnexpectedExceptionFallback:
    """Unexpected exceptions should trigger fallback."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_triggers_fallback(self):
        """Unexpected exception (e.g., RuntimeError) → fallback with low confidence."""
        llm_mock = MagicMock()
        llm_mock.ainvoke = AsyncMock(
            side_effect=RuntimeError("Unexpected LLM error")
        )
        diag = _make_diagnostician_with_mocked_llm(llm_mock)
        state = _base_state()

        result = await diag.run(state)

        assert result["confidence_level"] == "Low"
        assert result["remediation_plan"]["remediation_possible"] is False

    @pytest.mark.asyncio
    async def test_unexpected_exception_state_complete(self):
        """After unexpected error, all state fields populated."""
        llm_mock = MagicMock()
        llm_mock.ainvoke = AsyncMock(
            side_effect=RuntimeError("Some unexpected error")
        )
        diag = _make_diagnostician_with_mocked_llm(llm_mock)
        state = _base_state()

        result = await diag.run(state)

        # All fields should be set
        assert "root_cause" in result
        assert "confidence_level" in result
        assert "remediation_plan" in result
        assert "recommended_action" in result
        assert "preventive_measures" in result


class TestFallbackStateComplete:
    """Verify fallback diagnosis produces complete state regardless of error type."""

    @pytest.mark.asyncio
    async def test_fallback_sets_all_required_fields(self):
        """Fallback must set all required state fields."""
        llm_mock = MagicMock()
        llm_mock.ainvoke = AsyncMock(
            side_effect=ValueError("Unexpected error")
        )
        diag = _make_diagnostician_with_mocked_llm(llm_mock)
        state = _base_state(
            cluster_findings={
                "resources": {"pods": "pod-a CrashLoopBackOff"},
                "logs": {"pod-a": "OOMKilled"},
                "events": [],
                "preliminary_findings": "Pod is crashing due to memory",
                "problem_pods": ["pod-a"],
                "recommendations": ["Increase memory limit"],
            }
        )

        result = await diag.run(state)

        # Verify all fields are present
        assert result.get("root_cause"), "root_cause should be set"
        assert result.get("confidence_level") == "Low", "confidence should be Low"
        assert result.get("recommended_action"), "recommended_action should be set"
        assert result.get("preventive_measures"), "preventive_measures should be set"
        assert result.get("remediation_plan"), "remediation_plan should be set"

        # Remediation plan must be non-possible on fallback
        plan = result["remediation_plan"]
        assert plan.get("remediation_possible") is False
        assert plan.get("manual_instructions"), "manual_instructions should be set"

    @pytest.mark.asyncio
    async def test_fallback_uses_cluster_findings(self):
        """Fallback should incorporate cluster findings in diagnosis."""
        llm_mock = MagicMock()
        llm_mock.ainvoke = AsyncMock(
            side_effect=ToolError("LLM unavailable")
        )
        diag = _make_diagnostician_with_mocked_llm(llm_mock)
        state = _base_state(
            cluster_findings={
                "resources": {"pods": "pod-x Running"},
                "logs": {},
                "events": [],
                "preliminary_findings": "Pod count mismatch detected",
                "problem_pods": ["pod-x"],
                "recommendations": ["Check deployment replicas"],
            }
        )

        result = await diag.run(state)

        # Root cause should mention the problem pods
        root_cause = result.get("root_cause", "")
        assert "pod-x" in root_cause or "broken" in root_cause.lower()
