"""Unit tests for Diagnostician._parse_json_response hardening.

Tests verify that JSON parsing:
- Extracts from fenced blocks (```json ... ```)
- Extracts from bare objects ({...})
- Raises ToolError when no JSON found
- Raises ToolError when JSON is invalid
- Raises ValidationError when JSON doesn't match schema
"""

import json
import pytest
from pydantic import BaseModel, Field, ValidationError

from src.agents.diagnostician import Diagnostician
from src.exceptions import ToolError


class SampleModel(BaseModel):
    """Simple model for testing JSON parsing."""
    root_cause: str = Field(..., min_length=10)
    action: str = Field(default="unknown")


class TestParseJsonResponse:
    """Test _parse_json_response with various inputs."""

    @staticmethod
    def parse(content: str):
        """Helper to call _parse_json_response."""
        return Diagnostician._parse_json_response(content, SampleModel)

    def test_parses_fenced_json_block(self):
        """Extract JSON from ```json ... ``` fencing."""
        content = '''
Some explanation here.

```json
{
  "root_cause": "This is a detailed root cause explanation",
  "action": "restart"
}
```

More text after.
'''
        result = self.parse(content)
        assert result.root_cause == "This is a detailed root cause explanation"
        assert result.action == "restart"

    def test_parses_bare_json_object(self):
        """Extract raw JSON object without fencing."""
        content = 'Some text {"root_cause": "A very detailed root cause here", "action": "scale"} more text'
        result = self.parse(content)
        assert result.root_cause == "A very detailed root cause here"
        assert result.action == "scale"

    def test_raises_tool_error_on_no_json(self):
        """No JSON object in response raises ToolError."""
        content = "This response has no JSON object at all, just plain text."
        with pytest.raises(ToolError) as exc_info:
            self.parse(content)
        assert "No JSON object found" in str(exc_info.value)

    def test_raises_tool_error_on_invalid_json(self):
        """Invalid JSON syntax raises ToolError."""
        content = '```json\n{"root_cause": "Missing closing brace"\n```'
        with pytest.raises(ToolError) as exc_info:
            self.parse(content)
        assert "Invalid JSON" in str(exc_info.value)

    def test_raises_validation_error_on_bad_schema(self):
        """Valid JSON but wrong schema raises ValidationError (Pydantic)."""
        content = '{"root_cause": "short", "action": "scale"}'
        # root_cause is too short (min_length=10)
        with pytest.raises(ValidationError):
            self.parse(content)

    def test_error_message_includes_context(self):
        """Error messages include a preview of the raw content."""
        content = "Invalid response with no JSON"
        with pytest.raises(ToolError) as exc_info:
            self.parse(content)
        error_msg = str(exc_info.value)
        # Error should include context (raw content preview)
        assert "Invalid response" in error_msg or "No JSON" in error_msg

    def test_parses_fenced_json_without_language(self):
        """Extract JSON from ``` ... ``` (no 'json' language specified)."""
        content = '''
```
{
  "root_cause": "Detailed explanation of the problem here",
  "action": "restart"
}
```
'''
        result = self.parse(content)
        assert result.root_cause == "Detailed explanation of the problem here"

    def test_parses_json_with_extra_whitespace(self):
        """JSON parsing handles whitespace and formatting."""
        content = '''{
  "root_cause": "Root cause with proper spacing here",
  "action":  "delete"
}'''
        result = self.parse(content)
        assert result.root_cause == "Root cause with proper spacing here"
        assert result.action == "delete"
