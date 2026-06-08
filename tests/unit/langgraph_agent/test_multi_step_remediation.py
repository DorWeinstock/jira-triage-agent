"""Unit tests for multi-step remediation plan support.

Tests that:
1. RemediationStep model validates correctly
2. RemediationPlan supports ordered steps list
3. RemediationPlan remains backward-compatible (single action still works)
4. K8sRemediationExecutor iterates steps sequentially with stop-on-first-failure
5. K8sRemediationExecutor acquires locks upfront for all steps
6. Diagnostician generates multi-step plans
7. Diagnostician _format_plan_as_text handles multi-step plans
8. Diagnostician attempt_remediation passes multi-step plans to executor
9. Fallback diagnosis uses steps format
10. action_taken summarizes all completed steps
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.llm_outputs import RemediationStep, RemediationPlan, ActionType, Diagnosis, ConfidenceLevel
from src.agents.k8s_remediation_executor import K8sRemediationExecutor
from src.agents.diagnostician import Diagnostician
from src.state import AgentState


# ===========================================================================
# Helpers
# ===========================================================================

def _make_step(**overrides) -> RemediationStep:
    """Create a RemediationStep with sensible defaults."""
    defaults = dict(
        action=ActionType.SCALE,
        resource_type="deployment",
        name="my-svc",
        namespace="production",
        replicas=2,
        reason="Scale up to restore service",
    )
    defaults.update(overrides)
    return RemediationStep(**defaults)


def _make_plan(steps=None, **overrides) -> RemediationPlan:
    """Create a RemediationPlan with step(s)."""
    defaults = dict(
        remediation_possible=True,
        steps=steps or [_make_step()],
    )
    defaults.update(overrides)
    return RemediationPlan(**defaults)


def _make_executor(tool_return="deployment scaled", lock_service=None):
    """Create a K8sRemediationExecutor with mock tools."""
    tools = MagicMock()
    tools.call_tool = AsyncMock(return_value=tool_return)
    lock = lock_service or MagicMock()
    lock.acquire_lock = AsyncMock(return_value=True)
    lock.release_lock = AsyncMock()
    return K8sRemediationExecutor(tools, lock_service=lock), tools, lock


def _sample_state(**overrides) -> dict:
    """Create a minimal agent state for testing."""
    defaults = dict(
        ticket_id="PROJ-100",
        thread_id="thread-100",
        namespace="production",
        affected_resources={"deployments": ["my-svc"], "services": ["my-svc"]},
    )
    defaults.update(overrides)
    return defaults


def _make_diagnostician() -> Diagnostician:
    """Create a Diagnostician with mocked LLM and remediation agent."""
    agent = MagicMock()
    agent.execute_plan = AsyncMock(return_value={
        "success": True,
        "action_taken": "Scaled deployment my-svc to 2 replicas; Restarted deployment my-svc",
        "output": "ok",
    })
    with patch("src.agents.diagnostician.create_diagnosis_llm", return_value=MagicMock()):
        diag = Diagnostician(remediation_agent=agent)
    return diag


# ===========================================================================
# 1. RemediationStep model
# ===========================================================================

class TestRemediationStepModel:

    def test_create_step_with_required_fields(self):
        step = RemediationStep(
            action=ActionType.RESTART,
            resource_type="deployment",
            name="web",
            namespace="default",
            reason="Pick up new config",
        )
        assert step.action == ActionType.RESTART
        assert step.name == "web"

    def test_step_with_optional_data(self):
        step = RemediationStep(
            action=ActionType.CREATE_CONFIGMAP,
            resource_type="configmap",
            name="my-cm",
            namespace="default",
            data={"key": "value"},
            reason="Create missing configmap",
        )
        assert step.data == {"key": "value"}

    def test_step_namespace_defaults_to_default(self):
        step = RemediationStep(
            action=ActionType.RESTART,
            resource_type="deployment",
            name="web",
            namespace="",
            reason="Fix",
        )
        assert step.namespace == "default"


# ===========================================================================
# 2. RemediationPlan with steps
# ===========================================================================

class TestRemediationPlanWithSteps:

    def test_plan_with_single_step(self):
        step = _make_step()
        plan = RemediationPlan(remediation_possible=True, steps=[step])
        assert len(plan.steps) == 1
        assert plan.steps[0].action == ActionType.SCALE

    def test_plan_with_multiple_steps(self):
        steps = [
            _make_step(action=ActionType.CREATE_CONFIGMAP, name="cfg"),
            _make_step(action=ActionType.RESTART, name="web"),
        ]
        plan = RemediationPlan(remediation_possible=True, steps=steps)
        assert len(plan.steps) == 2
        assert plan.steps[0].action == ActionType.CREATE_CONFIGMAP
        assert plan.steps[1].action == ActionType.RESTART

    def test_backward_compat_single_action_creates_step(self):
        """Legacy plans with top-level action/name/etc. still work."""
        plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.SCALE,
            resource_type="deployment",
            name="my-svc",
            namespace="production",
            replicas=2,
            reason="Scale up",
        )
        # Should auto-create a single step from top-level fields
        assert len(plan.steps) == 1
        assert plan.steps[0].action == ActionType.SCALE
        assert plan.steps[0].name == "my-svc"
        assert plan.steps[0].replicas == 2

    def test_plan_not_possible_has_manual_instructions(self):
        plan = RemediationPlan(
            remediation_possible=False,
            manual_instructions="Contact DBA team",
        )
        assert plan.manual_instructions == "Contact DBA team"
        assert len(plan.steps) == 0

    def test_plan_serialization_roundtrip(self):
        """Plan can be serialized to dict and back."""
        steps = [
            _make_step(action=ActionType.CREATE_CONFIGMAP, name="cfg", data={"k": "v"}),
            _make_step(action=ActionType.RESTART, name="web"),
        ]
        plan = RemediationPlan(remediation_possible=True, steps=steps)
        data = plan.model_dump()
        restored = RemediationPlan(**data)
        assert len(restored.steps) == 2
        assert restored.steps[0].data == {"k": "v"}
        assert restored.steps[1].action == ActionType.RESTART


# ===========================================================================
# 3. Executor - multi-step sequential execution
# ===========================================================================

class TestExecutorMultiStep:

    @pytest.mark.asyncio
    async def test_executes_all_steps_in_order(self):
        executor, tools, lock = _make_executor()
        steps = [
            _make_step(action=ActionType.CREATE_CONFIGMAP, name="cfg", data={"k": "v"}),
            _make_step(action=ActionType.RESTART, name="web"),
        ]
        plan = _make_plan(steps=steps)
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        assert result["success"] is True
        assert tools.call_tool.call_count == 2

    @pytest.mark.asyncio
    async def test_stops_on_first_failure(self):
        executor, tools, lock = _make_executor()
        # First call succeeds, second fails
        tools.call_tool = AsyncMock(
            side_effect=["configmap created", Exception("K8s error")]
        )
        steps = [
            _make_step(action=ActionType.CREATE_CONFIGMAP, name="cfg", data={"k": "v"}),
            _make_step(action=ActionType.RESTART, name="web"),
            _make_step(action=ActionType.SCALE, name="other", replicas=3),
        ]
        plan = _make_plan(steps=steps)
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        assert result["success"] is False
        assert "error" in result
        # Third step should NOT have been attempted
        assert tools.call_tool.call_count == 2

    @pytest.mark.asyncio
    async def test_action_taken_summarizes_completed_steps(self):
        executor, tools, lock = _make_executor()
        steps = [
            _make_step(action=ActionType.SCALE, name="svc-a", replicas=2),
            _make_step(action=ActionType.RESTART, name="svc-b"),
        ]
        plan = _make_plan(steps=steps)
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        assert result["success"] is True
        # action_taken should mention both steps
        assert "svc-a" in result["action_taken"]
        assert "svc-b" in result["action_taken"]

    @pytest.mark.asyncio
    async def test_upfront_locking_all_resources(self):
        """Executor acquires locks for ALL step resources before executing any."""
        executor, tools, lock = _make_executor()
        steps = [
            _make_step(action=ActionType.SCALE, name="svc-a", namespace="prod"),
            _make_step(action=ActionType.RESTART, name="svc-b", namespace="prod"),
        ]
        plan = _make_plan(steps=steps)
        state = _sample_state()

        await executor.execute_plan(state, plan)

        # Should acquire locks for both resources upfront
        assert lock.acquire_lock.call_count == 2

    @pytest.mark.asyncio
    async def test_lock_failure_blocks_all_steps(self):
        """If any lock acquisition fails (returns False), no steps execute."""
        executor, tools, lock = _make_executor()
        # Real lock service returns False on contention, not raises AgentError
        lock.acquire_lock = AsyncMock(side_effect=[True, False])
        steps = [
            _make_step(action=ActionType.SCALE, name="svc-a"),
            _make_step(action=ActionType.RESTART, name="svc-b"),
        ]
        plan = _make_plan(steps=steps)
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        assert result["success"] is False
        assert "lock" in result["error"].lower()
        tools.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_failure_via_exception_still_handled(self):
        """AgentError from lock service is still caught (defense-in-depth)."""
        executor, tools, lock = _make_executor()
        from src.exceptions import AgentError
        lock.acquire_lock = AsyncMock(
            side_effect=AgentError("Network error", locked_by="unknown")
        )
        steps = [
            _make_step(action=ActionType.SCALE, name="svc-a"),
        ]
        plan = _make_plan(steps=steps)
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        assert result["success"] is False
        assert "lock" in result["error"].lower() or "locked" in result["error"].lower()
        tools.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_locks_released_after_execution(self):
        """All acquired locks are released even on failure."""
        executor, tools, lock = _make_executor()
        tools.call_tool = AsyncMock(side_effect=Exception("K8s error"))
        steps = [
            _make_step(action=ActionType.SCALE, name="svc-a"),
        ]
        plan = _make_plan(steps=steps)
        state = _sample_state()

        await executor.execute_plan(state, plan)

        # Lock should still be released
        assert lock.release_lock.call_count == 1

    @pytest.mark.asyncio
    async def test_single_step_backward_compat(self):
        """Single-step plans work identically to old single-action plans."""
        executor, tools, lock = _make_executor()
        plan = RemediationPlan(
            remediation_possible=True,
            action=ActionType.RESTART,
            resource_type="deployment",
            name="web",
            namespace="production",
            reason="Restart to fix",
        )
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        assert result["success"] is True
        assert tools.call_tool.call_count == 1

    @pytest.mark.asyncio
    async def test_manual_intervention_step_skips_lock(self):
        """Steps with manual_intervention action don't need locks."""
        executor, tools, lock = _make_executor()
        plan = RemediationPlan(
            remediation_possible=False,
            manual_instructions="Contact DBA",
        )
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        lock.acquire_lock.assert_not_called()


# ===========================================================================
# 4. Diagnostician - prompt schema includes steps
# ===========================================================================

class TestDiagnosticianMultiStep:

    def test_prompt_includes_steps_schema(self):
        diag = _make_diagnostician()
        state = _sample_state(namespace="production")
        prompt = diag._build_diagnosis_system_prompt(state)
        assert "steps" in prompt

    def test_format_plan_as_text_multi_step(self):
        diag = _make_diagnostician()
        steps = [
            _make_step(action=ActionType.CREATE_CONFIGMAP, name="my-cm", data={"k": "v"}),
            _make_step(action=ActionType.RESTART, name="my-svc"),
        ]
        plan = _make_plan(steps=steps)
        text = diag._format_plan_as_text(plan)
        # Should mention both steps
        assert "my-cm" in text
        assert "my-svc" in text
        assert "Step 1" in text or "1." in text or "1)" in text

    def test_format_plan_as_text_single_step(self):
        diag = _make_diagnostician()
        plan = _make_plan(steps=[_make_step(action=ActionType.RESTART, name="web")])
        text = diag._format_plan_as_text(plan)
        assert "web" in text

    def test_format_plan_not_possible(self):
        diag = _make_diagnostician()
        plan = RemediationPlan(
            remediation_possible=False,
            manual_instructions="Contact ops team",
        )
        text = diag._format_plan_as_text(plan)
        assert "Contact ops team" in text

    def test_fallback_diagnosis_uses_steps_format(self):
        diag = _make_diagnostician()
        state = _sample_state(
            cluster_findings={"problem_pods": [], "recommendations": [], "preliminary_findings": "broken"},
        )
        diag._apply_fallback_diagnosis(state, "LLM failed")

        plan_dict = state["remediation_plan"]
        assert "steps" in plan_dict or plan_dict.get("remediation_possible") is False

    @pytest.mark.asyncio
    async def test_attempt_remediation_passes_plan_to_executor(self):
        diag = _make_diagnostician()
        steps = [
            _make_step(action=ActionType.CREATE_CONFIGMAP, name="cfg", data={"k": "v"}),
            _make_step(action=ActionType.RESTART, name="web"),
        ]
        plan = _make_plan(steps=steps)

        state = _sample_state(
            confidence_level="High",
            remediation_plan=plan.model_dump(),
        )

        await diag.attempt_remediation(state)

        # The remediation agent should have been called
        diag.remediation_agent.execute_plan.assert_called_once()
        # The plan passed should be a RemediationPlan with steps
        call_args = diag.remediation_agent.execute_plan.call_args
        passed_plan = call_args[0][1]
        assert len(passed_plan.steps) == 2

    def test_update_state_from_diagnosis_multi_step(self):
        diag = _make_diagnostician()
        steps = [
            _make_step(action=ActionType.SCALE, name="svc", replicas=2),
            _make_step(action=ActionType.RESTART, name="svc"),
        ]
        plan = _make_plan(steps=steps)
        diagnosis = Diagnosis(
            root_cause="Deployment scaled to 0 and needs restart",
            confidence_level=ConfidenceLevel.HIGH,
            remediation_plan=plan,
            preventive_measures=["Monitor replica count"],
            evidence=["Pods at 0 replicas"],
        )
        state = _sample_state()
        diag._update_state_from_diagnosis(diagnosis, state)

        plan_dict = state["remediation_plan"]
        assert len(plan_dict["steps"]) == 2
        assert "svc" in state["recommended_action"]


# ===========================================================================
# 5. Bug fixes - silent failures
# ===========================================================================

class TestLockReturnValueChecked:
    """Bug 1: acquire_lock returns False on contention, not raises AgentError.

    The executor must check the return value, not just rely on exception handling.
    """

    @pytest.mark.asyncio
    async def test_lock_contention_returns_false_blocks_execution(self):
        """When acquire_lock returns False (not raises), execution must stop."""
        executor, tools, lock = _make_executor()
        # Real lock service returns False on contention, does NOT raise
        lock.acquire_lock = AsyncMock(side_effect=[True, False])
        steps = [
            _make_step(action=ActionType.SCALE, name="svc-a"),
            _make_step(action=ActionType.RESTART, name="svc-b"),
        ]
        plan = _make_plan(steps=steps)
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        assert result["success"] is False
        assert "lock" in result["error"].lower() or "locked" in result["error"].lower()
        # No steps should have executed
        tools.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_lock_contention_releases_already_acquired_locks(self):
        """When a lock fails, previously acquired locks must be released."""
        executor, tools, lock = _make_executor()
        # First lock succeeds, second returns False (contention)
        lock.acquire_lock = AsyncMock(side_effect=[True, False])
        steps = [
            _make_step(action=ActionType.SCALE, name="svc-a", namespace="prod"),
            _make_step(action=ActionType.RESTART, name="svc-b", namespace="prod"),
        ]
        plan = _make_plan(steps=steps)
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        # First lock should still be released despite contention on second
        assert lock.release_lock.call_count >= 1


class TestPatchActionFalseSuccess:
    """Bug 2: PATCH returns 'Manual intervention required. Run: kubectl ...'

    MCPResponseParser.is_success() treats this as success (no error keywords).
    But the patch was never applied -- it should report failure.
    """

    @pytest.mark.asyncio
    async def test_patch_action_reports_failure(self):
        """PATCH action must set success=False since it's not automated."""
        executor, tools, lock = _make_executor()
        step = _make_step(
            action=ActionType.PATCH,
            resource_type="deployment",
            name="my-deploy",
            namespace="production",
            data={"spec": {"replicas": 3}},
        )
        plan = _make_plan(steps=[step])
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        # PATCH is not automated -- it should NOT report success
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_patch_action_output_contains_kubectl_command(self):
        """PATCH action should still provide the kubectl command for manual use."""
        executor, tools, lock = _make_executor()
        step = _make_step(
            action=ActionType.PATCH,
            resource_type="deployment",
            name="my-deploy",
            namespace="production",
            data={"spec": {"replicas": 3}},
        )
        plan = _make_plan(steps=[step])
        state = _sample_state()

        result = await executor.execute_plan(state, plan)

        # Output should still contain the kubectl command
        assert "kubectl" in result["output"].lower() or "manual" in result["output"].lower()


# ===========================================================================
# 6. Additional test coverage for JSON parsing and multi-step scenarios
# ===========================================================================

class TestJsonParseErrorHandling:
    """Test JSON parsing errors in attempt_remediation."""

    @pytest.mark.asyncio
    async def test_attempt_remediation_with_invalid_json(self):
        """Garbage JSON in LLM response triggers fallback."""
        llm_mock = MagicMock()
        llm_mock.ainvoke = AsyncMock(
            return_value=MagicMock(content="{invalid json}")
        )
        diag = Diagnostician(remediation_agent=MagicMock())
        with patch("src.agents.diagnostician.create_diagnosis_llm", return_value=llm_mock):
            diag = Diagnostician(remediation_agent=MagicMock())
        
        state = _sample_state()
        result = await diag.run(state)

        # Should have fallen back on parse error
        assert result["confidence_level"] == "Low"
        assert result["remediation_plan"]["remediation_possible"] is False


class TestMultiStepLowConfidenceSkip:
    """Test that low confidence multi-step plans skip remediation."""

    @pytest.mark.asyncio
    async def test_multi_step_with_low_confidence_skips_remediation(self):
        """Even with a multi-step plan, low confidence prevents execution."""
        diag = _make_diagnostician()
        steps = [
            _make_step(action=ActionType.CREATE_CONFIGMAP, name="cfg", data={"k": "v"}),
            _make_step(action=ActionType.RESTART, name="web"),
        ]
        plan = _make_plan(steps=steps)

        state = _sample_state(
            confidence_level="Low",  # Low confidence
            remediation_plan=plan.model_dump(),
        )

        await diag.attempt_remediation(state)

        # Should skip remediation due to low confidence
        assert state["remediation_attempted"] is False
        assert state["remediation_result"]["skipped"] is True

    @pytest.mark.asyncio
    async def test_multi_step_with_medium_confidence_attempts_remediation(self):
        """Medium confidence allows multi-step execution."""
        diag = _make_diagnostician()
        steps = [
            _make_step(action=ActionType.SCALE, name="svc", replicas=2),
            _make_step(action=ActionType.RESTART, name="svc"),
        ]
        plan = _make_plan(steps=steps)

        state = _sample_state(
            confidence_level="Medium",  # Medium confidence
            remediation_plan=plan.model_dump(),
        )

        await diag.attempt_remediation(state)

        # Should attempt remediation
        assert state["remediation_attempted"] is True
        assert diag.remediation_agent.execute_plan.called
