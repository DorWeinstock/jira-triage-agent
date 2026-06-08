"""Unit tests for HistoryAgent ticket parsing.

These tests verify that _parse_tickets correctly handles all MCP response formats.
"""

import pytest
from unittest.mock import Mock, AsyncMock


class TestParseTickets:
    """Tests for HistoryAgent._parse_tickets method."""

    @pytest.fixture
    def history_agent(self):
        """Create a HistoryAgent with mocked dependencies."""
        # Import here to avoid circular imports during collection
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.history_agent import HistoryAgent

        mock_jira_tools = Mock()
        agent = HistoryAgent(mock_jira_tools)
        return agent

    def test_parse_go_server_format(self, history_agent):
        """Test parsing the actual Go MCP server format: '- KEY: Summary'"""
        response = """Found 3 tickets:
- SP-3799: order-service is down in production - no pods running
- SP-3800: order-service is down in production - no pods running
- SP-3801: order-service is down in production - no pods running"""

        result = history_agent._parse_tickets(response)

        assert len(result) == 3
        assert result[0]['key'] == 'SP-3799'
        assert result[0]['summary'] == 'order-service is down in production - no pods running'
        assert result[1]['key'] == 'SP-3800'
        assert result[2]['key'] == 'SP-3801'

    def test_parse_go_server_format_with_dict_wrapper(self, history_agent):
        """Test parsing when response is wrapped in dict with 'content' key."""
        response = {
            'content': """Found 2 tickets:
- PROJ-123: Database connection timeout
- PROJ-456: Memory limit exceeded"""
        }

        result = history_agent._parse_tickets(response)

        assert len(result) == 2
        assert result[0]['key'] == 'PROJ-123'
        assert result[0]['summary'] == 'Database connection timeout'
        assert result[1]['key'] == 'PROJ-456'

    def test_parse_legacy_format(self, history_agent):
        """Test parsing legacy format: '**KEY**: Summary' with '---' separators."""
        response = """---
**SP-100**: First ticket
Status: Done
Resolution: Fixed
---
**SP-101**: Second ticket
Status: Closed
Resolution: Won't Fix
---"""

        result = history_agent._parse_tickets(response)

        assert len(result) == 2
        assert result[0]['key'] == 'SP-100'
        assert result[0]['summary'] == 'First ticket'
        assert result[0].get('status') == 'Done'
        assert result[0].get('resolution') == 'Fixed'
        assert result[1]['key'] == 'SP-101'

    def test_parse_empty_response(self, history_agent):
        """Test parsing 'No tickets found' response."""
        result = history_agent._parse_tickets("No tickets found matching criteria")
        assert result == []

    def test_parse_error_response(self, history_agent):
        """Test parsing error response."""
        result = history_agent._parse_tickets("Error: Connection timeout")
        assert result == []

    def test_parse_list_response(self, history_agent):
        """Test parsing when response is already a list."""
        response = [
            {'key': 'ABC-1', 'summary': 'First'},
            {'key': 'ABC-2', 'summary': 'Second'}
        ]

        result = history_agent._parse_tickets(response)

        assert len(result) == 2
        assert result[0]['key'] == 'ABC-1'

    def test_parse_ignores_non_ticket_lines(self, history_agent):
        """Test that non-ticket lines are ignored."""
        response = """Found 1 tickets:
- SP-999: Actual ticket
- not-a-key: this should be ignored
Some other text
- another line without proper format"""

        result = history_agent._parse_tickets(response)

        assert len(result) == 1
        assert result[0]['key'] == 'SP-999'

    def test_parse_various_project_keys(self, history_agent):
        """Test parsing tickets with different project key formats."""
        response = """Found 4 tickets:
- ABC-1: Short key
- LONGPROJECT-99999: Long project key
- X-1: Single letter project
- SW-242654: Real example from Jira"""

        result = history_agent._parse_tickets(response)

        assert len(result) == 4
        keys = [t['key'] for t in result]
        assert 'ABC-1' in keys
        assert 'LONGPROJECT-99999' in keys
        assert 'X-1' in keys
        assert 'SW-242654' in keys

    def test_parse_with_colons_in_summary(self, history_agent):
        """Test parsing summaries that contain colons."""
        response = """Found 1 tickets:
- SP-100: Error: Connection refused: port 8080"""

        result = history_agent._parse_tickets(response)

        assert len(result) == 1
        assert result[0]['key'] == 'SP-100'
        assert result[0]['summary'] == 'Error: Connection refused: port 8080'

    def test_parse_go_server_format_with_components(self, history_agent):
        """Test parsing Go format with component tags."""
        response = """Found 2 tickets:
- SP-100 [2026-01-15] (Done) {order-service,payments}: order crash
- SP-101 [2026-01-10] (OPEN): generic issue without components"""

        result = history_agent._parse_tickets(response)

        assert len(result) == 2
        assert result[0]['key'] == 'SP-100'
        assert result[0]['components'] == ['order-service', 'payments']
        assert result[0]['status'] == 'Done'
        assert result[1]['key'] == 'SP-101'
        assert result[1]['components'] == []

    def test_parse_go_server_format_single_component(self, history_agent):
        """Test parsing Go format with single component."""
        response = """Found 1 tickets:
- SP-200 [2026-01-15] (Resolved) {api-gateway}: timeout"""

        result = history_agent._parse_tickets(response)

        assert len(result) == 1
        assert result[0]['components'] == ['api-gateway']
