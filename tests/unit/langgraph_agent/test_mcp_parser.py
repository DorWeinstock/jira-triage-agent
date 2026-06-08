"""Unit tests for langgraph_agent.src.utils.mcp_parser.

Covers all five issues fixed in the mcp_parser refactoring:
1. Thread-safe lru_cache pattern compilation
2. is_success using SUCCESS_INDICATORS
3. extract_json handling top-level arrays
4. _parse_markdown extracting the key field
5. Critical-path edge cases (nested braces, unbalanced, word boundaries)
"""

import threading

import pytest

from src.utils.mcp_parser import (
    MCPResponseFormat,
    MCPResponseParser,
    _compile_error_pattern,
    _compile_success_pattern,
)


# ---------------------------------------------------------------------------
# _compile_error_pattern / _compile_success_pattern (Step 1 — lru_cache)
# ---------------------------------------------------------------------------


class TestCompilePatterns:
    def test_error_pattern_lru_cache_same_object(self):
        """Same indicator must return the identical cached object."""
        assert _compile_error_pattern("failed") is _compile_error_pattern("failed")

    def test_success_pattern_lru_cache_same_object(self):
        assert _compile_success_pattern("running") is _compile_success_pattern("running")

    def test_error_word_boundary_prevents_compound_word_match(self):
        pattern = _compile_error_pattern("error")
        assert pattern.search("error_handling") is None
        assert pattern.search("an error occurred") is not None

    def test_success_word_boundary_prevents_compound_word_match(self):
        pattern = _compile_success_pattern("ready")
        assert pattern.search("notready") is None
        assert pattern.search("pod is ready") is not None

    def test_concurrent_access_is_race_free(self):
        """50 threads calling is_success concurrently must all agree."""
        results = []

        def check():
            results.append(MCPResponseParser.is_success("pod is running"))

        threads = [threading.Thread(target=check) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is True for r in results)


# ---------------------------------------------------------------------------
# is_success (Step 2 — SUCCESS_INDICATORS integration)
# ---------------------------------------------------------------------------


class TestIsSuccess:
    @pytest.mark.parametrize(
        "response",
        [
            "pod is running",
            "deployment is ready",
            {"status": "healthy"},
            {"key": "ABC-1", "summary": "thing"},  # non-empty dict, no errors
            "job succeeded",
            "deployment.apps/my-deploy restarted",  # kubectl output
            "configmap/my-cm created",              # kubectl output (no SUCCESS_INDICATORS word)
        ],
    )
    def test_positive_responses(self, response):
        assert MCPResponseParser.is_success(response) is True

    @pytest.mark.parametrize(
        "response",
        [
            None,
            "",
            {},
            [],
            "error: image pull failed",
            "pod in CrashLoopBackOff",
            "status: failed",
        ],
    )
    def test_negative_responses(self, response):
        assert MCPResponseParser.is_success(response) is False

    def test_compound_error_word_does_not_trigger(self):
        # "error_handling" contains "error" but as part of a compound identifier
        assert MCPResponseParser.is_success("error_handling routine restarted") is True

    def test_is_error_is_strict_inverse(self):
        assert MCPResponseParser.is_error("pod failed") is True
        assert MCPResponseParser.is_error("pod running") is False

    def test_none_is_not_success(self):
        assert MCPResponseParser.is_success(None) is False

    def test_empty_string_is_not_success(self):
        assert MCPResponseParser.is_success("") is False

    def test_empty_dict_is_not_success(self):
        assert MCPResponseParser.is_success({}) is False

    def test_empty_list_is_not_success(self):
        assert MCPResponseParser.is_success([]) is False


# ---------------------------------------------------------------------------
# extract_json (Step 3 — array support + edge cases)
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_object_extraction_basic(self):
        assert MCPResponseParser.extract_json('{"key": "val"}') == {"key": "val"}

    def test_object_extraction_with_surrounding_text(self):
        assert MCPResponseParser.extract_json('prefix {"key": "val"} suffix') == {
            "key": "val"
        }

    def test_array_extraction(self):
        result = MCPResponseParser.extract_json('[{"pod": "api-0"}, {"pod": "api-1"}]')
        assert result == [{"pod": "api-0"}, {"pod": "api-1"}]

    def test_object_before_array_picks_object(self):
        result = MCPResponseParser.extract_json('{"a": 1} and [1, 2, 3]')
        assert result == {"a": 1}

    def test_array_before_object_picks_array(self):
        result = MCPResponseParser.extract_json('[1, 2] then {"b": 2}')
        assert result == [1, 2]

    def test_nested_object_braces(self):
        result = MCPResponseParser.extract_json('{"a": {"b": {"c": 3}}}')
        assert result == {"a": {"b": {"c": 3}}}

    def test_nested_array_brackets(self):
        result = MCPResponseParser.extract_json("[[1, 2], [3, 4]]")
        assert result == [[1, 2], [3, 4]]

    def test_unbalanced_object_returns_none(self):
        assert MCPResponseParser.extract_json('{"unclosed": true') is None

    def test_unbalanced_array_returns_none(self):
        assert MCPResponseParser.extract_json('[1, 2, 3') is None

    def test_no_json_returns_none(self):
        assert MCPResponseParser.extract_json("no json here") is None

    def test_empty_string_returns_none(self):
        assert MCPResponseParser.extract_json("") is None

    def test_none_input_returns_none(self):
        assert MCPResponseParser.extract_json(None) is None  # type: ignore[arg-type]

    def test_malformed_json_returns_none(self):
        # Balanced braces but invalid JSON content
        assert MCPResponseParser.extract_json("{not: valid json}") is None


# ---------------------------------------------------------------------------
# _parse_markdown key field (Step 4)
# ---------------------------------------------------------------------------


_MD_WITH_KEY = (
    "**Ticket Information**\n"
    "Key: ABC-123\n"
    "Summary: Pod crashing on startup\n"
    "Status: Open\n"
    "Priority: High\n"
)

_MD_WITH_TICKET_PREFIX = (
    "**Ticket Information**\n"
    "Ticket: XY-99\n"
    "Summary: Alt prefix\n"
    "Status: Closed\n"
)

_MD_NO_KEY = (
    "**Ticket Information**\n"
    "Summary: No key present\n"
    "Status: Closed\n"
)


class TestParseMarkdownKeyField:
    def test_key_extracted_with_key_prefix(self):
        result = MCPResponseParser.parse(_MD_WITH_KEY)
        assert result["key"] == "ABC-123"

    def test_key_extracted_with_ticket_prefix(self):
        result = MCPResponseParser.parse(_MD_WITH_TICKET_PREFIX)
        assert result["key"] == "XY-99"

    def test_key_defaults_to_empty_string_when_absent(self):
        result = MCPResponseParser.parse(_MD_NO_KEY)
        assert result["key"] == ""

    def test_other_fields_still_populated(self):
        result = MCPResponseParser.parse(_MD_WITH_KEY)
        assert result["summary"] == "Pod crashing on startup"
        assert result["status"] == "Open"
        assert result["priority"] == "High"

    def test_key_field_shape_consistent_with_json_path(self):
        """Both markdown and JSON parse paths must include 'key'."""
        json_resp = {
            "key": "ABC-123",
            "fields": {
                "summary": "Test",
                "description": "",
                "labels": [],
                "priority": {"name": "High"},
                "status": {"name": "Open"},
            },
        }
        json_result = MCPResponseParser.parse(json_resp)
        md_result = MCPResponseParser.parse(_MD_WITH_KEY)
        assert "key" in json_result
        assert "key" in md_result

    def test_key_not_confused_with_summary_text(self):
        # "key" appears in summary text but must not be extracted as the ticket key
        md = "**Ticket Information**\nSummary: The key issue is pod restart\nStatus: Open"
        result = MCPResponseParser.parse(md)
        assert result["key"] == ""


# ---------------------------------------------------------------------------
# detect_format — sanity checks
# ---------------------------------------------------------------------------


class TestDetectFormat:
    def test_markdown_string_detected(self):
        fmt = MCPResponseParser.detect_format(
            "**Ticket Information**\nSummary: foo"
        )
        assert fmt == MCPResponseFormat.MARKDOWN

    def test_json_string_detected(self):
        fmt = MCPResponseParser.detect_format('{"key": "val"}')
        assert fmt == MCPResponseFormat.JSON

    def test_none_is_unknown(self):
        assert MCPResponseParser.detect_format(None) == MCPResponseFormat.UNKNOWN

    def test_plain_text_dict_with_content_key(self):
        fmt = MCPResponseParser.detect_format({"content": "just plain text"})
        assert fmt == MCPResponseFormat.PLAIN_TEXT

    def test_nested_content_dict(self):
        fmt = MCPResponseParser.detect_format({"content": '{"nested": true}'})
        assert fmt == MCPResponseFormat.NESTED_CONTENT
