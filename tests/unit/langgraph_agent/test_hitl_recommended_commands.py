"""Tests for HITL recommended commands extraction and display.

Tests that the approval comment includes kubectl commands derived from
the remediation plan steps.
"""

import json

import pytest

from src.services.approval_comment import (
    _extract_recommended_commands,
    format_approval_comment,
)
from src.models.llm_outputs import ActionType, RemediationPlan, RemediationStep


def _make_state(**overrides):
    """Build a minimal valid state dict for approval comment tests."""
    base = {
        "ticket_id": "SP-100",
        "root_cause": "Test root cause for unit test",
        "confidence_level": "high",
        "recommended_action": "Apply the recommended fix",
        "cluster_findings": {},
        "remediation_plan": None,
    }
    base.update(overrides)
    return base


def _plan_dict(*steps, remediation_possible=True, **kwargs):
    """Create a serialized RemediationPlan dict from RemediationStep objects."""
    plan = RemediationPlan(
        remediation_possible=remediation_possible,
        steps=list(steps),
        **kwargs,
    )
    return plan.model_dump()


# =========================================================================
# _extract_recommended_commands tests
# =========================================================================


class TestExtractRecommendedCommands:
    """Test kubectl command extraction from remediation plan steps."""

    def test_scale_command(self):
        """Scale step produces kubectl scale command with replicas."""
        step = RemediationStep(
            action=ActionType.SCALE,
            name="order-service",
            namespace="production",
            resource_type="deployment",
            replicas=3,
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        commands = _extract_recommended_commands(state)

        assert len(commands) == 1
        assert commands[0] == (
            "kubectl scale deployment order-service --replicas=3 -n production"
        )

    def test_restart_command(self):
        """Restart step produces kubectl rollout restart command."""
        step = RemediationStep(
            action=ActionType.RESTART,
            name="payment-api",
            namespace="staging",
            resource_type="deployment",
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        commands = _extract_recommended_commands(state)

        assert len(commands) == 1
        assert commands[0] == (
            "kubectl rollout restart deployment payment-api -n staging"
        )

    def test_delete_command(self):
        """Delete step produces kubectl delete command."""
        step = RemediationStep(
            action=ActionType.DELETE,
            name="stale-pod-xyz",
            namespace="default",
            resource_type="pod",
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        commands = _extract_recommended_commands(state)

        assert len(commands) == 1
        assert commands[0] == "kubectl delete pod stale-pod-xyz -n default"

    def test_create_configmap_command(self):
        """create_configmap step produces kubectl create configmap with --from-literal flags."""
        step = RemediationStep(
            action=ActionType.CREATE_CONFIGMAP,
            name="app-config",
            namespace="production",
            resource_type="configmap",
            data={"LOG_LEVEL": "debug", "MAX_RETRIES": "5"},
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        commands = _extract_recommended_commands(state)

        assert len(commands) == 1
        cmd = commands[0]
        assert cmd.startswith("kubectl create configmap app-config")
        assert "--from-literal=LOG_LEVEL=debug" in cmd
        assert "--from-literal=MAX_RETRIES=5" in cmd
        assert cmd.endswith("-n production")

    def test_create_secret_command_no_values_exposed(self):
        """create_secret step shows key names only -- NEVER exposes secret values."""
        step = RemediationStep(
            action=ActionType.CREATE_SECRET,
            name="db-creds",
            namespace="production",
            resource_type="secret",
            data={"DB_PASSWORD": "supersecret123", "DB_USER": "admin"},  # pragma: allowlist secret
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        commands = _extract_recommended_commands(state)

        assert len(commands) == 1
        cmd = commands[0]
        assert cmd.startswith("kubectl create secret generic db-creds")
        # Key names visible, values masked
        assert "DB_PASSWORD" in cmd
        assert "DB_USER" in cmd
        # Secret values MUST NOT appear
        assert "supersecret123" not in cmd
        assert "admin" not in cmd
        assert cmd.endswith("-n production")

    def test_patch_command(self):
        """Patch step produces kubectl patch command with JSON payload."""
        patch_data = {"spec": {"replicas": 2}}
        step = RemediationStep(
            action=ActionType.PATCH,
            name="web-deploy",
            namespace="default",
            resource_type="deployment",
            data=patch_data,
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        commands = _extract_recommended_commands(state)

        assert len(commands) == 1
        cmd = commands[0]
        assert "kubectl patch deployment web-deploy" in cmd
        assert "-n default" in cmd
        assert "--type=merge" in cmd
        assert json.dumps(patch_data) in cmd

    def test_apply_manifest_command(self):
        """apply_manifest step produces kubectl apply -f - heredoc."""
        yaml_content = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: test\n"
        step = RemediationStep(
            action=ActionType.APPLY_MANIFEST,
            name="test-manifest",
            namespace="default",
            resource_type="configmap",
            yaml_content=yaml_content,
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        commands = _extract_recommended_commands(state)

        assert len(commands) == 1
        cmd = commands[0]
        assert "kubectl apply -f -" in cmd
        assert "<<EOF" in cmd
        assert yaml_content in cmd
        assert "EOF" in cmd

    def test_multi_step_ordered_commands(self):
        """Multi-step plan produces commands in order."""
        steps = [
            RemediationStep(
                action=ActionType.DELETE,
                name="bad-pod",
                namespace="production",
                resource_type="pod",
            ),
            RemediationStep(
                action=ActionType.RESTART,
                name="web-app",
                namespace="production",
                resource_type="deployment",
            ),
            RemediationStep(
                action=ActionType.SCALE,
                name="web-app",
                namespace="production",
                resource_type="deployment",
                replicas=3,
            ),
        ]
        state = _make_state(
            remediation_plan=_plan_dict(*steps),
        )

        commands = _extract_recommended_commands(state)

        assert len(commands) == 3
        assert "delete pod bad-pod" in commands[0]
        assert "rollout restart deployment web-app" in commands[1]
        assert "scale deployment web-app --replicas=3" in commands[2]

    def test_manual_intervention_skipped(self):
        """manual_intervention steps produce no command."""
        step = RemediationStep(
            action=ActionType.MANUAL_INTERVENTION,
            name="needs-human",
            namespace="production",
            resource_type="deployment",
            reason="Requires manual database migration",
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        commands = _extract_recommended_commands(state)

        assert commands == []

    def test_mixed_steps_skip_manual(self):
        """Multi-step plan with manual steps skips only the manual ones."""
        steps = [
            RemediationStep(
                action=ActionType.RESTART,
                name="api",
                namespace="default",
                resource_type="deployment",
            ),
            RemediationStep(
                action=ActionType.MANUAL_INTERVENTION,
                name="db",
                namespace="default",
                resource_type="database",
                reason="Run migration",
            ),
            RemediationStep(
                action=ActionType.SCALE,
                name="api",
                namespace="default",
                resource_type="deployment",
                replicas=2,
            ),
        ]
        state = _make_state(
            remediation_plan=_plan_dict(*steps),
        )

        commands = _extract_recommended_commands(state)

        assert len(commands) == 2
        assert "rollout restart" in commands[0]
        assert "scale" in commands[1]

    def test_empty_remediation_plan(self):
        """Missing remediation_plan returns empty list."""
        state = _make_state(remediation_plan=None)

        commands = _extract_recommended_commands(state)

        assert commands == []

    def test_no_remediation_plan_key(self):
        """State without remediation_plan key returns empty list."""
        state = {
            "ticket_id": "SP-100",
            "root_cause": "Something",
            "confidence_level": "high",
        }

        commands = _extract_recommended_commands(state)

        assert commands == []

    def test_not_remediation_possible(self):
        """remediation_possible=False returns empty list."""
        plan = RemediationPlan(
            remediation_possible=False,
            manual_instructions="Contact the DBA",
        )
        state = _make_state(remediation_plan=plan.model_dump())

        commands = _extract_recommended_commands(state)

        assert commands == []

    def test_scale_default_replicas(self):
        """Scale with replicas=None defaults to 1."""
        step = RemediationStep(
            action=ActionType.SCALE,
            name="svc",
            namespace="default",
            resource_type="deployment",
            replicas=None,
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        commands = _extract_recommended_commands(state)

        assert len(commands) == 1
        assert "--replicas=1" in commands[0]


# =========================================================================
# format_approval_comment integration tests
# =========================================================================


class TestFormatApprovalCommentWithCommands:
    """Test that format_approval_comment renders the commands section."""

    def test_includes_commands_section(self):
        """Approval comment includes Recommended Commands when plan has actionable steps."""
        step = RemediationStep(
            action=ActionType.RESTART,
            name="web-app",
            namespace="production",
            resource_type="deployment",
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        comment = format_approval_comment(state)

        assert "Recommended Commands" in comment
        assert "{code}" in comment
        assert "kubectl rollout restart deployment web-app -n production" in comment

    def test_excludes_commands_when_manual_only(self):
        """No commands section when all steps are manual_intervention."""
        step = RemediationStep(
            action=ActionType.MANUAL_INTERVENTION,
            name="manual-task",
            namespace="production",
            resource_type="deployment",
            reason="Needs manual migration",
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        comment = format_approval_comment(state)

        assert "Recommended Commands" not in comment

    def test_excludes_commands_when_no_plan(self):
        """No commands section when no remediation_plan in state."""
        state = _make_state(remediation_plan=None)

        comment = format_approval_comment(state)

        assert "Recommended Commands" not in comment

    def test_commands_in_code_block(self):
        """Commands are wrapped in Jira {code} blocks."""
        steps = [
            RemediationStep(
                action=ActionType.DELETE,
                name="bad-pod",
                namespace="staging",
                resource_type="pod",
            ),
            RemediationStep(
                action=ActionType.RESTART,
                name="api-svc",
                namespace="staging",
                resource_type="deployment",
            ),
        ]
        state = _make_state(
            remediation_plan=_plan_dict(*steps),
        )

        comment = format_approval_comment(state)

        assert "{code}" in comment
        assert "kubectl delete pod bad-pod -n staging" in comment
        assert "kubectl rollout restart deployment api-svc -n staging" in comment

    def test_commands_appear_after_recommended_fix(self):
        """Commands section appears after the Recommended Fix panel."""
        step = RemediationStep(
            action=ActionType.SCALE,
            name="web",
            namespace="default",
            resource_type="deployment",
            replicas=5,
        )
        state = _make_state(
            remediation_plan=_plan_dict(step),
        )

        comment = format_approval_comment(state)

        fix_pos = comment.index("Recommended Fix")
        cmd_pos = comment.index("Recommended Commands")
        assert cmd_pos > fix_pos
