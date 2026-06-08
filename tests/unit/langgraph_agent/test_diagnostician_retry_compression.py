"""Unit tests for Diagnostician retry context compression.

Verifies that on remediation retries the diagnostician:
- Skips historical ticket context (already consumed on initial diagnosis)
- Compresses remediation history (summarise older entries, keep last full)
"""

import pytest
from unittest.mock import MagicMock, patch

from src.agents.diagnostician import Diagnostician
from src.state import AgentState


def _make_diagnostician() -> Diagnostician:
    """Create a Diagnostician with a mocked remediation agent and LLM."""
    agent = MagicMock()
    with patch("src.agents.diagnostician.create_diagnosis_llm", return_value=MagicMock()):
        return Diagnostician(remediation_agent=agent)


def _base_state(**overrides) -> AgentState:
    """Create a minimal AgentState for testing."""
    defaults = dict(
        ticket_id="TEST-1",
        thread_id="t-1",
        namespace="production",
        affected_resources={"deployments": ["my-deploy"], "services": ["my-svc"]},
        cluster_findings={
            "resources": {"pods": "pod-a Running"},
            "logs": {"pod-a": "some error"},
            "events": [],
            "preliminary_findings": "Something is broken",
        },
        similar_tickets=[
            {"key": "OLD-1", "summary": "same issue before", "last_comment": "fixed it"}
        ],
        past_resolutions=["Restarted the pod"],
    )
    defaults.update(overrides)
    return AgentState(**defaults)


# ---------------------------------------------------------------------------
# _build_historical_context
# ---------------------------------------------------------------------------
class TestBuildHistoricalContext:

    def test_initial_diagnosis_returns_full_context(self):
        diag = _make_diagnostician()
        state = _base_state(remediation_count=0)
        result = diag._build_historical_context(state)
        assert "HISTORICAL RESOLUTIONS" in result
        assert "OLD-1" in result

    def test_retry_1_returns_empty(self):
        diag = _make_diagnostician()
        state = _base_state(remediation_count=1)
        assert diag._build_historical_context(state) == ""

    def test_retry_2_returns_empty(self):
        diag = _make_diagnostician()
        state = _base_state(remediation_count=2)
        assert diag._build_historical_context(state) == ""

    def test_default_remediation_count_returns_full_context(self):
        """AgentState defaults remediation_count to 0 → full history."""
        diag = _make_diagnostician()
        state = _base_state()  # no explicit remediation_count
        result = diag._build_historical_context(state)
        assert "HISTORICAL RESOLUTIONS" in result


# ---------------------------------------------------------------------------
# _build_context — historical section on retry
# ---------------------------------------------------------------------------
class TestBuildContextRetry:

    def test_initial_includes_similar_past_issues(self):
        diag = _make_diagnostician()
        state = _base_state(remediation_count=0)
        ctx = diag._build_context(state)
        assert "Similar Past Issues" in ctx
        assert "Omitted on retry" not in ctx

    def test_retry_omits_historical_section(self):
        diag = _make_diagnostician()
        state = _base_state(remediation_count=1)
        ctx = diag._build_context(state)
        assert "Omitted on retry" in ctx
        assert "Similar Past Issues" not in ctx

    def test_retry_still_includes_ticket_and_k8s(self):
        diag = _make_diagnostician()
        state = _base_state(remediation_count=1)
        ctx = diag._build_context(state)
        assert "Original Ticket" in ctx
        assert "Kubernetes Investigation" in ctx


# ---------------------------------------------------------------------------
# _format_remediation_history
# ---------------------------------------------------------------------------
class TestFormatRemediationHistory:

    def test_empty_history(self):
        diag = _make_diagnostician()
        state = _base_state(remediation_history=[])
        assert diag._format_remediation_history(state) == "None"

    def test_single_entry_full(self):
        entry = {"action": "scale", "success": True, "detail": "scaled to 2"}
        diag = _make_diagnostician()
        state = _base_state(remediation_history=[entry])
        result = diag._format_remediation_history(state)
        assert "scale" in result
        assert "scaled to 2" in result

    def test_two_entries_first_summarised(self):
        entries = [
            {"action": "restart", "success": False, "error": "timeout"},
            {"action": "scale", "success": True, "detail": "scaled to 3"},
        ]
        diag = _make_diagnostician()
        state = _base_state(remediation_history=entries)
        result = diag._format_remediation_history(state)
        # First entry summarised
        assert "#1: restart -> FAILED" in result
        # Last entry kept full
        assert "Last attempt (full):" in result
        assert "scale" in result

    def test_three_entries(self):
        entries = [
            {"action": "restart", "success": False},
            {"action": "delete", "success": False},
            {"action": "scale", "success": True, "detail": "final fix"},
        ]
        diag = _make_diagnostician()
        state = _base_state(remediation_history=entries)
        result = diag._format_remediation_history(state)
        assert "#1: restart -> FAILED" in result
        assert "#2: delete -> FAILED" in result
        assert "Last attempt (full):" in result
