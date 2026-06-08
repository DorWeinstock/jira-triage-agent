"""Unit tests for parse_ticket_response() helper in HistoryAgent.

These tests verify that parse_ticket_response() correctly handles all response
formats and validates structure with Pydantic JiraTicketResponse model.
"""

import pytest
from src.agents.history_agent import parse_ticket_response
from src.models.llm_outputs import JiraTicketResponse


class TestParseTicketResponse:
    """Tests for parse_ticket_response() helper function."""

    def test_parse_go_server_text_format(self):
        """Test parsing Go server text response with summary and resolution."""
        response = """Ticket: SP-100
Summary: Order service is down in production
Status: Done
Resolution: Restarted pods and updated image
--- Comment 1 (by john@example.com on 2026-01-15T10:00:00) ---
Applied fix as described in runbook.
--- Comment 2 (by jane@example.com on 2026-01-15T11:00:00) ---
Confirmed service is stable."""

        result = parse_ticket_response(response)

        assert isinstance(result, JiraTicketResponse)
        assert result.summary == "Order service is down in production"
        assert result.resolution == "Restarted pods and updated image"
        assert "Applied fix" in result.last_comment
        assert result.is_resolved is True  # inferred from "Done" status

    def test_parse_response_with_dict_wrapper(self):
        """Test parsing response wrapped in dict with 'content' key."""
        response = {
            "content": """Ticket: PROJ-456
Summary: Database connection pool exhausted
Status: Closed
Resolution: Upgraded connection limit"""
        }

        result = parse_ticket_response(response)

        assert isinstance(result, JiraTicketResponse)
        assert result.summary == "Database connection pool exhausted"
        assert result.resolution == "Upgraded connection limit"

    def test_parse_response_with_raw_key(self):
        """Test parsing response wrapped in dict with 'raw' key."""
        response = {
            "raw": """Ticket: INFRA-789
Summary: DNS resolution timeout
Status: Open
Resolution: """
        }

        result = parse_ticket_response(response)

        assert isinstance(result, JiraTicketResponse)
        assert result.summary == "DNS resolution timeout"
        assert result.status == "Open"
        assert result.is_resolved is False  # inferred from "Open" status

    def test_parse_structured_dict_response(self):
        """Test parsing structured dict response (future format)."""
        response = {
            "key": "TEST-123",
            "summary": "Memory limit exceeded",
            "description": "Pod getting OOMKilled",
            "status": "In Progress",
            "resolution": "",
            "last_comment": "Investigating heap dump",
            "is_resolved": False,
            "components": ["backend", "monitoring"]
        }

        result = parse_ticket_response(response)

        assert isinstance(result, JiraTicketResponse)
        assert result.key == "TEST-123"
        assert result.summary == "Memory limit exceeded"
        assert result.description == "Pod getting OOMKilled"
        assert result.components == ["backend", "monitoring"]

    def test_parse_response_empty_string(self):
        """Test parsing empty string response (invalid, should raise error)."""
        with pytest.raises(ValueError, match="Empty ticket response"):
            parse_ticket_response("")

    def test_parse_response_error_message(self):
        """Test parsing Go server error response."""
        response = "Error: ticket not found"

        with pytest.raises(ValueError, match="Go server returned error"):
            parse_ticket_response(response)

    def test_parse_response_no_tickets_found(self):
        """Test parsing 'no tickets found' response."""
        response = "No tickets found matching criteria"

        with pytest.raises(ValueError, match="Go server returned error"):
            parse_ticket_response(response)

    def test_parse_response_multiline_summary(self):
        """Test parsing response with summary on multiple lines (edge case)."""
        response = """Ticket: EDGE-111
Summary: This is a very long summary that might have
Status: Done
Resolution: Fixed it"""

        result = parse_ticket_response(response)

        # regex stops at newline, so only first line captured
        assert result.summary == "This is a very long summary that might have"

    def test_parse_response_comment_extraction(self):
        """Test that most recent comment (first match) is extracted."""
        response = """Ticket: TEST-999
Summary: Test ticket
Status: Done
Resolution: Fixed
--- Comment 1 (by alice@example.com on 2026-01-15T10:00:00) ---
This is the first comment (should be extracted).
--- Comment 2 (by bob@example.com on 2026-01-15T11:00:00) ---
This is the second comment (should not be extracted)."""

        result = parse_ticket_response(response)

        assert "This is the first comment" in result.last_comment
        assert "second comment" not in result.last_comment

    def test_parse_response_comment_truncation(self):
        """Test that comments longer than 500 chars are truncated."""
        response = """Ticket: LONG-555
Summary: Test
Status: Done
Resolution: Fixed
--- Comment 1 (by user@example.com on 2026-01-15T10:00:00) ---
""" + "A" * 600

        result = parse_ticket_response(response)

        assert len(result.last_comment) == 500

    def test_parse_response_unsupported_type(self):
        """Test parsing unsupported response type (int, list, etc)."""
        with pytest.raises(ValueError, match="Unsupported ticket response type"):
            parse_ticket_response(12345)

        with pytest.raises(ValueError, match="Unsupported ticket response type"):
            parse_ticket_response([1, 2, 3])

    def test_parse_response_dict_with_none_content(self):
        """Test parsing dict with None content falls back to dict fields."""
        response = {
            "content": None,
            "summary": "Fallback summary",
            "key": "FALL-111",
            "status": "Done"
        }

        result = parse_ticket_response(response)

        assert result.summary == "Fallback summary"
        assert result.key == "FALL-111"
        assert result.is_resolved is True

    def test_is_resolved_inference_from_status(self):
        """Test that is_resolved is correctly inferred from status field."""
        # Resolved statuses
        for status in ["Done", "Closed", "DONE", "closed"]:
            response = f"""Ticket: TEST-{status}
Summary: Test
Status: {status}
Resolution: Fixed"""
            result = parse_ticket_response(response)
            assert result.is_resolved is True, f"Expected is_resolved=True for status '{status}'"

        # Unresolved statuses
        for status in ["Open", "To Do", "In Progress", "In Review"]:
            response = f"""Ticket: TEST-{status}
Summary: Test
Status: {status}
Resolution: """
            result = parse_ticket_response(response)
            assert result.is_resolved is False, f"Expected is_resolved=False for status '{status}'"
