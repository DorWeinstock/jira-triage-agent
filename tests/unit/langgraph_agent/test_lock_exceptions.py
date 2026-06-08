"""Unit tests for simplified lock exceptions."""

import pytest
from src.exceptions import AgentError


class TestLockExceptions:
    """Test that lock errors use simplified AgentError."""

    def test_agent_error_with_lock_context(self):
        """Test AgentError with lock-related context."""
        error = AgentError(
            message="Resource already locked",
            resource_key="deployment--default--my-service",
            locked_by="PROJ-123",
        )

        # Simple exception with message
        assert str(error) == "Resource already locked"

        # Context is stored
        assert error.context["resource_key"] == "deployment--default--my-service"
        assert error.context["locked_by"] == "PROJ-123"

    def test_agent_error_accepts_arbitrary_kwargs(self):
        """Test that AgentError accepts arbitrary keyword arguments."""
        error = AgentError(
            message="Lock release failed",
            resource_key="deployment--default--my-service",
            some_other_field="value",
        )

        assert str(error) == "Lock release failed"
        assert error.context["resource_key"] == "deployment--default--my-service"
        assert error.context["some_other_field"] == "value"

    def test_agent_error_works_without_kwargs(self):
        """Test that AgentError works with just a message."""
        error = AgentError("Simple error message")

        assert str(error) == "Simple error message"
        assert error.context == {}
