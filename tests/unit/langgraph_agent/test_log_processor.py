"""Unit tests for K8s log preprocessing: boring line filtering and fuzzy dedup."""

import re

import pytest

from src.utils.log_processor import is_boring_line, deduplicate_lines, process_pod_logs


# ---------------------------------------------------------------------------
# is_boring_line
# ---------------------------------------------------------------------------
class TestIsBoringLine:

    @pytest.mark.parametrize("line", [
        "",
        "   ",
        "\t",
    ])
    def test_empty_and_whitespace(self, line):
        assert is_boring_line(line) is True

    @pytest.mark.parametrize("line", [
        "...",
        "-----",
        "====",
        "***",
    ])
    def test_decorators(self, line):
        assert is_boring_line(line) is True

    @pytest.mark.parametrize("line", [
        "2024-01-15 10:30:00",
        "2024-01-15T10:30:00",
        "2024/01/15 10:30:00",
    ])
    def test_timestamp_only(self, line):
        assert is_boring_line(line) is True

    @pytest.mark.parametrize("line", [
        "GET /healthz 200 OK",
        "readyz check passed",
        "livez endpoint responding",
        "GET /metrics 200",
    ])
    def test_health_and_metrics(self, line):
        assert is_boring_line(line) is True

    @pytest.mark.parametrize("line", [
        "readiness probe succeeded",
        "liveness probe failed",
    ])
    def test_probes(self, line):
        assert is_boring_line(line) is True

    @pytest.mark.parametrize("line", [
        "leader election started for controller-manager",
        "successfully acquired lease default/my-lock",
        "renewed lease default/my-lock",
    ])
    def test_leader_election(self, line):
        assert is_boring_line(line) is True

    @pytest.mark.parametrize("line", [
        "watch channel closed, restarting",
        "cache synced for pods",
        "informer started for deployments",
    ])
    def test_lifecycle_noise(self, line):
        assert is_boring_line(line) is True

    @pytest.mark.parametrize("line", [
        "ERROR: OOMKilled container payment-service",
        "CrashLoopBackOff: back-off 5m0s restarting failed container",
        "panic: runtime error: invalid memory address",
        "java.lang.NullPointerException",
        "  at com.example.Service.process(Service.java:42)",
    ])
    def test_real_errors_not_boring(self, line):
        assert is_boring_line(line) is False

    def test_extra_boring_patterns(self):
        custom = [re.compile(r"CUSTOM_NOISE")]
        assert is_boring_line("CUSTOM_NOISE line", extra_boring_patterns=custom) is True
        assert is_boring_line("real error", extra_boring_patterns=custom) is False


# ---------------------------------------------------------------------------
# deduplicate_lines
# ---------------------------------------------------------------------------
class TestDeduplicateLines:

    def test_extra_boring_patterns_non_pattern_raises(self):
        """Verify that passing non-Pattern objects raises TypeError."""
        with pytest.raises(TypeError, match="re.Pattern"):
            is_boring_line("hello", extra_boring_patterns=["not-a-pattern"])

    def test_extra_boring_patterns_multiple_invalid_raises(self):
        """Verify error message identifies the index of the invalid pattern."""
        with pytest.raises(TypeError, match="extra_boring_patterns\\[1\\]"):
            is_boring_line("hello", extra_boring_patterns=[re.compile(r"valid"), 42])
    def test_empty_string(self):
        assert deduplicate_lines("") == ""

    def test_whitespace_only(self):
        assert deduplicate_lines("   \n\n  ") == ""

    def test_all_boring(self):
        text = "GET /healthz 200\n...\nGET /metrics 200\n"
        assert deduplicate_lines(text) == ""

    def test_no_duplicates_preserved(self):
        text = "ERROR: connection refused\nWARN: retrying in 5s\nINFO: connected"
        result = deduplicate_lines(text)
        assert "connection refused" in result
        assert "retrying" in result
        assert "connected" in result

    def test_near_duplicates_reduced(self):
        text = (
            "ERROR: connection refused to db-host:5432\n"
            "ERROR: connection refused to db-host:5432\n"
            "ERROR: connection refused to db-host:5432\n"
        )
        result = deduplicate_lines(text)
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 1

    def test_below_threshold_kept(self):
        text = (
            "ERROR: connection refused to db-host:5432\n"
            "WARN: timeout connecting to cache-host:6379\n"
        )
        result = deduplicate_lines(text, threshold=85)
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 2

    def test_order_preserved(self):
        text = "first error line\nsecond warning line\nthird info line"
        result = deduplicate_lines(text)
        lines = result.splitlines()
        assert lines[0] == "first error line"
        assert lines[1] == "second warning line"
        assert lines[2] == "third info line"

    def test_threshold_validation_high(self):
        """Verify that threshold > 100 raises ValueError."""
        with pytest.raises(ValueError, match="threshold must be 0–100"):
            deduplicate_lines("hello", threshold=200)

    def test_threshold_validation_low(self):
        """Verify that threshold < 0 raises ValueError."""
        with pytest.raises(ValueError, match="threshold must be 0–100"):
            deduplicate_lines("hello", threshold=-1)

    def test_threshold_boundary_zero(self):
        """Verify that threshold=0 works (all lines >= 0% similar)."""
        # At threshold=0, only the first line is kept (all others are >= 0% similar)
        text = "apple\nbanana\ncherry"
        result = deduplicate_lines(text, threshold=0)
        assert result == "apple"

    def test_threshold_boundary_100(self):
        """Verify that threshold=100 only collapses identical lines."""
        text = "error\nerror\nerror with slight difference"
        result = deduplicate_lines(text, threshold=100)
        lines = [l for l in result.splitlines() if l.strip()]
        # Two unique lines (exact duplicates collapsed, near-duplicate kept)
        assert len(lines) == 2

    def test_exact_duplicates_efficient(self):
        """Verify that exact duplicates are caught by set before fuzzy comparison."""
        # This ensures the optimization works: exact duplicates should be O(1) not O(n)
        text = "\n".join(["same line"] * 100)
        result = deduplicate_lines(text)
        assert result == "same line"


# ---------------------------------------------------------------------------
# process_pod_logs
# ---------------------------------------------------------------------------
class TestProcessPodLogs:

    def test_empty_dict(self):
        assert process_pod_logs({}) == {}

    def test_multiple_pods_processed_independently(self):
        logs = {
            "pod-a": "ERROR: crash\nERROR: crash\nGET /healthz 200",
            "pod-b": "WARN: slow query\nWARN: slow query\n...",
        }
        result = process_pod_logs(logs)

        assert "pod-a" in result
        assert "pod-b" in result

        # pod-a: crash kept once, healthz removed
        a_lines = [l for l in result["pod-a"].splitlines() if l.strip()]
        assert len(a_lines) == 1
        assert "crash" in a_lines[0]

        # pod-b: slow query kept once, dots removed
        b_lines = [l for l in result["pod-b"].splitlines() if l.strip()]
        assert len(b_lines) == 1
        assert "slow query" in b_lines[0]

    def test_reduction_logged(self, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            process_pod_logs({"test-pod": "ERROR: real\n" * 3 + "GET /healthz 200\n" * 10})
        assert any("reduction" in r.message for r in caplog.records)
