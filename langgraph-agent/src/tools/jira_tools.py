"""Wrapper for Jira MCP server tools.

This module provides a client for interacting with the Jira MCP server,
enabling Jira ticket operations like reading, searching, and commenting.
"""

import json
import logging
import re
from typing import Any

import httpx

from .base_mcp_client import BaseMCPClient
from ..exceptions import ValidationError

logger = logging.getLogger(__name__)

# Jira ticket ID pattern: PROJECT-NUMBER (e.g., PROJ-123, K8S-4567)
JIRA_TICKET_PATTERN = re.compile(r'^[A-Z][A-Z0-9]+-\d+$')


class JiraTools(BaseMCPClient):
    """Client for interacting with Jira MCP server using MCP protocol.

    Provides high-level methods for common Jira operations while handling
    MCP protocol details internally. All methods include input validation
    to catch errors early and provide clear error messages.
    """

    def __init__(self, mcp_endpoint: str, rest_base_url: str | None = None):
        """Initialize Jira tools client.

        Args:
            mcp_endpoint: URL of the Jira MCP server
                (e.g., http://jira-agent:8080/mcp/jira)
            rest_base_url: Base URL for direct REST API calls
                (e.g., http://jira-agent:8080). When provided, this takes
                precedence over the URL derived from mcp_endpoint, making
                transition calls robust to non-standard endpoint paths.
        """
        super().__init__(endpoint=mcp_endpoint)
        self._rest_base_url = rest_base_url

    def _validate_ticket_id(self, ticket_id: str) -> None:
        """Validate Jira ticket ID format.

        Args:
            ticket_id: Ticket ID to validate (e.g., PROJ-123)

        Raises:
            ValidationError: If ticket ID is invalid
        """
        if not ticket_id:
            raise ValidationError(
                "Ticket ID cannot be empty",
                field="ticket_id",
                value=ticket_id,
                agent_name=self.client_name
            )
        if not JIRA_TICKET_PATTERN.match(ticket_id.upper()):
            raise ValidationError(
                f"Invalid Jira ticket ID format: '{ticket_id}'. "
                "Expected format: PROJECT-NUMBER (e.g., PROJ-123, K8S-456)",
                field="ticket_id",
                value=ticket_id,
                agent_name=self.client_name
            )

    def _validate_jql(self, jql: str) -> None:
        """Validate JQL query is not empty.

        Args:
            jql: JQL query to validate

        Raises:
            ValidationError: If JQL is empty
        """
        if not jql or not jql.strip():
            raise ValidationError(
                "JQL query cannot be empty",
                field="jql",
                value=jql,
                agent_name=self.client_name
            )

    def _validate_comment(self, comment: str) -> None:
        """Validate comment text.

        Args:
            comment: Comment text to validate

        Raises:
            ValidationError: If comment is empty or too long
        """
        if not comment or not comment.strip():
            raise ValidationError(
                "Comment cannot be empty",
                field="comment",
                value=comment,
                agent_name=self.client_name
            )
        # Jira has a limit of approximately 32,767 characters for comments
        max_length = 32000
        if len(comment) > max_length:
            raise ValidationError(
                f"Comment too long: {len(comment)} characters. "
                f"Maximum allowed: {max_length} characters",
                field="comment",
                value=f"[{len(comment)} chars]",
                agent_name=self.client_name
            )

    async def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Fetch complete details of a Jira ticket.

        Args:
            ticket_id: Jira ticket ID (e.g., PROJ-123)

        Returns:
            Dictionary with ticket details wrapped for agent compatibility:
            - content: The ticket content
            - raw: Raw response from MCP

        Raises:
            ValidationError: If ticket ID is invalid
            MCPConnectionError: If connection to MCP server fails
        """
        self._validate_ticket_id(ticket_id)

        response = await self.call_tool("get_ticket", {"ticket_id": ticket_id.upper()})
        logger.info(f"Retrieved ticket {ticket_id}")
        return {"content": response, "raw": response}

    async def search_tickets(self, jql: str, limit: int = 5) -> dict[str, Any]:
        """Search for similar tickets using JQL or text search.

        Args:
            jql: JQL query or text to search
            limit: Maximum number of results (1-100)

        Returns:
            Dictionary with search results wrapped for agent compatibility:
            - content: The search results
            - raw: Raw response from MCP

        Raises:
            ValidationError: If JQL is empty or limit is invalid
            MCPConnectionError: If connection to MCP server fails
        """
        self._validate_jql(jql)
        if limit < 1 or limit > 100:
            raise ValidationError(
                f"Limit must be between 1 and 100, got: {limit}",
                field="limit",
                value=str(limit),
                agent_name=self.client_name
            )

        response = await self.call_tool("search_tickets", {"jql": jql, "max_results": limit})
        logger.info("Searched tickets with JQL query (length=%d)", len(jql))
        return {"content": response, "raw": response}

    async def add_comment(self, ticket_id: str, comment: str) -> dict[str, Any]:
        """Add a comment to a Jira ticket.

        Args:
            ticket_id: Jira ticket ID
            comment: Comment text (supports Jira markdown)

        Returns:
            Dictionary with comment details:
            - content: Response from MCP
            - success: Whether the comment was added successfully
            - comment_id: ID of the created comment (if available)
            - created: Jira timestamp when comment was created (if available)
            - raw: Raw response from MCP

        Raises:
            ValidationError: If ticket ID or comment is invalid
            MCPConnectionError: If connection to MCP server fails
        """
        self._validate_ticket_id(ticket_id)
        self._validate_comment(comment)

        response = await self.call_tool("add_comment", {
            "ticket_id": ticket_id.upper(),
            "comment": comment
        })
        logger.info(f"Added comment to {ticket_id}")

        result = {"content": response, "success": False, "raw": response}

        # Parse JSON response to extract comment details
        # Response format: {"status":"success","ticket_id":"X","comment_id":"Y","created":"Z"}
        if response:
            try:
                data = json.loads(response)
                if data.get("status") == "success":
                    result["success"] = True
                    result["comment_id"] = data.get("comment_id")
                    result["created"] = data.get("created")
                    logger.debug(f"Comment created: id={data.get('comment_id')}, created={data.get('created')}")
            except (json.JSONDecodeError, TypeError):
                # Fallback for legacy response format
                logger.warning(
                    "add_comment: unexpected non-JSON response from MCP; "
                    "falling back to string detection"
                )
                result["success"] = "success" in str(response).lower()

        return result

    async def add_label(self, ticket_id: str, label: str) -> dict[str, Any]:
        """Add a label to a Jira ticket.

        Args:
            ticket_id: Jira ticket ID (e.g., PROJ-123)
            label: Label to add (e.g., ai-agent-investigated)

        Returns:
            Dictionary with success status:
            - content: Response from MCP
            - success: Whether the label was added successfully
            - raw: Raw response from MCP

        Raises:
            ValidationError: If ticket ID or label is invalid
            MCPConnectionError: If connection to MCP server fails
        """
        self._validate_ticket_id(ticket_id)
        if not label or not label.strip():
            raise ValidationError(
                "Label cannot be empty",
                field="label",
                value=label,
                agent_name=self.client_name
            )

        response = await self.call_tool("add_label", {
            "ticket_id": ticket_id.upper(),
            "label": label.strip()
        })
        logger.info(f"Added label '{label}' to {ticket_id}")

        result: dict[str, Any] = {"content": response, "success": False, "raw": response}
        if response:
            try:
                data = json.loads(response)
                result["success"] = data.get("status") == "success"
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "add_label: unexpected non-JSON response from MCP; "
                    "falling back to string detection"
                )
                result["success"] = "successfully" in str(response).lower()
        return result

    async def remove_label(self, ticket_id: str, label: str) -> dict[str, Any]:
        """Remove a label from a Jira ticket.

        Args:
            ticket_id: Jira ticket ID (e.g., PROJ-123)
            label: Label to remove

        Returns:
            Dictionary with success status:
            - content: Response from MCP
            - success: Whether the label was removed successfully
            - raw: Raw response from MCP

        Raises:
            ValidationError: If ticket ID or label is invalid
            MCPConnectionError: If connection to MCP server fails
        """
        self._validate_ticket_id(ticket_id)
        if not label or not label.strip():
            raise ValidationError(
                "Label cannot be empty",
                field="label",
                value=label,
                agent_name=self.client_name
            )

        response = await self.call_tool("remove_label", {
            "ticket_id": ticket_id.upper(),
            "label": label.strip()
        })
        logger.info(f"Removed label '{label}' from {ticket_id}")

        result: dict[str, Any] = {"content": response, "success": False, "raw": response}
        if response:
            try:
                data = json.loads(response)
                result["success"] = data.get("status") == "success"
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "remove_label: unexpected non-JSON response from MCP; "
                    "falling back to string detection"
                )
                result["success"] = "successfully" in str(response).lower()
        return result

    async def update_issue(
        self, ticket_id: str, summary: str | None = None, description: str | None = None
    ) -> dict[str, Any]:
        """Update a Jira ticket's summary and/or description.

        Replaces the full field value server-side — there is no append.

        Args:
            ticket_id: Jira ticket ID (e.g., PROJ-123)
            summary: New summary/title (omit to leave unchanged)
            description: New description (omit to leave unchanged)

        Returns:
            Dictionary with success status:
            - content: Response from MCP
            - success: Whether the update succeeded
            - raw: Raw response from MCP

        Raises:
            ValidationError: If ticket ID is invalid or neither field is given
            MCPConnectionError: If connection to MCP server fails
        """
        self._validate_ticket_id(ticket_id)
        if summary is None and description is None:
            raise ValidationError(
                "At least one of summary or description is required",
                field="summary/description",
                value=None,
                agent_name=self.client_name,
            )

        params: dict[str, Any] = {"ticket_id": ticket_id.upper()}
        if summary is not None:
            params["summary"] = summary
        if description is not None:
            params["description"] = description

        response = await self.call_tool("update_issue", params)
        logger.info(f"Updated {ticket_id}")

        result: dict[str, Any] = {"content": response, "success": False, "raw": response}
        if response:
            try:
                data = json.loads(response)
                result["success"] = data.get("status") == "success"
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "update_issue: unexpected non-JSON response from MCP; "
                    "falling back to string detection"
                )
                result["success"] = "updated" in str(response).lower()
        return result

    def _transition_base_url(self) -> str:
        """Return the REST base URL for direct API calls.

        Prefers the explicit rest_base_url passed at construction.
        Falls back to deriving from the MCP endpoint for backward compatibility.
        """
        if self._rest_base_url:
            return self._rest_base_url.rstrip("/")
        endpoint = self.endpoint.rstrip("/")
        mcp_suffix = "/mcp/jira"
        if endpoint.endswith(mcp_suffix):
            return endpoint[: -len(mcp_suffix)]
        logger.warning(
            "Unexpected MCP endpoint format %r (expected suffix %r); "
            "REST transitions may fail",
            self.endpoint,
            mcp_suffix,
        )
        return endpoint.rsplit("/", 1)[0]

    async def _do_transition(self, ticket_id: str, status_path: str, status_name: str) -> dict[str, Any]:
        """Call the REST transition endpoint directly (bypasses MCP).

        Intentionally fire-and-forget: transitions are best-effort status
        updates that must not abort a workflow. Always returns a dict; never
        raises.

        Return dict always contains:
          - success (bool): True only if HTTP 2xx AND status=="success" in body
          - content (dict | str): parsed JSON body, or raw text on parse failure
          - error (str): present and non-None only when success is False
        """
        try:
            self._validate_ticket_id(ticket_id)
            url = f"{self._transition_base_url()}/api/transition/{ticket_id.upper()}/{status_path}"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url)
            try:
                data = resp.json()
            except Exception:
                data = {"status": "error", "error": resp.text[:200]}
            success = resp.is_success and data.get("status") == "success"
            if success:
                logger.debug(f"Transitioned {ticket_id} to {status_name}")
            else:
                logger.warning(f"Transition {ticket_id} to {status_name} returned: {data}")
            error = data.get("error") if not success else None
            result: dict[str, Any] = {"content": data, "success": success}
            if error:
                result["error"] = error
            return result
        except Exception as e:
            logger.warning(f"Failed to transition {ticket_id} to {status_name}: {e}")
            return {"content": str(e), "success": False, "error": str(e)}

    async def move_to_in_progress(self, ticket_id: str) -> dict[str, Any]:
        """Move a Jira ticket to In Progress status.

        Fire-and-forget: returns error dict on failure instead of raising.
        """
        return await self._do_transition(ticket_id, "in-progress", "In Progress")

    async def move_to_in_review(self, ticket_id: str) -> dict[str, Any]:
        """Move a Jira ticket to In Review status.

        Fire-and-forget: returns error dict on failure instead of raising.
        """
        return await self._do_transition(ticket_id, "in-review", "In Review")

    async def update_assignee(self, ticket_id: str, username: str) -> dict[str, Any]:
        """Assign a Jira ticket to a user by username (self-hosted Jira Server).

        Args:
            ticket_id: Jira ticket ID (e.g., GAUDISW-123)
            username: Jira username to assign to (e.g., jdoe)

        Returns:
            Dictionary with success status.
        """
        self._validate_ticket_id(ticket_id)
        if not username or not username.strip():
            raise ValidationError(
                "Username cannot be empty",
                field="username",
                value=username,
                agent_name=self.client_name,
            )

        response = await self.call_tool("update_assignee", {
            "ticket_id": ticket_id.upper(),
            "username": username.strip(),
        })
        logger.info("Assigned %s to %s", ticket_id, username)

        result: dict[str, Any] = {"content": response, "success": False, "raw": response}
        if response:
            try:
                data = json.loads(response)
                result["success"] = data.get("status") == "success"
            except (json.JSONDecodeError, TypeError):
                result["success"] = "success" in str(response).lower()
        return result

    async def get_reporter(self, ticket_id: str) -> str | None:
        """Return the reporter's Jira username for a ticket, or None if unavailable."""
        self._validate_ticket_id(ticket_id)
        try:
            result = await self.get_ticket(ticket_id)
            content = result.get("content", "")
            # The Go MCP server includes "Reporter: <username>" in the formatted output
            # when we add it to formatTicketOutput. Until then, fall back to None.
            for line in (content or "").splitlines():
                if line.startswith("Reporter:"):
                    return line.removeprefix("Reporter:").strip() or None
        except Exception as exc:
            logger.warning("Could not fetch reporter for %s: %s", ticket_id, exc)
        return None
