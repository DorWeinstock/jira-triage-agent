"""Unified MCP response parsing with multiple format support.

This module centralizes all MCP response parsing logic to:
- Handle multiple response formats consistently
- Reduce code duplication across agents
- Provide clear error handling
- Centralize success/failure detection
"""

import functools
import json
import logging
import re
from enum import Enum
from typing import Any

from ..constants import ERROR_INDICATORS, SUCCESS_INDICATORS

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=None)
def _compile_error_pattern(indicator: str) -> re.Pattern:
    """Return a compiled word-boundary regex for *indicator* (cached, thread-safe)."""
    return re.compile(rf"\b{re.escape(indicator)}\b", re.IGNORECASE)


@functools.lru_cache(maxsize=None)
def _compile_success_pattern(indicator: str) -> re.Pattern:
    """Return a compiled word-boundary regex for *indicator* (cached, thread-safe)."""
    return re.compile(rf"\b{re.escape(indicator)}\b", re.IGNORECASE)


class MCPResponseFormat(Enum):
    """Detected format of MCP response."""

    JSON = "json"
    MARKDOWN = "markdown"
    NESTED_CONTENT = "nested_content"
    PLAIN_TEXT = "plain_text"
    UNKNOWN = "unknown"


class MCPResponseParser:
    """Unified parser for all MCP response formats.

    Handles:
    - Direct JSON responses
    - Markdown-formatted text responses
    - Nested content structures
    - Plain text fallbacks

    Example usage:
        parser = MCPResponseParser()
        data = parser.parse(mcp_response)
        if parser.is_success(data):
            process(data)
    """

    # Patterns for markdown parsing
    SUMMARY_PATTERN = re.compile(r"Summary:\s*(.+?)(?:\n|$)")
    DESCRIPTION_PATTERN = re.compile(
        r"\*\*Description:\*\*\s*\n(.+?)(?:\n\n\*\*|\n\*\*|$)", re.DOTALL
    )
    DESCRIPTION_SIMPLE_PATTERN = re.compile(
        r"Description:\s*\n(.+?)(?:\n\n|\n\*\*|$)", re.DOTALL
    )
    STATUS_PATTERN = re.compile(r"Status:\s*(.+?)(?:\n|$)")
    PRIORITY_PATTERN = re.compile(r"Priority:\s*(.+?)(?:\n|$)")
    KEY_PATTERN = re.compile(r"(?:Key|Ticket):\s*([A-Z][A-Z0-9]+-\d+)")

    # Markers for markdown format detection
    MARKDOWN_MARKERS = ("**Ticket Information**", "Summary:", "**Summary:**")

    @classmethod
    def detect_format(cls, response: Any) -> MCPResponseFormat:
        """Detect the format of an MCP response.

        Args:
            response: Raw MCP response (dict, str, or other)

        Returns:
            Detected format enum
        """
        if response is None:
            return MCPResponseFormat.UNKNOWN

        if isinstance(response, dict):
            if "content" in response and isinstance(response["content"], str):
                content = response["content"]
                if any(marker in content for marker in cls.MARKDOWN_MARKERS):
                    return MCPResponseFormat.MARKDOWN
                try:
                    json.loads(content)
                    return MCPResponseFormat.NESTED_CONTENT
                except (json.JSONDecodeError, TypeError):
                    return MCPResponseFormat.PLAIN_TEXT
            return MCPResponseFormat.JSON

        if isinstance(response, str):
            if any(marker in response for marker in cls.MARKDOWN_MARKERS):
                return MCPResponseFormat.MARKDOWN
            try:
                json.loads(response)
                return MCPResponseFormat.JSON
            except (json.JSONDecodeError, TypeError):
                return MCPResponseFormat.PLAIN_TEXT

        return MCPResponseFormat.UNKNOWN

    @classmethod
    def parse(cls, response: Any) -> dict[str, Any]:
        """Parse MCP response into a consistent dictionary format.

        Automatically detects format and parses accordingly.

        Args:
            response: Raw MCP response

        Returns:
            Parsed dictionary with extracted data
        """
        response_format = cls.detect_format(response)
        logger.debug(f"Detected MCP response format: {response_format.value}")

        if response_format == MCPResponseFormat.UNKNOWN:
            return {"error": "Unknown response format", "raw": str(response)}

        if response_format == MCPResponseFormat.JSON:
            return cls._parse_json(response)

        if response_format == MCPResponseFormat.MARKDOWN:
            return cls._parse_markdown(response)

        if response_format == MCPResponseFormat.NESTED_CONTENT:
            return cls._parse_nested_content(response)

        if response_format == MCPResponseFormat.PLAIN_TEXT:
            return cls._parse_plain_text(response)

        return {"error": "Unhandled format", "raw": str(response)}

    @classmethod
    def _parse_json(cls, response: dict | str) -> dict[str, Any]:
        """Parse JSON format response."""
        if isinstance(response, str):
            try:
                return json.loads(response)
            except json.JSONDecodeError as e:
                return {"error": f"JSON parse error: {e}", "raw": response}

        if isinstance(response, dict):
            # Handle Jira API nested structure
            if "fields" in response:
                return cls._flatten_jira_fields(response)
            return response

        return {"error": "Invalid JSON format", "raw": str(response)}

    @classmethod
    def _flatten_jira_fields(cls, response: dict) -> dict[str, Any]:
        """Flatten Jira API nested fields structure."""
        fields = response.get("fields", {})
        result = {
            "key": response.get("key", ""),
            "summary": fields.get("summary", "Unknown issue"),
            "description": fields.get("description", ""),
            "labels": fields.get("labels", []),
        }

        # Handle nested priority
        priority = fields.get("priority", {})
        result["priority"] = (
            priority.get("name", "Unknown") if isinstance(priority, dict) else str(priority)
        )

        # Handle nested status
        status = fields.get("status", {})
        result["status"] = (
            status.get("name", "Unknown") if isinstance(status, dict) else str(status)
        )

        return result

    @classmethod
    def _parse_markdown(cls, response: dict | str) -> dict[str, Any]:
        """Parse markdown-formatted response."""
        content = response
        if isinstance(response, dict) and "content" in response:
            content = response["content"]

        if not isinstance(content, str):
            return {"error": "Invalid markdown content", "raw": str(response)}

        result = {
            "key": "",
            "summary": "Unknown issue",
            "description": "",
            "labels": [],
            "priority": "Unknown",
            "status": "Unknown",
        }

        # Extract Key
        key_match = cls.KEY_PATTERN.search(content)
        if key_match:
            result["key"] = key_match.group(1).strip()

        # Extract Summary
        summary_match = cls.SUMMARY_PATTERN.search(content)
        if summary_match:
            result["summary"] = summary_match.group(1).strip()

        # Extract Description
        desc_match = cls.DESCRIPTION_PATTERN.search(content)
        if desc_match:
            result["description"] = desc_match.group(1).strip()
        else:
            desc_match = cls.DESCRIPTION_SIMPLE_PATTERN.search(content)
            if desc_match:
                result["description"] = desc_match.group(1).strip()

        # Extract Status
        status_match = cls.STATUS_PATTERN.search(content)
        if status_match:
            result["status"] = status_match.group(1).strip()

        # Extract Priority
        priority_match = cls.PRIORITY_PATTERN.search(content)
        if priority_match:
            result["priority"] = priority_match.group(1).strip()

        return result

    @classmethod
    def _parse_nested_content(cls, response: dict) -> dict[str, Any]:
        """Parse response with nested JSON content."""
        content = response.get("content", "")
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError) as e:
            return {"error": f"Nested content parse error: {e}", "raw": content}

    @classmethod
    def _parse_plain_text(cls, response: dict | str) -> dict[str, Any]:
        """Parse plain text response."""
        content = response
        if isinstance(response, dict) and "content" in response:
            content = response["content"]

        return {
            "summary": "Unknown issue",
            "description": str(content) if content else "",
            "labels": [],
            "priority": "Unknown",
            "status": "Unknown",
        }

    @classmethod
    def is_success(cls, response: Any) -> bool:
        """Check if a response indicates success.

        Centralizes the success detection logic that was duplicated
        7 times across the codebase as: "error" not in str(output).lower()

        Uses word boundary matching to avoid false positives where
        error keywords appear as part of other words (e.g., "error_handling"
        should not trigger an error detection for "error").

        Detection logic (in priority order):
        1. None or empty string/collection → False (explicit no-op guard)
        2. Any ERROR_INDICATORS keyword found → False
        3. Any SUCCESS_INDICATORS keyword found → True (explicit positive signal)
        4. Non-empty response with no error keywords → True (kubectl/tool outputs
           like "deployment restarted" don't match SUCCESS_INDICATORS vocabulary)

        Args:
            response: Response to check (string, dict, or other)

        Returns:
            True if response indicates success
        """
        if response is None:
            return False

        # Reject empty string / empty collection early
        if isinstance(response, (str, dict, list)) and not response:
            return False

        # Convert to string for keyword scanning
        response_str = str(response).lower()

        # Fail on any error indicator (word-boundary matched)
        for indicator in ERROR_INDICATORS:
            pattern = _compile_error_pattern(indicator)
            if pattern.search(response_str):
                return False

        # Explicit positive signal → definitely success
        for indicator in SUCCESS_INDICATORS:
            pattern = _compile_success_pattern(indicator)
            if pattern.search(response_str):
                return True

        # Non-empty, no error keywords → treat as success (preserves kubectl output compat)
        return True

    @classmethod
    def is_error(cls, response: Any) -> bool:
        """Check if a response indicates an error.

        Args:
            response: Response to check

        Returns:
            True if response indicates an error
        """
        return not cls.is_success(response)

    @classmethod
    def extract_json(cls, content: str) -> dict[str, Any] | list | None:
        """Extract JSON from a string that may contain other text.

        Handles both top-level objects ({...}) and arrays ([...]).
        Picks whichever delimiter appears first in the string.

        Args:
            content: String that may contain JSON

        Returns:
            Parsed JSON value (dict or list) or None if not found
        """
        if not content:
            return None

        obj_start = content.find("{")
        arr_start = content.find("[")

        # Build candidates for both delimiter types, ignoring any that are absent
        candidates = [
            (idx, open_c, close_c)
            for idx, open_c, close_c in [
                (obj_start, "{", "}"),
                (arr_start, "[", "]"),
            ]
            if idx != -1
        ]

        if not candidates:
            return None

        # Pick whichever valid delimiter appears earliest in the string
        start_idx, open_c, close_c = min(candidates, key=lambda t: t[0])

        # Walk forward tracking bracket depth to find the matching closing delimiter
        depth = 0
        end_idx = -1
        for i, char in enumerate(content[start_idx:], start=start_idx):
            if char == open_c:
                depth += 1
            elif char == close_c:
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break

        if end_idx == -1:
            return None

        json_str = content[start_idx : end_idx + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.debug(f"Failed to parse extracted JSON: {json_str[:100]}...")
            return None
