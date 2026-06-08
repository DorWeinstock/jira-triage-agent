"""Unit tests for lock-related state fields."""

import pytest
from src.state import AgentState


class TestLockStateFields:
    """Test lock-related fields in AgentState."""

    def test_remediation_skipped_due_to_lock_default(self):
        """Test default value of remediation_skipped_due_to_lock."""
        state = AgentState()
        assert state.get("remediation_skipped_due_to_lock", False) is False

    def test_locked_by_ticket_default(self):
        """Test default value of locked_by_ticket."""
        state = AgentState()
        assert state.get("locked_by_ticket") is None

    def test_set_lock_skip_fields(self):
        """Test setting lock skip fields."""
        state = AgentState(
            remediation_skipped_due_to_lock=True, locked_by_ticket="PROJ-456"
        )
        assert state.get("remediation_skipped_due_to_lock") is True
        assert state.get("locked_by_ticket") == "PROJ-456"
