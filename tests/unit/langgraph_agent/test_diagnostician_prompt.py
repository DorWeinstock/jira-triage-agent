"""Unit tests for diagnostician prompt content.

Tests verify that the diagnosis system prompt correctly guides the LLM on
when to use automated remediation vs manual intervention. Specifically:
- Multi-step plans with known values should be automated (remediation_possible=true)
- The outdated "Complex multi-step procedures" manual_intervention guidance is removed
"""

import pytest

from src.agents.diagnostician import Diagnostician
from src.state import AgentState


class TestDiagnosticianPromptContent:
    """Test that the diagnostician prompt template has correct guidance."""

    @pytest.fixture
    def diagnostician(self):
        """Create a Diagnostician with no remediation agent (prompt testing only)."""
        return Diagnostician(remediation_agent=None)

    @pytest.fixture
    def sample_state(self):
        """Minimal state for building system prompt."""
        return AgentState(
            ticket_id="TEST-1",
            thread_id="thread-1",
            namespace="production",
            affected_deployments=["my-service"],
        )

    def test_prompt_does_not_contain_complex_multi_step_manual(
        self, diagnostician, sample_state
    ):
        """The prompt must NOT tell the LLM that 'complex multi-step procedures'
        require manual intervention -- that guidance is outdated since we now
        support multi-step automated plans."""
        prompt = diagnostician._build_diagnosis_system_prompt(sample_state)
        assert "Complex multi-step procedures" not in prompt

    def test_prompt_contains_multi_step_automation_guidance(
        self, diagnostician, sample_state
    ):
        """The prompt MUST tell the LLM that multi-step plans with all known
        values should use remediation_possible=true, in the manual intervention
        guidance section (replacing the outdated 'Complex multi-step procedures' line)."""
        prompt = diagnostician._build_diagnosis_system_prompt(sample_state)
        # Should contain explicit guidance that multi-step plans with known values
        # are suitable for automated remediation (not manual intervention)
        assert "multi-step" in prompt.lower()
        # Must explicitly state that multi-step with known values = remediation_possible=true
        # This is the replacement guidance for the removed "Complex multi-step procedures" line
        assert "all steps have known" in prompt.lower() or "all values" in prompt.lower()

    def test_prompt_still_requires_manual_for_missing_values(
        self, diagnostician, sample_state
    ):
        """Manual intervention guidance must still include missing config values."""
        prompt = diagnostician._build_diagnosis_system_prompt(sample_state)
        assert "Missing configuration values" in prompt or "missing" in prompt.lower()

    def test_prompt_still_requires_manual_for_domain_knowledge(
        self, diagnostician, sample_state
    ):
        """Manual intervention guidance must still include domain knowledge cases."""
        prompt = diagnostician._build_diagnosis_system_prompt(sample_state)
        assert "domain knowledge" in prompt.lower()

    def test_prompt_still_requires_manual_for_low_confidence(
        self, diagnostician, sample_state
    ):
        """Manual intervention guidance must still include low confidence cases."""
        prompt = diagnostician._build_diagnosis_system_prompt(sample_state)
        assert "low confidence" in prompt.lower() or "Low confidence" in prompt

    def test_prompt_template_raw_does_not_contain_outdated_guidance(
        self, diagnostician
    ):
        """Check the raw template string directly for the outdated text."""
        template = diagnostician._DIAGNOSIS_PROMPT_TEMPLATE
        assert "Complex multi-step procedures" not in template
