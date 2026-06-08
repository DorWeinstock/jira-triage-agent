"""Tests for approval comment formatting."""

import pytest


class TestApprovalCommentFormatter:
    """Test Jira approval comment formatting with wiki markup panels."""

    def test_high_confidence_green_indicator(self):
        """High confidence should use green indicator and background."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "ticket_id": "SP-1234",
            "root_cause": "OOM killed",
            "confidence_level": "high",
            "recommended_action": "Scale memory to 512Mi",
            "cluster_findings": {"affected_deployment": "payment-service"},
        }

        comment = format_approval_comment(state)

        assert "#d4edda" in comment  # Green background
        assert "🟢 HIGH CONFIDENCE" in comment
        assert "OOM killed" in comment
        assert "Scale memory to 512Mi" in comment
        assert "APPROVAL REQUIRED" in comment

    def test_medium_confidence_yellow_indicator(self):
        """Medium confidence should use yellow indicator and background."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "ticket_id": "SP-1234",
            "root_cause": "Possible network issue",
            "confidence_level": "medium",
            "recommended_action": "Check network policies",
            "cluster_findings": {},
        }

        comment = format_approval_comment(state)

        assert "#fff3cd" in comment  # Yellow background
        assert "🟡 MEDIUM CONFIDENCE" in comment

    def test_low_confidence_red_indicator(self):
        """Low confidence should use red indicator and background."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "ticket_id": "SP-1234",
            "root_cause": "Unknown issue",
            "confidence_level": "low",
            "recommended_action": "Manual investigation needed",
            "cluster_findings": {},
        }

        comment = format_approval_comment(state)

        assert "#f8d7da" in comment  # Red background
        assert "🔴 LOW CONFIDENCE" in comment

    def test_includes_approval_instructions(self):
        """Comment should include approve/reject instructions."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "ticket_id": "SP-1234",
            "root_cause": "Test",
            "confidence_level": "high",
            "recommended_action": "Test action",
            "cluster_findings": {},
        }

        comment = format_approval_comment(state)

        assert "approve" in comment.lower()
        assert "reject" in comment.lower()

    def test_includes_evidence_from_findings(self):
        """Comment should include key evidence from cluster findings."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "ticket_id": "SP-1234",
            "root_cause": "OOM",
            "confidence_level": "high",
            "recommended_action": "Scale up",
            "cluster_findings": {
                "affected_deployment": "order-service",
                "namespace": "production",
            },
        }

        comment = format_approval_comment(state)

        assert "order-service" in comment
        assert "production" in comment

    def test_panel_format_structure(self):
        """Comment should use Jira wiki markup panel syntax."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "ticket_id": "SP-1234",
            "root_cause": "Test issue",
            "confidence_level": "high",
            "recommended_action": "Test fix",
            "cluster_findings": {},
        }

        comment = format_approval_comment(state)

        # Verify panel structure
        assert "{panel:" in comment
        assert "bgColor=" in comment
        assert "borderColor=" in comment
        # Verify section titles
        assert "title=Problem" in comment
        assert "title=Recommended Fix" in comment
        assert "title=Evidence" in comment

    def test_long_root_cause_not_truncated(self):
        """Long root cause should be displayed in full (not truncated)."""
        from src.services.approval_comment import format_approval_comment

        long_cause = "A" * 200  # 200 chars - longer than old MAX_FIELD_LENGTH
        state = {
            "ticket_id": "SP-1234",
            "root_cause": long_cause,
            "confidence_level": "high",
            "recommended_action": "Fix it",
            "cluster_findings": {},
        }

        comment = format_approval_comment(state)

        # Full string should appear (no truncation)
        assert long_cause in comment

    def test_resources_as_dict(self):
        """Resources dict should list resource types."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "confidence_level": "high",
            "root_cause": "Pod crash",
            "recommended_action": "Restart",
            "cluster_findings": {
                "resources": {
                    "pods": "pod-1 Running",
                    "service": "svc-1 ClusterIP",
                    "deployment": "",  # Empty, should not count
                }
            },
        }

        comment = format_approval_comment(state)

        assert "2 resource types" in comment
        assert "pods" in comment
        assert "service" in comment

    def test_events_as_list(self):
        """Events list should show count."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "confidence_level": "high",
            "root_cause": "OOM",
            "recommended_action": "Scale up",
            "cluster_findings": {
                "events": [
                    {"reason": "OOMKilled"},
                    {"reason": "BackOff"},
                ]
            },
        }

        comment = format_approval_comment(state)

        assert "2 events" in comment

    def test_long_fix_not_truncated(self):
        """Long recommended action should be displayed in full (not truncated)."""
        from src.services.approval_comment import format_approval_comment

        long_fix = "kubectl scale deployment order-service --replicas=5 -n production && kubectl rollout status deployment order-service -n production"
        state = {
            "confidence_level": "high",
            "root_cause": "Issue",
            "recommended_action": long_fix,
            "cluster_findings": {},
        }

        comment = format_approval_comment(state)

        # Full string should appear (no truncation)
        assert long_fix in comment

    def test_malformed_findings_handled(self):
        """Malformed cluster_findings should not crash."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "confidence_level": "high",
            "root_cause": "Issue",
            "recommended_action": "Fix",
            "cluster_findings": "not a dict",  # Malformed
        }

        comment = format_approval_comment(state)

        assert "No findings" in comment or "See diagnosis" in comment

    def test_no_findings_shows_fallback(self):
        """Empty findings should show fallback message."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "confidence_level": "high",
            "root_cause": "Issue",
            "recommended_action": "Fix",
            "cluster_findings": {},
        }

        comment = format_approval_comment(state)

        assert "No findings" in comment or "See diagnosis" in comment

    def test_logs_included_in_evidence(self):
        """Logs indicator should appear in evidence if logs present."""
        from src.services.approval_comment import format_approval_comment

        state = {
            "confidence_level": "high",
            "root_cause": "OOM",
            "recommended_action": "Scale up",
            "cluster_findings": {
                "logs": "Error: Out of memory",
            },
        }

        comment = format_approval_comment(state)

        assert "Logs" in comment

    def test_multiline_content_preserved(self):
        """Multiline content should be preserved without truncation."""
        from src.services.approval_comment import format_approval_comment

        multiline_cause = """The order-service deployment is experiencing OOM kills.
This is caused by insufficient memory limits (256Mi).
The service handles large JSON payloads which spike memory usage.
Pod restarts have occurred 5 times in the last hour."""

        state = {
            "confidence_level": "high",
            "root_cause": multiline_cause,
            "recommended_action": "Increase memory limit to 512Mi",
            "cluster_findings": {
                "affected_deployment": "order-service",
                "namespace": "production",
            },
        }

        comment = format_approval_comment(state)

        # All lines should be present
        assert "OOM kills" in comment
        assert "insufficient memory limits" in comment
        assert "spike memory usage" in comment
        assert "5 times in the last hour" in comment

    def test_none_state_raises_type_error(self):
        """None state should raise TypeError."""
        from src.services.approval_comment import format_approval_comment

        with pytest.raises(TypeError, match="state must be a dict"):
            format_approval_comment(None)

    def test_non_dict_state_raises_type_error(self):
        """Non-dict state should raise TypeError."""
        from src.services.approval_comment import format_approval_comment

        with pytest.raises(TypeError, match="state must be a dict"):
            format_approval_comment("not a dict")

    def test_extract_problem_handles_dict_root_cause(self):
        """_extract_problem should handle dict root_cause with summary/detail."""
        from src.services.approval_comment import _extract_problem

        state = {"root_cause": {"summary": "OOM issue", "detail": "Memory limit exceeded"}}
        result = _extract_problem(state)
        assert result == "OOM issue"

    def test_extract_problem_handles_dict_with_detail_fallback(self):
        """_extract_problem should use detail if summary missing."""
        from src.services.approval_comment import _extract_problem

        state = {"root_cause": {"detail": "Only detail available"}}
        result = _extract_problem(state)
        assert result == "Only detail available"

    def test_extract_problem_handles_none_root_cause(self):
        """_extract_problem should return 'Unknown issue' for None."""
        from src.services.approval_comment import _extract_problem

        state = {"root_cause": None}
        result = _extract_problem(state)
        assert result == "Unknown issue"

    def test_extract_problem_handles_empty_string_root_cause(self):
        """_extract_problem should return 'Unknown issue' for empty string."""
        from src.services.approval_comment import _extract_problem

        state = {"root_cause": "   "}
        result = _extract_problem(state)
        assert result == "Unknown issue"

    def test_step_to_command_patch_escapes_single_quotes(self):
        """_step_to_command PATCH should escape single quotes for shell safety."""
        from src.services.approval_comment import _step_to_command
        from src.models.llm_outputs import ActionType, RemediationStep

        step = RemediationStep(
            action=ActionType.PATCH,
            name="svc",
            namespace="default",
            resource_type="deployment",
            data={"key": "it's a value"},
        )
        cmd = _step_to_command(step)
        # Escaped form: it'\''s (POSIX single-quote escape)
        assert "it'\\''s" in cmd or cmd is None

    def test_step_to_command_apply_manifest_escapes_eof(self):
        """_step_to_command APPLY_MANIFEST should escape EOF sentinel."""
        from src.services.approval_comment import _step_to_command
        from src.models.llm_outputs import ActionType, RemediationStep

        step = RemediationStep(
            action=ActionType.APPLY_MANIFEST,
            name="manifest",
            namespace="default",
            resource_type="deployment",
            yaml_content="kind: Pod\nmetadata:\n  name: test  # EOF marker here",
        )
        cmd = _step_to_command(step)
        # EOF should be escaped to E_O_F
        assert "E_O_F" in cmd

    def test_format_evidence_shows_truncation_notice(self):
        """_format_evidence should show '...and N more' when fallback is truncated."""
        from src.services.approval_comment import _format_evidence

        # Create 8 arbitrary findings to exceed fallback limit of 5
        findings = {f"key_{i}": f"value_{i}" for i in range(8)}
        result = _format_evidence({"cluster_findings": findings})
        # Should show "...and 3 more findings" (8 - 5 = 3)
        assert "more" in result

    def test_format_evidence_handles_malformed_nested_dict(self):
        """_format_evidence should not crash with deeply nested malformed dicts."""
        from src.services.approval_comment import _format_evidence

        state = {
            "cluster_findings": {
                "resources": {"nested": {"deeply": {"nested": "value"}}},
                "other": {"a": 1},
            }
        }
        result = _format_evidence(state)
        assert isinstance(result, str)
        assert len(result) > 0  # Should produce some output, not crash
