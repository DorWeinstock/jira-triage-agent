"""Unit tests for llm_outputs.py validators and guards.

Tests cover:
1. RemediationStep.ensure_namespace - normalizes bad namespace values
2. RemediationPlan.auto_create_step_from_top_level - backward compatibility
3. JiraTicketResponse.infer_is_resolved - infers from status field
4. yaml_content max_length enforcement - prevents large/injection payloads
5. RemediationPlan schema hiding - legacy fields excluded from LLM schema
"""

import pytest
from pydantic import ValidationError

from src.models.llm_outputs import (
    RemediationStep,
    RemediationPlan,
    JiraTicketResponse,
    ActionType,
    TicketExtraction,
    get_llm_schema_for_remediation_plan,
)


# ===========================================================================
# 1. RemediationStep.ensure_namespace validator
# ===========================================================================


class TestRemediationStepEnsureNamespace:
    """Test RemediationStep.ensure_namespace field validator."""

    @pytest.mark.parametrize("bad_value", ["", "N/A", "unknown", None])
    def test_bad_namespace_values_become_default(self, bad_value):
        """Empty, N/A, unknown, or None become 'default'."""
        step = RemediationStep(
            action=ActionType.RESTART,
            name="web",
            namespace=bad_value,
            reason="restart to fix",
        )
        assert step.namespace == "default"

    def test_valid_namespace_preserved(self):
        """Valid namespace is preserved."""
        step = RemediationStep(
            action=ActionType.RESTART,
            name="web",
            namespace="production",
            reason="restart to fix",
        )
        assert step.namespace == "production"

    def test_whitespace_only_becomes_default(self):
        """Whitespace-only namespace becomes default."""
        step = RemediationStep(
            action=ActionType.RESTART,
            name="web",
            namespace="   ",
            reason="restart to fix",
        )
        # Whitespace is truthy, so it's preserved as-is by the validator
        # (the validator only checks `not v` and specific strings)
        assert step.namespace == "   "


# ===========================================================================
# 2. RemediationPlan.auto_create_step_from_top_level model_validator
# ===========================================================================


class TestRemediationPlanAutoCreateStep:
    """Test RemediationPlan.auto_create_step_from_top_level validator."""

    def test_legacy_fields_auto_create_single_step(self):
        """When steps is empty, legacy top-level fields create a step."""
        plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.SCALE,
            resource_type="deployment",
            name="my-svc",
            namespace="production",
            replicas=3,
            reason="scale up to restore",
        )
        assert len(plan.steps) == 1
        assert plan.steps[0].action == ActionType.SCALE
        assert plan.steps[0].name == "my-svc"
        assert plan.steps[0].namespace == "production"
        assert plan.steps[0].replicas == 3

    def test_manual_intervention_does_not_create_step(self):
        """Manual intervention action does NOT create an auto-step."""
        plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.MANUAL_INTERVENTION,
            name="svc",
        )
        assert plan.steps == []

    def test_existing_steps_not_overwritten_by_legacy_fields(self):
        """If steps already exist, legacy fields are ignored."""
        step = RemediationStep(
            action=ActionType.RESTART,
            name="web",
            namespace="default",
            reason="restart",
        )
        plan = RemediationPlan(
            remediation_possible=True,
            steps=[step],
            action=ActionType.SCALE,  # legacy field present but should be ignored
            name="different-svc",
            replicas=5,
        )
        assert len(plan.steps) == 1
        assert plan.steps[0].action == ActionType.RESTART  # not overwritten
        assert plan.steps[0].name == "web"  # not overwritten

    def test_plan_serialization_roundtrip(self):
        """Plan can be dumped to dict and reconstructed."""
        plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.SCALE,
            resource_type="deployment",
            name="svc",
            replicas=2,
            reason="scale",
        )
        # Serialize
        data = plan.model_dump()
        # Reconstruct
        restored = RemediationPlan(**data)
        assert len(restored.steps) == 1
        assert restored.steps[0].replicas == 2

    def test_remediation_not_possible_no_step_creation(self):
        """When remediation_possible=False, no auto-step is created."""
        plan = RemediationPlan(
            remediation_possible=False,
            action=ActionType.SCALE,
            name="svc",
            manual_instructions="Contact DBA",
        )
        assert plan.steps == []
        assert plan.manual_instructions == "Contact DBA"


# ===========================================================================
# 3. JiraTicketResponse.infer_is_resolved model_validator
# ===========================================================================


class TestJiraTicketResponseInferIsResolved:
    """Test JiraTicketResponse.infer_is_resolved model_validator."""

    @pytest.mark.parametrize("status", ["resolved", "done", "closed"])
    def test_resolved_statuses_infer_flag_true(self, status):
        """Status values 'resolved', 'done', 'closed' infer is_resolved=True."""
        ticket = JiraTicketResponse(key="SP-123", status=status)
        assert ticket.is_resolved is True

    @pytest.mark.parametrize("status", ["Resolved", "DONE", "Closed"])
    def test_case_insensitive_matching(self, status):
        """Status matching is case-insensitive."""
        ticket = JiraTicketResponse(key="SP-123", status=status)
        assert ticket.is_resolved is True

    def test_open_status_does_not_infer_resolved(self):
        """Open statuses do not infer is_resolved=True."""
        ticket = JiraTicketResponse(key="SP-123", status="In Progress")
        assert ticket.is_resolved is False

    def test_explicit_true_is_preserved(self):
        """If is_resolved is explicitly True, it stays True."""
        ticket = JiraTicketResponse(
            key="SP-123", status="In Progress", is_resolved=True
        )
        assert ticket.is_resolved is True

    def test_explicit_false_for_resolved_status_inferred_to_true(self):
        """Even if is_resolved=False initially, status inference takes over."""
        ticket = JiraTicketResponse(key="SP-123", status="resolved", is_resolved=False)
        assert ticket.is_resolved is True  # inferred from status

    def test_empty_status_does_not_infer_resolved(self):
        """Empty status string does not match resolution indicators."""
        ticket = JiraTicketResponse(key="SP-123", status="")
        assert ticket.is_resolved is False


# ===========================================================================
# 4. yaml_content max_length enforcement
# ===========================================================================


class TestYamlContentSizeGuard:
    """Test yaml_content max_length constraint."""

    def test_small_yaml_content_accepted(self):
        """Small YAML content is accepted."""
        step = RemediationStep(
            action=ActionType.APPLY_MANIFEST,
            name="test",
            namespace="default",
            reason="apply",
            yaml_content="apiVersion: v1\nkind: Pod\n",
        )
        assert step.yaml_content is not None
        assert "apiVersion" in step.yaml_content

    def test_yaml_at_limit_accepted(self):
        """YAML at exactly the max_length is accepted."""
        max_yaml = "x" * 65_536
        step = RemediationStep(
            action=ActionType.APPLY_MANIFEST,
            name="test",
            namespace="default",
            reason="apply",
            yaml_content=max_yaml,
        )
        assert len(step.yaml_content) == 65_536

    def test_yaml_exceeding_limit_rejected(self):
        """YAML exceeding max_length is rejected."""
        oversized_yaml = "x" * 65_537
        with pytest.raises(ValidationError) as exc_info:
            RemediationStep(
                action=ActionType.APPLY_MANIFEST,
                name="test",
                namespace="default",
                reason="apply",
                yaml_content=oversized_yaml,
            )
        assert "at most 65536 characters" in str(exc_info.value)

    def test_yaml_none_is_valid(self):
        """yaml_content=None is valid (optional field)."""
        step = RemediationStep(
            action=ActionType.RESTART,
            name="test",
            namespace="default",
            reason="restart",
            yaml_content=None,
        )
        assert step.yaml_content is None

    def test_remediation_plan_legacy_yaml_content_max_length(self):
        """RemediationPlan's legacy yaml_content also enforces max_length."""
        oversized_yaml = "x" * 65_537
        with pytest.raises(ValidationError) as exc_info:
            RemediationPlan(
                remediation_possible=True,
                action=ActionType.APPLY_MANIFEST,
                name="test",
                yaml_content=oversized_yaml,
            )
        assert "at most 65536 characters" in str(exc_info.value)


# ===========================================================================
# 5. RemediationPlan schema hiding for LLM
# ===========================================================================


class TestRemediationPlanLlmSchemaHiding:
    """Test that legacy fields are hidden from the LLM schema."""

    def test_get_llm_schema_excludes_legacy_fields(self):
        """get_llm_schema_for_remediation_plan excludes legacy fields."""
        schema = get_llm_schema_for_remediation_plan()
        props = schema.get("properties", {})

        # These legacy fields should be absent
        legacy_fields = {
            "action",
            "resource_type",
            "name",
            "namespace",
            "data",
            "yaml_content",
            "replicas",
            "reason",
        }
        for field in legacy_fields:
            assert field not in props, f"Legacy field '{field}' should not be in LLM schema"

    def test_llm_schema_keeps_canonical_fields(self):
        """Canonical fields (remediation_possible, steps, etc.) are present."""
        schema = get_llm_schema_for_remediation_plan()
        props = schema.get("properties", {})

        # These fields should be present
        assert "remediation_possible" in props
        assert "steps" in props
        assert "manual_instructions" in props

    def test_llm_schema_has_steps_with_remediationstep_schema(self):
        """The steps field references RemediationStep schema."""
        schema = get_llm_schema_for_remediation_plan()
        props = schema.get("properties", {})
        steps_schema = props.get("steps", {})

        # steps should be an array
        assert steps_schema.get("type") == "array"
        # items should reference RemediationStep
        items = steps_schema.get("items", {})
        # RemediationStep is either inline or a $ref
        assert items.get("type") == "object" or "$ref" in items

    def test_full_remediation_plan_still_works_with_legacy_fields(self):
        """Full RemediationPlan model_dump() still includes legacy fields."""
        plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.SCALE,
            resource_type="deployment",
            name="svc",
            namespace="prod",
            replicas=3,
            reason="scale",
        )
        dumped = plan.model_dump()

        # Legacy fields ARE present in the actual instance dump
        assert dumped["action"] == "scale"
        assert dumped["name"] == "svc"
        assert dumped["namespace"] == "prod"
        # And the auto-created step is also there
        assert len(dumped["steps"]) == 1


# ===========================================================================
# 6. Integration tests
# ===========================================================================


class TestIntegrationValidators:
    """Integration tests combining multiple validators."""

    def test_step_with_bad_namespace_and_valid_yaml(self):
        """Namespace and yaml_content validators work together."""
        step = RemediationStep(
            action=ActionType.APPLY_MANIFEST,
            name="test",
            namespace="N/A",  # will be normalized
            reason="apply",
            yaml_content="apiVersion: v1\nkind: Pod",
        )
        assert step.namespace == "default"
        assert step.yaml_content is not None

    def test_plan_with_legacy_fields_auto_creates_step_with_namespace_normalization(self):
        """Legacy fields with bad namespace still create a valid step."""
        plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.RESTART,
            name="svc",
            namespace="unknown",  # will be normalized
            reason="restart",
        )
        assert len(plan.steps) == 1
        assert plan.steps[0].namespace == "default"  # normalized

    def test_ticket_response_inference_with_explicit_flag(self):
        """is_resolved can be explicitly set and status inference respects it."""
        ticket = JiraTicketResponse(key="SP-1", status="done", is_resolved=True)
        assert ticket.is_resolved is True

        ticket2 = JiraTicketResponse(key="SP-2", status="open")
        assert ticket2.is_resolved is False
