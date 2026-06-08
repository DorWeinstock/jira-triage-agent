"""Exception hierarchy for agent error handling.

This module provides a comprehensive exception hierarchy covering all agent error scenarios.
The design is extensible to accommodate future error types and observability requirements.
"""


class AgentError(Exception):
    """Base exception for all agent errors.

    Use this for general agent failures that don't fit other categories.
    Supports optional context data for enhanced debugging and logging.
    """

    def __init__(self, message: str, **context):
        """Initialize with message and optional context kwargs.

        Args:
            message: Error message describing the failure.
            **context: Optional key-value pairs providing additional error context
                      (e.g., tool_name="jira", timeout=30).
        """
        super().__init__(message)
        self.context = context or {}

    def __str__(self) -> str:
        """Return string representation including context if present."""
        msg = self.args[0] if self.args else ""
        if self.context:
            return f"{msg} | context={self.context}"
        return msg

    def __repr__(self) -> str:
        """Return detailed representation for debugging."""
        return f"{self.__class__.__name__}({self.args[0]!r}, context={self.context})"


class ToolError(AgentError):
    """Raised when tool execution fails.

    Covers:
    - MCP connection failures
    - MCP session errors
    - Tool timeouts
    - Tool execution errors
    - LLM invocation failures
    """

    def __str__(self) -> str:
        """Return tool-specific error representation."""
        return super().__str__()


class RemediationError(AgentError):
    """Raised when remediation operation fails.

    Covers:
    - Remediation execution failures
    - Verification failures
    - Rollback needed scenarios
    """

    def __str__(self) -> str:
        """Return remediation-specific error representation."""
        return super().__str__()


class ValidationError(AgentError):
    """Raised when input validation fails.

    Covers:
    - Invalid resource names
    - Invalid namespaces
    - Invalid tool arguments
    - Schema validation failures
    """

    def __str__(self) -> str:
        """Return validation-specific error representation."""
        return super().__str__()


class MCPConnectionError(ToolError):
    """Raised when MCP server connection fails after retries.

    Covers:
    - Initial connection failures
    - DNS resolution errors
    - Network unreachable errors
    """

    def __str__(self) -> str:
        """Return MCP connection-specific error representation."""
        return super().__str__()


class MCPSessionTerminatedError(ToolError):
    """Raised when MCP session is terminated and recovery fails.

    Covers:
    - Server-side session expiration (e.g., 15-minute timeout)
    - Session terminated after max reconnect attempts exhausted
    - Unrecoverable session state
    """

    def __str__(self) -> str:
        """Return MCP session-specific error representation."""
        return super().__str__()
