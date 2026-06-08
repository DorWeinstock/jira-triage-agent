"""K8s Remediation Executor for executing K8s fixes.

This agent is a PURE EXECUTOR with NO LLM calls. It receives a structured
RemediationPlan from the Diagnostician and executes the corresponding K8s
operations.

Architecture note:
    Previously, this agent had its own LLM call to parse text instructions
    into structured actions. That redundant LLM call has been eliminated.
    The Diagnostician now generates RemediationPlan directly, and this
    agent simply executes it.

Responsibilities:
    - Execute K8s operations (scale, restart, delete, create configmap, etc.)
    - Manage remediation locks to prevent concurrent modifications
    - Report success/failure of operations
"""

import base64
import json
import logging
import yaml

from ..state import AgentState
from ..tools.k8s_tools import K8sTools
from ..exceptions import RemediationError, ToolError, AgentError, ValidationError
from ..models import RemediationPlan, RemediationStep, ActionType
from ..services.remediation_lock_service import RemediationLockService
from ..utils import MCPResponseParser
from ..config import get_settings

logger = logging.getLogger(__name__)

# Agent identifier for error context
AGENT_NAME = "K8sRemediationExecutor"


class K8sRemediationExecutor:
    """Executes K8s remediation actions from structured plans.

    This is a pure executor with NO LLM calls. It receives a RemediationPlan
    from the Diagnostician and executes the corresponding K8s operations.
    """

    def __init__(
        self,
        k8s_tools: K8sTools,
        lock_service: RemediationLockService = None,
    ):
        self.tools = k8s_tools
        # Lock service should be injected for proper lifecycle management
        # If not provided, create one (for backwards compatibility with tests)
        self.lock_service = (
            lock_service if lock_service is not None else RemediationLockService()
        )

    async def execute_plan(self, state: AgentState, plan: RemediationPlan) -> dict:
        """
        Execute a structured remediation plan with multi-step support.

        Steps are executed sequentially with stop-on-first-failure semantics.
        Locks are acquired upfront for ALL step resources before any execution
        begins, ensuring atomicity.

        Args:
            state: Current agent state (for ticket_id, namespace context)
            plan: Structured remediation plan from Diagnostician

        Returns:
            Dictionary with execution result:
                - success: bool
                - action_taken: str description of what was done
                - output: str raw output from K8s operation(s)
                - error: str if failed
        """
        steps = plan.steps
        step_count = len(steps)
        logger.info(
            f"[{AGENT_NAME}] Executing plan: {step_count} step(s), "
            f"remediation_possible={plan.remediation_possible}"
        )

        result = {
            "success": False,
            "action_taken": "",
            "error": None,
            "output": "",
            "requires_manual": False,
        }

        ticket_id = state.get("ticket_id", "unknown")
        thread_id = state.get("thread_id", "unknown")

        # If no steps (manual intervention or not possible), handle directly
        if not steps:
            if not plan.remediation_possible:
                result["action_taken"] = "Manual intervention required"
                result["output"] = (
                    plan.manual_instructions
                    or plan.reason
                    or "Manual intervention required"
                )
                result["success"] = False
                result["error"] = "This issue requires manual intervention"
                return result
            result["error"] = "No remediation steps in plan"
            return result

        # SECURITY: Check namespace allowlist for ALL steps (defense in depth)
        settings = get_settings()
        for step in steps:
            ns = step.namespace or state.get("namespace", "default")
            if ns in settings.readonly_namespaces:
                logger.error(
                    f"[{AGENT_NAME}] Remediation BLOCKED for namespace '{ns}' "
                    f"(protected namespace) in step: {step.action.value}/{step.name}"
                )
                result["error"] = (
                    f"Remediation not allowed in namespace '{ns}'. "
                    f"Protected namespaces: {settings.readonly_namespaces}"
                )
                result["error_type"] = "namespace_not_allowed"
                return result

        # Upfront locking: acquire locks for ALL step resources before execution
        lockable_steps = [
            s for s in steps if s.action != ActionType.MANUAL_INTERVENTION
        ]
        acquired_locks: list[dict] = []  # Track locks for cleanup

        try:
            # Lock acquisition phase - capture lock errors but continue to finally
            lock_error = None
            for step in lockable_steps:
                if lock_error:
                    break
                ns = step.namespace or state.get("namespace", "default")
                try:
                    lock_acquired = await self.lock_service.acquire_lock(
                        resource_type=step.resource_type,
                        namespace=ns,
                        name=step.name,
                        ticket_id=ticket_id,
                        thread_id=thread_id,
                    )
                    if not lock_acquired:
                        logger.warning(
                            f"[{AGENT_NAME}] Lock contention for {step.resource_type}/{step.name} "
                            f"in {ns} - resource locked by another ticket"
                        )
                        lock_error = (
                            f"Resource locked by another ticket: "
                            f"{step.resource_type}/{step.name} in {ns}"
                        )
                        break
                    acquired_locks.append(
                        {
                            "resource_type": step.resource_type,
                            "namespace": ns,
                            "name": step.name,
                        }
                    )
                except AgentError as e:
                    # Defense-in-depth: lock service could raise on network errors
                    logger.warning(
                        f"[{AGENT_NAME}] Failed to acquire lock for {step.name}: {e}"
                    )
                    lock_error = f"Resource is locked by another ticket: {getattr(e, 'locked_by', str(e))}"
                    break

            # If lock error occurred, return early with finally still running
            if lock_error:
                result["error"] = lock_error
                result["error_type"] = "lock_acquisition_error"
                return result

            # Execute steps sequentially with stop-on-first-failure
            completed_actions: list[str] = []
            outputs: list[str] = []

            for i, step in enumerate(steps, start=1):
                ns = step.namespace or state.get("namespace", "default")
                logger.info(
                    f"[{AGENT_NAME}] Step {i}/{step_count}: "
                    f"action={step.action.value}, resource={step.resource_type}/{step.name}"
                )

                step_result = {
                    "success": False,
                    "action_taken": "",
                    "error": None,
                    "output": "",
                }

                try:
                    step_result = await self._execute_action(
                        step.action,
                        step,
                        ns,
                        step.name,
                        step.resource_type,
                        step_result,
                    )
                except ToolError as e:
                    logger.error(f"[{AGENT_NAME}] K8s tool error at step {i}: {e}")
                    step_result["error"] = f"K8s operation failed: {str(e)}"
                    step_result["error_type"] = "tool_error"
                except Exception as e:
                    logger.error(f"[{AGENT_NAME}] Unexpected error at step {i}: {e}")
                    step_result["error"] = f"Remediation failed: {str(e)}"
                    step_result["error_type"] = "unexpected_error"

                if step_result.get("action_taken"):
                    completed_actions.append(step_result["action_taken"])
                if step_result.get("output"):
                    outputs.append(str(step_result["output"]))

                if not step_result.get("success"):
                    # Stop on first failure
                    result["success"] = False
                    result["action_taken"] = (
                        "; ".join(completed_actions) if completed_actions else ""
                    )
                    result["output"] = "\n".join(outputs)
                    result["error"] = step_result.get("error", f"Step {i} failed")
                    result["error_type"] = step_result.get("error_type", "step_failure")
                    result["requires_manual"] = step_result.get(
                        "requires_manual", False
                    )
                    logger.warning(
                        f"[{AGENT_NAME}] Stopping at step {i}/{step_count}: {result['error']}"
                    )
                    return result

            # All steps succeeded
            result["success"] = True
            result["action_taken"] = "; ".join(completed_actions)
            result["output"] = "\n".join(outputs)

        finally:
            # Release ALL acquired locks
            for lock_info in acquired_locks:
                try:
                    await self.lock_service.release_lock(
                        resource_type=lock_info["resource_type"],
                        namespace=lock_info["namespace"],
                        name=lock_info["name"],
                        ticket_id=ticket_id,
                    )
                except Exception as e:
                    logger.error(
                        f"[{AGENT_NAME}] Failed to release lock for {lock_info['name']}: {e}"
                    )

        logger.info(
            f"[{AGENT_NAME}] Result: success={result['success']}, "
            f"steps_completed={len(completed_actions)}/{step_count}"
        )
        return result

    async def _execute_action(
        self,
        action: ActionType,
        step: "RemediationStep",
        namespace: str,
        name: str,
        resource_type: str,
        result: dict,
    ) -> dict:
        """Execute the specific K8s action based on action type.

        Args:
            action: The type of action to execute
            step: Remediation step with parameters
            namespace: Target namespace
            name: Resource name
            resource_type: K8s resource type
            result: Result dict to update

        Returns:
            Updated result dict
        """
        if action == ActionType.CREATE_CONFIGMAP:
            output = await self._create_configmap(
                name=name, namespace=namespace, data=step.data or {}
            )
            result["action_taken"] = f"Created ConfigMap {name} in {namespace}"
            result["output"] = output
            result["success"] = MCPResponseParser.is_success(output)

        elif action == ActionType.CREATE_SECRET:
            output = await self._create_secret(
                name=name, namespace=namespace, data=step.data or {}
            )
            result["action_taken"] = f"Created Secret {name} in {namespace}"
            result["output"] = output
            result["success"] = MCPResponseParser.is_success(output)

        elif action == ActionType.APPLY_MANIFEST:
            yaml_content = step.yaml_content or ""
            output = await self._apply_manifest(yaml_content, namespace)
            result["action_taken"] = "Applied YAML manifest"
            result["output"] = output
            result["success"] = MCPResponseParser.is_success(output)

        elif action == ActionType.RESTART:
            output = await self._restart_deployment(name=name, namespace=namespace)
            result["action_taken"] = f"Restarted deployment {name} in {namespace}"
            result["output"] = output
            result["success"] = MCPResponseParser.is_success(output)

        elif action == ActionType.SCALE:
            replicas = step.replicas if step.replicas is not None else 1
            output = await self._scale_deployment(
                name=name, namespace=namespace, replicas=replicas
            )
            result["action_taken"] = f"Scaled deployment {name} to {replicas} replicas"
            result["output"] = output
            result["success"] = MCPResponseParser.is_success(output)

        elif action == ActionType.DELETE:
            output = await self._delete_resource(
                resource_type=resource_type, name=name, namespace=namespace
            )
            result["action_taken"] = f"Deleted {resource_type} {name} in {namespace}"
            result["output"] = output
            result["success"] = MCPResponseParser.is_success(output)

        elif action == ActionType.PATCH:
            patch_data = step.data or {}
            output = await self._patch_resource(
                resource_type=resource_type,
                name=name,
                namespace=namespace,
                patch=patch_data,
            )
            result["action_taken"] = (
                f"Patch command generated for {resource_type} {name} in {namespace}"
            )
            result["output"] = output
            # PATCH is not automated - returns kubectl command for manual execution
            result["success"] = False
            result["requires_manual"] = True
            result["error"] = "Patch requires manual execution"

        elif action == ActionType.MANUAL_INTERVENTION:
            result["action_taken"] = "Manual intervention required"
            result["output"] = step.reason or "Manual intervention required"
            result["success"] = False
            result["requires_manual"] = True
            result["error"] = "This issue requires manual intervention"

        else:
            result["error"] = f"Unknown action: {action}"

        return result

    async def _create_configmap(self, name: str, namespace: str, data: dict) -> str:
        """Create a ConfigMap"""
        configmap_yaml = yaml.dump(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": name, "namespace": namespace},
                "data": {k: str(v) for k, v in data.items()},
            }
        )

        return await self._apply_manifest(configmap_yaml, namespace)

    async def _create_secret(self, name: str, namespace: str, data: dict) -> str:
        """Create a Secret"""
        # Base64 encode the secret data
        encoded_data = {
            k: base64.b64encode(str(v).encode()).decode() for k, v in data.items()
        }

        secret_yaml = yaml.dump(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": name, "namespace": namespace},
                "type": "Opaque",
                "data": encoded_data,
            }
        )

        return await self._apply_manifest(secret_yaml, namespace)

    def _validate_yaml(self, yaml_content: str) -> None:
        """Validate yaml_content is parseable YAML. Raises ValidationError if not."""
        if not yaml_content or not yaml_content.strip():
            raise ValidationError("YAML content is empty")
        try:
            parsed = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            raise ValidationError(f"Invalid YAML content: {e}") from e
        if not isinstance(parsed, dict):
            raise ValidationError(
                f"YAML content must be a mapping (dict), got: {type(parsed).__name__}"
            )

    async def _apply_manifest(
        self, yaml_content: str, namespace: str = "default"
    ) -> str:
        """Apply a YAML manifest to the cluster."""
        self._validate_yaml(yaml_content)
        try:
            result = await self.tools.call_tool(
                "kubectl_apply", {"namespace": namespace, "manifest": yaml_content}
            )
            return str(result)
        except Exception as e:
            logger.error(f"[{AGENT_NAME}] Failed to apply manifest: {e}")
            raise ToolError(f"Failed to apply manifest: {e}") from e

    async def _restart_deployment(self, name: str, namespace: str) -> str:
        """Restart a deployment via rollout restart."""
        try:
            result = await self.tools.call_tool(
                "kubectl_rollout_restart", {"namespace": namespace, "deployment": name}
            )
            return str(result)
        except Exception as e:
            logger.error(f"[{AGENT_NAME}] Failed to restart deployment {name}: {e}")
            raise ToolError(f"Failed to restart deployment {name}: {e}") from e

    async def _scale_deployment(self, name: str, namespace: str, replicas: int) -> str:
        """Scale a deployment to specified replica count."""
        try:
            result = await self.tools.call_tool(
                "kubectl_scale",
                {"namespace": namespace, "deployment": name, "replicas": replicas},
            )
            logger.info(f"[{AGENT_NAME}] Scale result for {name}: {result}")
            if result is None:
                raise ToolError(f"Failed to scale deployment {name}: returned None")
            return str(result)
        except Exception as e:
            logger.error(f"[{AGENT_NAME}] Failed to scale deployment {name}: {e}")
            raise ToolError(f"Failed to scale deployment {name}: {e}") from e

    async def _delete_resource(
        self, resource_type: str, name: str, namespace: str
    ) -> str:
        """Delete a Kubernetes resource."""
        try:
            result = await self.tools.call_tool(
                "kubectl_delete",
                {"namespace": namespace, "resource_type": resource_type, "name": name},
            )
            return str(result)
        except Exception as e:
            logger.error(f"[{AGENT_NAME}] Failed to delete {resource_type}/{name}: {e}")
            raise ToolError(f"Failed to delete {resource_type}/{name}: {e}") from e

    async def _patch_resource(
        self, resource_type: str, name: str, namespace: str, patch: dict
    ) -> str:
        """Patch a Kubernetes resource.

        Note: Generic patching is not automated. Returns kubectl command for manual execution.
        """
        patch_json = json.dumps(patch)
        kubectl_cmd = f"kubectl patch {resource_type} {name} -n {namespace} --type=merge -p '{patch_json}'"
        logger.info(
            f"[{AGENT_NAME}] Patch operation requires manual intervention: {kubectl_cmd}"
        )
        return f"Manual intervention required. Run: {kubectl_cmd}"
