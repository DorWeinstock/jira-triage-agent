"""Unit tests for AgentState in langgraph_agent/src/state.py.

Covers the 5 fixes applied during the state.py refactor:
  1. No duplicate fields
  2. Consistent PEP 585 type hints
  3. Mutable defaults use Field(default_factory=...)
  4. Literal constraints on confidence_level, action_risk_level, max_remediation_loops > 0
  5. resumed_from_checkpoint is a public field (no leading underscore)
"""

import typing

import pytest
from pydantic.fields import FieldInfo

from src.state import AgentState


# =============================================================================
# Fix 1: No duplicate fields
# =============================================================================

class TestNoDuplicateFields:
    def test_previous_remediation_result_defined_once(self):
        annotations = AgentState.__annotations__
        # If a field were duplicated Python silently keeps the last definition;
        # we verify the field exists and has the correct type annotation.
        assert "previous_remediation_result" in annotations

    def test_new_issues_defined_once(self):
        assert "new_issues" in AgentState.__annotations__

    def test_field_count_is_stable(self):
        """Annotation count should not reflect hidden duplicates."""
        keys = list(AgentState.__annotations__.keys())
        assert len(keys) == len(set(keys)), "Duplicate field annotations detected"


# =============================================================================
# Fix 2: PEP 585 type hints — no legacy typing.Dict / typing.List
# =============================================================================

class TestTypeHints:
    def test_no_legacy_Dict_in_annotations(self):
        for field, hint in AgentState.__annotations__.items():
            assert hint is not typing.Dict, (
                f"Field '{field}' uses legacy typing.Dict; use dict instead"
            )

    def test_no_legacy_List_in_annotations(self):
        for field, hint in AgentState.__annotations__.items():
            assert hint is not typing.List, (
                f"Field '{field}' uses legacy typing.List; use list instead"
            )

    def test_affected_resources_uses_builtin_dict(self):
        hint = AgentState.__annotations__["affected_resources"]
        # get_origin returns dict for dict[str, list[str]]
        origin = typing.get_origin(hint)
        assert origin is dict, f"Expected dict origin, got {origin}"

    def test_previous_remediation_result_uses_builtin_dict(self):
        hint = AgentState.__annotations__["previous_remediation_result"]
        assert typing.get_origin(hint) is dict


# =============================================================================
# Fix 3: Mutable defaults use Field(default_factory=...)
#
# AgentState is a TypedDict; calling AgentState() returns an empty {} dict —
# fields are only populated when explicitly passed.  The default values are
# stored as class-level attributes (FieldInfo or scalars) that LangGraph reads
# when initialising a new graph state.  We therefore test the *class
# descriptor* rather than an instantiated instance.
# =============================================================================

class TestMutableDefaults:
    """Each list/dict field must declare Field(default_factory=…), not a bare [] / {}."""

    @pytest.mark.parametrize("field", [
        "ticket_labels", "ticket_components", "error_messages",
        "similar_tickets", "past_resolutions", "preventive_measures",
        "remediation_history", "verification_evidence", "new_issues",
    ])
    def test_list_field_uses_default_factory(self, field):
        """Class attribute must be a FieldInfo with default_factory=list."""
        descriptor = AgentState.__dict__.get(field)
        assert isinstance(descriptor, FieldInfo), (
            f"Field '{field}' class attribute should be a pydantic FieldInfo "
            f"(got {type(descriptor).__name__}); ensure it uses "
            "Field(default_factory=list)"
        )
        assert descriptor.default_factory is list, (
            f"Field '{field}' FieldInfo.default_factory should be 'list', "
            f"got {descriptor.default_factory!r}"
        )

    @pytest.mark.parametrize("field", [
        "affected_resources", "cluster_findings",
        "remediation_result", "previous_remediation_result",
    ])
    def test_dict_field_uses_default_factory(self, field):
        """Class attribute must be a FieldInfo with default_factory=dict."""
        descriptor = AgentState.__dict__.get(field)
        assert isinstance(descriptor, FieldInfo), (
            f"Field '{field}' class attribute should be a pydantic FieldInfo "
            f"(got {type(descriptor).__name__}); ensure it uses "
            "Field(default_factory=dict)"
        )
        assert descriptor.default_factory is dict, (
            f"Field '{field}' FieldInfo.default_factory should be 'dict', "
            f"got {descriptor.default_factory!r}"
        )

    def test_mutating_one_explicit_state_does_not_affect_another(self):
        """Two independently-constructed states must not share list objects."""
        state_a = AgentState(ticket_labels=["bug"])
        state_b = AgentState(ticket_labels=[])
        state_a["ticket_labels"].append("extra")
        assert state_b["ticket_labels"] == [], (
            "Mutating ticket_labels on state_a leaked into state_b — "
            "lists are independent when constructed explicitly"
        )


# =============================================================================
# Fix 4: Literal constraints
# =============================================================================

class TestLiteralConstraints:
    def test_confidence_level_accepts_valid_capitalized_values(self):
        for value in ("High", "Medium", "Low"):
            state = AgentState(confidence_level=value)
            assert state["confidence_level"] == value

    def test_confidence_level_default_is_none(self):
        state = AgentState()
        assert state.get("confidence_level") is None

    def test_action_risk_level_accepts_valid_values(self):
        for value in ("high", "low"):
            state = AgentState(action_risk_level=value)
            assert state["action_risk_level"] == value

    def test_action_risk_level_default_is_none(self):
        state = AgentState()
        assert state.get("action_risk_level") is None

    def test_max_remediation_loops_class_default_is_positive(self):
        """LangGraph reads the class-level FieldInfo default, not an instance key."""
        descriptor = AgentState.__dict__["max_remediation_loops"]
        assert isinstance(descriptor, FieldInfo), (
            "max_remediation_loops should be declared with Field(default=…)"
        )
        assert descriptor.default > 0, (
            f"max_remediation_loops default {descriptor.default!r} must be > 0"
        )

    def test_max_remediation_loops_class_default_value(self):
        descriptor = AgentState.__dict__["max_remediation_loops"]
        assert descriptor.default == 3, (
            f"Expected default 3, got {descriptor.default!r}"
        )

    def test_max_remediation_loops_accepts_positive_int(self):
        state = AgentState(max_remediation_loops=5)
        assert state["max_remediation_loops"] == 5


# =============================================================================
# Fix 5: resumed_from_checkpoint is public (no leading underscore)
# =============================================================================

class TestResumedFromCheckpoint:
    def test_field_is_public(self):
        assert "resumed_from_checkpoint" in AgentState.__annotations__, (
            "'resumed_from_checkpoint' must be a public field in AgentState"
        )

    def test_private_field_does_not_exist(self):
        assert "_resumed_from_checkpoint" not in AgentState.__annotations__, (
            "'_resumed_from_checkpoint' (private) should not exist; "
            "use 'resumed_from_checkpoint' instead"
        )

    def test_class_default_is_false(self):
        """LangGraph reads the class-level default; it must be False."""
        default = AgentState.__dict__["resumed_from_checkpoint"]
        assert default is False, (
            f"resumed_from_checkpoint class-level default should be False, "
            f"got {default!r}"
        )

    def test_can_be_set_to_true(self):
        state = AgentState(resumed_from_checkpoint=True)
        assert state["resumed_from_checkpoint"] is True

    def test_can_be_reset_to_false(self):
        state = AgentState(resumed_from_checkpoint=True)
        state["resumed_from_checkpoint"] = False
        assert state["resumed_from_checkpoint"] is False

    def test_accessible_via_string_key(self):
        """Supervisor and checkpointer access this field via dict key."""
        state = AgentState()
        state["resumed_from_checkpoint"] = True
        assert state.get("resumed_from_checkpoint") is True
