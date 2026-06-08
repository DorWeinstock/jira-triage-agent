"""Comprehensive tests for exception hierarchy.

Tests cover:
- Exception instantiation and initialization
- Inheritance chain validation
- String representations (__str__ and __repr__)
- Context dict storage and handling
- Backward compatibility (optional context)
- Exception serialization and logging
"""

import pytest
from src.exceptions import (
    AgentError,
    ToolError,
    RemediationError,
    ValidationError,
    MCPConnectionError,
    MCPSessionTerminatedError,
)


class TestAgentErrorInitialization:
    """Test AgentError base class initialization."""

    def test_init_with_message_only(self):
        """AgentError should initialize with just a message."""
        exc = AgentError("Something went wrong")
        assert str(exc.args[0]) == "Something went wrong"
        assert exc.context == {}

    def test_init_with_context_kwargs(self):
        """AgentError should store context kwargs in context dict."""
        exc = AgentError("Error occurred", tool_name="jira", timeout=30)
        assert exc.context == {"tool_name": "jira", "timeout": 30}

    def test_init_with_empty_context(self):
        """AgentError with no kwargs should have empty context dict."""
        exc = AgentError("Error message")
        assert exc.context == {}
        assert isinstance(exc.context, dict)

    def test_context_is_dict_not_none(self):
        """Context should never be None, always a dict."""
        exc = AgentError("Message")
        assert exc.context is not None
        assert isinstance(exc.context, dict)

    def test_multiple_context_values(self):
        """AgentError should handle multiple context values."""
        exc = AgentError(
            "Error",
            component="k8s",
            namespace="default",
            resource="pod",
            severity="critical",
        )
        assert len(exc.context) == 4
        assert exc.context["component"] == "k8s"
        assert exc.context["severity"] == "critical"


class TestExceptionStringRepresentation:
    """Test __str__ and __repr__ methods."""

    def test_agent_error_str_without_context(self):
        """AgentError.__str__ should return message when context is empty."""
        exc = AgentError("Connection failed")
        assert str(exc) == "Connection failed"

    def test_agent_error_str_with_context(self):
        """AgentError.__str__ should include context when present."""
        exc = AgentError("Timeout", tool="jira", duration=5)
        result = str(exc)
        assert "Timeout" in result
        assert "context=" in result
        assert "tool" in result
        assert "jira" in result

    def test_agent_error_repr(self):
        """AgentError.__repr__ should show class name, message, and context."""
        exc = AgentError("Test error", code=500)
        result = repr(exc)
        assert "AgentError" in result
        assert "Test error" in result
        assert "context=" in result

    def test_repr_includes_context_even_empty(self):
        """__repr__ should show context dict even if empty."""
        exc = AgentError("Message")
        result = repr(exc)
        assert "context={}" in result

    def test_tool_error_str_delegates_to_parent(self):
        """ToolError.__str__ should delegate to parent implementation."""
        exc = ToolError("MCP call failed", endpoint="http://localhost:8080")
        result = str(exc)
        assert "MCP call failed" in result
        assert "context=" in result

    def test_validation_error_str(self):
        """ValidationError.__str__ should work correctly."""
        exc = ValidationError("Invalid namespace", namespace="invalid$name")
        result = str(exc)
        assert "Invalid namespace" in result

    def test_remediation_error_str(self):
        """RemediationError.__str__ should work correctly."""
        exc = RemediationError("Rollback failed", action="scale_deployment")
        result = str(exc)
        assert "Rollback failed" in result

    def test_mcp_connection_error_str(self):
        """MCPConnectionError.__str__ should work correctly."""
        exc = MCPConnectionError("Connection timeout", retries=3)
        result = str(exc)
        assert "Connection timeout" in result
        assert "retries" in result

    def test_mcp_session_error_str(self):
        """MCPSessionTerminatedError.__str__ should work correctly."""
        exc = MCPSessionTerminatedError("Session expired", duration=900)
        result = str(exc)
        assert "Session expired" in result


class TestExceptionInheritance:
    """Test exception hierarchy and inheritance chain."""

    def test_agent_error_is_exception(self):
        """AgentError should be an Exception subclass."""
        assert issubclass(AgentError, Exception)

    def test_tool_error_is_agent_error(self):
        """ToolError should inherit from AgentError."""
        assert issubclass(ToolError, AgentError)
        assert issubclass(ToolError, Exception)

    def test_remediation_error_is_agent_error(self):
        """RemediationError should inherit from AgentError."""
        assert issubclass(RemediationError, AgentError)

    def test_validation_error_is_agent_error(self):
        """ValidationError should inherit from AgentError."""
        assert issubclass(ValidationError, AgentError)

    def test_mcp_connection_error_is_tool_error(self):
        """MCPConnectionError should inherit from ToolError."""
        assert issubclass(MCPConnectionError, ToolError)
        assert issubclass(MCPConnectionError, AgentError)

    def test_mcp_session_error_is_tool_error(self):
        """MCPSessionTerminatedError should inherit from ToolError."""
        assert issubclass(MCPSessionTerminatedError, ToolError)
        assert issubclass(MCPSessionTerminatedError, AgentError)

    def test_exception_isinstance_check(self):
        """Exceptions should pass isinstance checks for parent classes."""
        exc = MCPConnectionError("Connection failed")
        assert isinstance(exc, MCPConnectionError)
        assert isinstance(exc, ToolError)
        assert isinstance(exc, AgentError)
        assert isinstance(exc, Exception)


class TestExceptionCatching:
    """Test exception catching and handling patterns."""

    def test_catch_specific_agent_error(self):
        """Can catch specific AgentError subclasses."""
        try:
            raise ValidationError("Invalid input")
        except ValidationError as e:
            assert str(e) == "Invalid input"

    def test_catch_tool_error_catches_mcp_connection_error(self):
        """Catching ToolError should catch MCPConnectionError."""
        try:
            raise MCPConnectionError("Connection timeout")
        except ToolError as e:
            assert isinstance(e, MCPConnectionError)

    def test_catch_agent_error_catches_all_subclasses(self):
        """Catching AgentError should catch all exception subclasses."""
        exceptions_to_test = [
            ToolError("Tool error"),
            RemediationError("Remediation error"),
            ValidationError("Validation error"),
            MCPConnectionError("MCP error"),
            MCPSessionTerminatedError("Session error"),
        ]

        for exc in exceptions_to_test:
            try:
                raise exc
            except AgentError as e:
                assert isinstance(e, AgentError)

    def test_catch_exception_catches_all(self):
        """Catching Exception should catch all custom exceptions."""
        try:
            raise ValidationError("Invalid data")
        except Exception as e:
            assert isinstance(e, Exception)
            assert isinstance(e, AgentError)


class TestContextPreservation:
    """Test that context is properly preserved through operations."""

    def test_context_preserved_after_str_conversion(self):
        """Context should remain unchanged after str() conversion."""
        exc = AgentError("Error", key="value")
        original_context = exc.context.copy()
        _ = str(exc)
        assert exc.context == original_context

    def test_context_preserved_after_repr(self):
        """Context should remain unchanged after repr() conversion."""
        exc = AgentError("Error", data={"nested": "value"})
        original_context = exc.context.copy()
        _ = repr(exc)
        assert exc.context == original_context

    def test_context_is_mutable(self):
        """Context dict should be mutable after creation."""
        exc = AgentError("Error")
        exc.context["new_key"] = "new_value"
        assert exc.context["new_key"] == "new_value"

    def test_modifying_context_doesnt_affect_message(self):
        """Modifying context should not affect error message."""
        exc = AgentError("Original message")
        exc.context["key"] = "value"
        assert str(exc.args[0]) == "Original message"


class TestExceptionArgs:
    """Test that exception args are properly accessible."""

    def test_exception_args_accessible(self):
        """Exception args should be accessible via .args."""
        message = "Test error message"
        exc = AgentError(message)
        assert exc.args[0] == message

    def test_exception_args_with_context(self):
        """Exception args should not include context."""
        exc = AgentError("Message", key="value")
        assert len(exc.args) == 1
        assert exc.args[0] == "Message"

    def test_can_raise_and_catch_with_args(self):
        """Raised exceptions should preserve args."""
        try:
            raise ValidationError("Validation failed")
        except ValidationError as e:
            assert e.args[0] == "Validation failed"


class TestBackwardCompatibility:
    """Test backward compatibility with existing code patterns."""

    def test_raise_without_context_kwargs(self):
        """Should work with existing code that raises without context."""
        try:
            raise ToolError("MCP tool failed")
        except ToolError as e:
            assert str(e) == "MCP tool failed"
            assert e.context == {}

    def test_string_conversion_for_logging(self):
        """str() should be usable for logging without errors."""
        exc = ToolError("Tool error", tool_name="jira")
        log_message = f"Error occurred: {exc}"
        assert "Error occurred:" in log_message
        assert "Tool error" in log_message

    def test_exception_as_string_in_f_string(self):
        """Exceptions should work in f-strings."""
        exc = ValidationError("Invalid input")
        message = f"Validation failed: {exc}"
        assert "Validation failed:" in message
        assert "Invalid input" in message


class TestEdgeCases:
    """Test edge cases and unusual scenarios."""

    def test_empty_message_string(self):
        """Should handle empty message string."""
        exc = AgentError("")
        assert str(exc.args[0]) == ""
        assert repr(exc)  # Should not raise

    def test_context_with_special_characters(self):
        """Context values with special characters should work."""
        exc = AgentError("Error", path="/path/to/file", query="a=1&b=2")
        assert exc.context["path"] == "/path/to/file"
        assert exc.context["query"] == "a=1&b=2"

    def test_context_with_complex_objects(self):
        """Context can store complex objects."""
        data = {"nested": {"key": "value"}, "list": [1, 2, 3]}
        exc = AgentError("Error", payload=data)
        assert exc.context["payload"] == data

    def test_exception_with_long_message(self):
        """Should handle long error messages."""
        long_message = "x" * 1000
        exc = AgentError(long_message)
        assert str(exc.args[0]) == long_message

    def test_exception_with_special_message_content(self):
        """Should handle special content in messages."""
        exc = AgentError("Error: {key} = {value}")
        assert "Error:" in str(exc.args[0])

    def test_none_context_values(self):
        """Should handle None values in context."""
        exc = AgentError("Error", result=None)
        assert exc.context["result"] is None

    def test_boolean_context_values(self):
        """Should handle boolean context values."""
        exc = AgentError("Error", success=False, verified=True)
        assert exc.context["success"] is False
        assert exc.context["verified"] is True

    def test_numeric_context_values(self):
        """Should handle numeric context values."""
        exc = AgentError("Error", timeout=30.5, retries=3)
        assert exc.context["timeout"] == 30.5
        assert exc.context["retries"] == 3


class TestExceptionChainingSupport:
    """Test support for Python 3's exception chaining."""

    def test_raise_from_preserves_cause(self):
        """Using 'raise from' should preserve exception cause."""
        try:
            try:
                raise ValueError("Original error")
            except ValueError as e:
                raise ToolError("Tool failed") from e
        except ToolError as e:
            assert e.__cause__.__class__.__name__ == "ValueError"

    def test_exception_chain_traceback(self):
        """Exception chain should be available in __context__."""
        try:
            try:
                raise RuntimeError("Base error")
            except RuntimeError:
                raise AgentError("Wrapped error")
        except AgentError as e:
            # __context__ is set when exception is raised in except block
            assert e.__context__ is not None


class TestExceptionSubclassCustomization:
    """Test that subclasses can be customized while preserving base behavior."""

    def test_tool_error_inherits_str_behavior(self):
        """ToolError should inherit __str__ behavior from AgentError."""
        exc = ToolError("Tool failed", endpoint="http://example.com")
        result = str(exc)
        assert "Tool failed" in result
        assert "endpoint" in result

    def test_validation_error_inherits_str_behavior(self):
        """ValidationError should inherit __str__ behavior from AgentError."""
        exc = ValidationError("Invalid", field="namespace")
        result = str(exc)
        assert "Invalid" in result
        assert "field" in result

    def test_all_subclasses_have_str_method(self):
        """All exception subclasses should have __str__ method."""
        for exc_class in [
            ToolError,
            RemediationError,
            ValidationError,
            MCPConnectionError,
            MCPSessionTerminatedError,
        ]:
            exc = exc_class("Test")
            assert hasattr(exc, "__str__")
            assert callable(exc.__str__)


class TestIntegration:
    """Integration tests combining multiple features."""

    def test_exception_lifecycle(self):
        """Test complete exception lifecycle."""
        # Create exception
        exc = ToolError("MCP timeout", tool="kubernetes", timeout=30)

        # Check initialization
        assert exc.context["tool"] == "kubernetes"

        # Convert to string for logging
        log_output = str(exc)
        assert "MCP timeout" in log_output

        # Get repr for debugging
        debug_output = repr(exc)
        assert "ToolError" in debug_output

        # Catch and re-raise
        try:
            raise exc
        except ToolError as e:
            # Verify context preserved
            assert e.context["timeout"] == 30
            # Use in another exception
            error_str = str(e)
            new_exc = AgentError(f"Failed to handle: {error_str}", original_error=error_str)
            assert "Failed to handle:" in str(new_exc)
            assert "MCP timeout" in new_exc.context["original_error"]

    def test_exception_handling_workflow(self):
        """Test realistic exception handling workflow."""
        try:
            # Simulate tool failure
            raise ToolError(
                "Failed to query Jira",
                endpoint="https://jira.example.com",
                method="GET",
                timeout=10,
            )
        except ToolError as e:
            # Log error
            error_message = str(e)
            assert "Failed to query Jira" in error_message

            # Wrap in higher-level error
            try:
                raise AgentError(
                    "Ticket reading failed",
                    original_error=str(e),
                    context=e.context,
                )
            except AgentError as wrapped:
                assert "Ticket reading failed" in str(wrapped)
                assert wrapped.context["original_error"] is not None

    def test_hierarchy_validation_workflow(self):
        """Test catching by hierarchy level."""
        exceptions = [
            ("tool_error", ToolError("Tool failed")),
            ("validation", ValidationError("Invalid input")),
            ("mcp_connection", MCPConnectionError("Connection failed")),
        ]

        for exc_type, exc in exceptions:
            try:
                raise exc
            except ToolError:
                # Should catch ToolError and its subclasses
                if "mcp_connection" in exc_type or "tool" in exc_type:
                    assert isinstance(exc, ToolError)
            except ValidationError:
                # Should catch ValidationError
                assert isinstance(exc, ValidationError)
            except AgentError:
                # Should catch any AgentError subclass
                assert isinstance(exc, AgentError)
