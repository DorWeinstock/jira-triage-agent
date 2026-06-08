"""Test namespace default consistency across codebase."""
import pytest


def test_namespace_defaults_match_constant():
    """Verify namespace defaults match DEFAULT_NAMESPACE constant.

    AgentState is a TypedDict subclass that defines defaults as class attributes.
    We verify the class-level default matches the centralized constant.
    """
    from src.constants import DEFAULT_NAMESPACE
    from src.state import AgentState

    # For TypedDict subclasses, get the default value from class attributes
    # This is how LangGraph MessagesState subclasses define defaults
    namespace_default = getattr(AgentState, "__annotations__", {}).get("namespace")

    # Verify the field exists
    assert namespace_default is not None, "AgentState should have a 'namespace' field"

    # Check the actual default value defined in the class body
    # For TypedDict with defaults, the value is stored as a class attribute
    actual_default = AgentState.__dict__.get("namespace", "NOT_SET")

    # If not in __dict__, check if it's defined with a default value in the class
    # by examining the class definition through __class_getitem__ or similar
    if actual_default == "NOT_SET":
        # Fallback: check via get_type_hints or class attributes
        import inspect
        source = inspect.getsource(AgentState)
        # Look for the pattern: namespace: str = "default"
        import re
        match = re.search(r'namespace:\s*str\s*=\s*["\']([^"\']+)["\']', source)
        if match:
            actual_default = match.group(1)

    assert actual_default == DEFAULT_NAMESPACE, (
        f"state.py namespace default '{actual_default}' != "
        f"constants.DEFAULT_NAMESPACE '{DEFAULT_NAMESPACE}'"
    )


def test_supervisor_uses_constant_for_namespace():
    """Verify supervisor initializes namespace from constant, not hardcoded value."""
    from src.constants import DEFAULT_NAMESPACE
    from src.supervisor import initialize_state

    # Create minimal state input (dict-like, simulating AgentState)
    state = {"ticket_id": "TEST-123"}
    result = initialize_state(state)

    assert result.get("namespace") == DEFAULT_NAMESPACE, (
        f"supervisor initialized namespace to '{result.get('namespace')}' "
        f"but should use DEFAULT_NAMESPACE '{DEFAULT_NAMESPACE}'"
    )


def test_no_hardcoded_production_namespace():
    """Verify supervisor doesn't hardcode 'production' for namespace."""
    from src.supervisor import initialize_state

    state = {"ticket_id": "TEST-456"}
    result = initialize_state(state)

    # The namespace should NOT be "production" when DEFAULT_NAMESPACE is "default"
    assert result.get("namespace") != "production", (
        "supervisor should not hardcode 'production' as namespace - "
        "should use DEFAULT_NAMESPACE constant instead"
    )
