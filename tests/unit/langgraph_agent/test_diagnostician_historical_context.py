"""Unit tests for diagnostician historical context in diagnosis and remediation plan generation.

These tests verify that historical resolutions from similar tickets are included
in the diagnosis prompt, which now generates both diagnosis AND remediation plan
in a single LLM call.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.diagnostician import Diagnostician
from src.models import Diagnosis, RemediationPlan, ActionType, ConfidenceLevel
from src.state import AgentState


class TestDiagnosticianHistoricalContext:
    """Test that historical resolutions are included in diagnosis and plan generation."""

    @pytest.fixture
    def mock_remediation_agent(self):
        """Create mock remediation agent."""
        agent = MagicMock()
        agent.execute_plan = AsyncMock(return_value={"success": True, "action_taken": "test"})
        return agent

    @pytest.fixture
    def state_with_history(self):
        """State with historical ticket data."""
        return AgentState(
            ticket_id="PROJ-123",
            thread_id="thread-123",
            namespace="production",
            affected_deployments=["payment-service"],
            cluster_findings={
                "problem_pods": ["payment-service-abc123"],
                "preliminary_findings": "ConfigMap not found",
            },
            similar_tickets=[
                {
                    "key": "PROJ-100",
                    "summary": "payment-service pods failing - missing ConfigMap",
                    "last_comment": "Fixed by creating the payment-config ConfigMap:\nkubectl create configmap payment-config -n production --from-literal=DATABASE_URL=postgres://payments-db:5432/payments --from-literal=REDIS_HOST=redis:6379",
                    "relevance_score": 0.95,
                }
            ],
            past_resolutions=[
                "- The issue was resolved by creating the missing ConfigMap with the correct values",
                "- The ConfigMap required DATABASE_URL and REDIS_HOST keys",
            ],
        )

    @pytest.mark.asyncio
    async def test_diagnosis_includes_historical_context(
        self, mock_remediation_agent, state_with_history
    ):
        """Test that the diagnosis prompt includes historical ticket data."""
        diagnostician = Diagnostician(remediation_agent=mock_remediation_agent)

        # Mock structured output to capture the prompt
        captured_messages = []

        async def capture_invoke(messages):
            captured_messages.extend(messages)
            # Return a mock LLM response with JSON content (as the real LLM would)
            diagnosis = Diagnosis(
                root_cause="Missing payment-config ConfigMap required by payment-service",
                confidence_level=ConfidenceLevel.HIGH,
                remediation_plan=RemediationPlan(
                    remediation_possible=True,
                    action=ActionType.CREATE_CONFIGMAP,
                    resource_type="configmap",
                    name="payment-config",
                    namespace="production",
                    data={
                        "DATABASE_URL": "postgres://payments-db:5432/payments",
                        "REDIS_HOST": "redis:6379",
                    },
                    reason="Historical resolution shows these values fixed the same issue",
                ),
                preventive_measures=["Add ConfigMap validation to CI/CD"],
                evidence=["Pod events show ConfigMap not found error"],
            )
            return MagicMock(content=diagnosis.model_dump_json())

        with patch.object(diagnostician, "llm") as mock_llm:
            mock_llm.with_structured_output = MagicMock(return_value=mock_llm)
            mock_llm.ainvoke = capture_invoke

            result = await diagnostician.run(state_with_history)

        # Verify the LLM was called
        assert len(captured_messages) == 2, "Should have system and human messages"

        # Get the human message (diagnosis prompt)
        diagnosis_prompt = captured_messages[1].content

        # Verify historical context is in the prompt
        assert "PROJ-100" in diagnosis_prompt, "Should include historical ticket key"
        assert "payment-config" in diagnosis_prompt or "ConfigMap" in diagnosis_prompt
        assert "DATABASE_URL" in diagnosis_prompt or "payments-db" in diagnosis_prompt

        # Verify the remediation plan was stored in state
        assert result.get("remediation_plan") is not None
        assert result["remediation_plan"]["action"] == "create_configmap"

    @pytest.mark.asyncio
    async def test_diagnosis_without_history(self, mock_remediation_agent):
        """Test that diagnosis works without historical data."""
        state = AgentState(
            ticket_id="PROJ-999",
            thread_id="thread-999",
            namespace="production",
            affected_deployments=["some-service"],
            cluster_findings={"problem_pods": ["some-service-xyz"]},
            similar_tickets=[],
            past_resolutions=[],
        )

        diagnostician = Diagnostician(remediation_agent=mock_remediation_agent)

        async def mock_invoke(messages):
            # Return a mock LLM response with JSON content (as the real LLM would)
            diagnosis = Diagnosis(
                root_cause="Pod crash due to OOM",
                confidence_level=ConfidenceLevel.MEDIUM,
                remediation_plan=RemediationPlan(
                    remediation_possible=True,
                    action=ActionType.RESTART,
                    resource_type="deployment",
                    name="some-service",
                    namespace="production",
                    reason="Restart to clear memory",
                ),
                preventive_measures=["Increase memory limits"],
            )
            return MagicMock(content=diagnosis.model_dump_json())

        with patch.object(diagnostician, "llm") as mock_llm:
            mock_llm.with_structured_output = MagicMock(return_value=mock_llm)
            mock_llm.ainvoke = mock_invoke

            result = await diagnostician.run(state)

        assert result.get("root_cause") is not None
        assert result.get("remediation_plan") is not None
        assert result["remediation_plan"]["action"] == "restart"

    @pytest.mark.asyncio
    async def test_past_resolutions_included_in_prompt(
        self, mock_remediation_agent, state_with_history
    ):
        """Test that past_resolutions list is included in the diagnosis prompt."""
        diagnostician = Diagnostician(remediation_agent=mock_remediation_agent)

        captured_prompts = []

        async def capture_invoke(messages):
            for msg in messages:
                captured_prompts.append(msg.content)
            return Diagnosis(
                root_cause="Missing ConfigMap",
                confidence_level=ConfidenceLevel.HIGH,
                remediation_plan=RemediationPlan(
                    remediation_possible=True,
                    action=ActionType.CREATE_CONFIGMAP,
                    resource_type="configmap",
                    name="payment-config",
                    namespace="production",
                    data={"DATABASE_URL": "value"},
                    reason="Based on historical resolution",
                ),
            )

        with patch.object(diagnostician, "llm") as mock_llm:
            mock_llm.with_structured_output = MagicMock(return_value=mock_llm)
            mock_llm.ainvoke = capture_invoke

            await diagnostician.run(state_with_history)

        # The diagnosis prompt should be the second message (human message)
        diagnosis_prompt = captured_prompts[1]

        # Verify past_resolutions content is present
        assert "HISTORICAL RESOLUTIONS" in diagnosis_prompt
        assert "DATABASE_URL" in diagnosis_prompt or "REDIS_HOST" in diagnosis_prompt

    @pytest.mark.asyncio
    async def test_remediation_plan_format_as_text(self, mock_remediation_agent):
        """Test that remediation plan is formatted as human-readable text for display."""
        diagnostician = Diagnostician(remediation_agent=mock_remediation_agent)

        plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.SCALE,
            resource_type="deployment",
            name="order-service",
            namespace="production",
            replicas=2,
            reason="Deployment scaled to 0 needs to be restored",
        )

        text = diagnostician._format_plan_as_text(plan)

        assert "Scale" in text
        assert "order-service" in text
        assert "2" in text
        assert "production" in text

    @pytest.mark.asyncio
    async def test_manual_intervention_plan_format(self, mock_remediation_agent):
        """Test that manual intervention plans are formatted correctly."""
        diagnostician = Diagnostician(remediation_agent=mock_remediation_agent)

        plan = RemediationPlan(
            remediation_possible=False,
            action=ActionType.MANUAL_INTERVENTION,
            resource_type="deployment",
            name="complex-service",
            namespace="production",
            reason="Complex multi-step procedure required",
            manual_instructions="1. Contact DBA team\n2. Verify database schema\n3. Apply migration",
        )

        text = diagnostician._format_plan_as_text(plan)

        assert "Contact DBA team" in text
        assert "migration" in text
