"""Diagnostician agent for synthesizing findings and generating remediation plans.

This agent is the decision-maker in the remediation workflow. It:
1. Synthesizes all investigation findings into a diagnosis
2. Generates a STRUCTURED remediation plan (not text instructions)
3. Delegates execution to K8sRemediationExecutor (which has no LLM)

Architecture note:
    The Diagnostician generates RemediationPlan directly in its diagnosis output,
    eliminating the redundant LLM call that was previously in K8sRemediationExecutor.
    K8sRemediationExecutor is now a pure executor with no LLM calls.
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from ..config import (
    MAX_CONTEXT_CHARS,
    get_settings,
    create_diagnosis_llm,
)
from ..exceptions import ToolError, RemediationError
from ..models import Diagnosis, RemediationPlan, RemediationStep, ActionType, get_llm_schema_for_remediation_plan
from ..state import AgentState
from ..utils.llm_utils import invoke_llm_with_retry
from .k8s_remediation_executor import K8sRemediationExecutor

logger = logging.getLogger(__name__)

# Agent identifier for error context
AGENT_NAME = "Diagnostician"

# Fallback values
DEFAULT_PREVENTIVE_MEASURES = [
    "Review and document this incident",
    "Implement monitoring for similar issues",
    "Consider adding alerting"
]


class Diagnostician:
    """Analyzes all gathered information, provides diagnosis, and orchestrates remediation"""

    # Static portion of the diagnosis system prompt (target/namespace injected at runtime).
    # The remediation_plan schema block is generated dynamically from the Pydantic model
    # via _build_diagnosis_system_prompt() to keep prompt and model in sync.
    _DIAGNOSIS_PROMPT_STATIC = """You are a senior DevOps diagnostician with deep expertise in Kubernetes troubleshooting.

Your job is to:
1. Diagnose the ROOT CAUSE of the issue
2. Create a STRUCTURED REMEDIATION PLAN that can be executed automatically

TARGET: {target_deployment} in namespace {namespace}

## Diagnosis Guidelines
- Be specific and actionable
- Distinguish between symptoms and root causes
- If evidence is weak, set remediation_possible=false and provide manual_instructions

## CRITICAL - Dependency Assumptions
- Only claim component A depends on component B if there is VERIFIED EVIDENCE
- Other failing components in the same namespace are NOT automatically related
- A deployment with 0 replicas is its own issue - do not blame other components without evidence

## Remediation Plan Guidelines

Set remediation_possible=true and specify an action ONLY if you can determine:
1. The exact resource to modify (name, namespace, resource_type)
2. The exact parameters needed (replicas, data values, etc.)

### Safe Automated Actions (set remediation_possible=true):
- **scale**: Scale deployment to specific replica count (set replicas field)
- **restart**: Rollout restart to pick up config changes or recover from transient failures
- **delete**: Delete stuck/crashed pods to trigger recreation
- **create_configmap**: ONLY if you find the actual required values in historical resolutions or ticket
- **create_secret**: ONLY if you find the actual required values (never guess)
- **patch**: Patch resource with specific values (set data field)

### When to Require Manual Intervention (set remediation_possible=false):
- Missing configuration values that aren't in the context
- Actions requiring domain knowledge you don't have
- Low confidence situations

NOTE: Multi-step plans where all steps have known values should use remediation_possible=true
with multiple entries in the steps array. Only use manual_intervention when values cannot be determined.

## Confidence Levels
- **high**: Clear evidence, known pattern, confident in root cause AND remediation
- **medium**: Strong evidence but some ambiguity, likely fix identified
- **low**: Limited evidence, multiple possibilities, manual investigation needed

Be honest about uncertainty. A recommendation for human review is better than a wrong fix.
NEVER use placeholder values like 'TODO', 'changeme', 'placeholder' in ConfigMaps/Secrets.

## RESPONSE FORMAT

Respond with ONLY a JSON object, no explanation or markdown. Use this exact schema:
{{
  "root_cause": "detailed root cause explanation (min 10 chars)",
  "confidence_level": "high|medium|low",
  "remediation_plan": {remediation_plan_schema},
  "preventive_measures": ["measure 1", "measure 2", "measure 3"],
  "evidence": ["evidence 1", "evidence 2"],
  "category": "resource_exhaustion|configuration_error|network_issue|application_bug|deployment_failure|image_pull_error|permission_error|unknown"
}}

MULTI-STEP PLANS: If the fix requires multiple ordered actions (e.g., create a ConfigMap THEN restart the deployment),
include multiple objects in the "steps" array. Steps execute sequentially; if any step fails, later steps are skipped.
For single-action fixes, use a single-element steps array."""

    def __init__(self, remediation_agent: K8sRemediationExecutor = None):
        self.llm = create_diagnosis_llm()
        self.remediation_agent = remediation_agent

    async def run(self, state: AgentState) -> AgentState:
        """
        Synthesize all information from previous agents and create diagnosis with remediation plan.

        Combines:
        - Ticket information (from JiraAgent)
        - Historical patterns (from HistoryAgent)
        - Cluster investigation (from K8sInvestigator)

        Produces:
        - Root cause analysis
        - Structured remediation plan (RemediationPlan)
        - Confidence level
        - Preventive measures

        Architecture note:
            This method generates the remediation plan DIRECTLY as structured output,
            eliminating the need for a separate LLM call in K8sRemediationExecutor.
        """
        logger.info("Synthesizing findings and creating diagnosis with remediation plan")

        system_prompt = self._build_diagnosis_system_prompt(state)

        try:
            context = self._build_context(state)
            state["_cached_diagnosis_context"] = context

            diagnosis_prompt = self._build_diagnosis_prompt(context, state)

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=diagnosis_prompt)
            ]

            # Single LLM call produces BOTH diagnosis AND remediation plan
            # Manual JSON parsing (vLLM CPU lacks guided decoding)
            response = await invoke_llm_with_retry(self.llm, messages)
            diagnosis = self._parse_json_response(response.content, Diagnosis)

            self._update_state_from_diagnosis(diagnosis, state)

            logger.info(
                f"Diagnosis complete: confidence={state.get('confidence_level')}, "
                f"action={diagnosis.remediation_plan.action.value if diagnosis.remediation_plan else 'none'}"
            )

        except ValidationError as e:
            logger.warning(f"[{AGENT_NAME}] Structured output validation failed: {e}")
            self._apply_fallback_diagnosis(state, f"Validation error: {e}")
        except ToolError as e:
            logger.warning(f"[{AGENT_NAME}] LLM diagnosis failed: {e}")
            self._apply_fallback_diagnosis(state, str(e))
        except Exception as e:
            logger.exception(f"[{AGENT_NAME}] Unexpected error during diagnosis, using rule-based fallback")
            self._apply_fallback_diagnosis(state, str(e))

        return state

    @staticmethod
    def _parse_json_response(content: str, model_class):
        """Extract JSON from LLM response and parse into Pydantic model.
        
        Raises:
            ToolError: If no valid JSON object is found or JSON parsing fails.
            ValidationError: If the parsed JSON doesn't match the model schema.
        """
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if json_match:
            content = json_match.group(1)
        else:
            brace_match = re.search(r'\{[\s\S]*\}', content)
            if brace_match:
                content = brace_match.group(0)
            else:
                raise ToolError(
                    f"No JSON object found in LLM response. Raw: {content[:200]!r}"
                )
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ToolError(
                f"Invalid JSON from LLM: {exc}. Raw: {content[:200]!r}"
            ) from exc
        return model_class(**data)

    def _build_diagnosis_system_prompt(self, state: AgentState) -> str:
        """Build the system prompt for diagnosis with integrated remediation planning.

        Injects the dynamically generated RemediationPlan schema (legacy fields
        excluded) so the prompt stays in sync with the Pydantic model automatically.
        """
        target_deployment = self._get_target_deployment(state)
        namespace = state.get("namespace", "default")

        remediation_plan_schema = json.dumps(
            get_llm_schema_for_remediation_plan(), indent=2
        )

        return self._DIAGNOSIS_PROMPT_STATIC.format(
            target_deployment=target_deployment,
            namespace=namespace,
            remediation_plan_schema=remediation_plan_schema,
        )

    def _build_diagnosis_prompt(self, context: str, state: AgentState) -> str:
        """Build the user prompt for diagnosis with remediation context."""
        target_deployment = self._get_target_deployment(state)
        historical_context = self._build_historical_context(state)
        retry_guidance = self._build_retry_guidance(state)

        return f"""Based on all the investigation data below, provide a comprehensive diagnosis WITH a structured remediation plan.

{context}

{historical_context}
{retry_guidance}

Previous remediation attempts: {self._format_remediation_history(state)}

Analyze the data and respond with ONLY a valid JSON object matching the schema from your instructions.
Target deployment for remediation: {target_deployment}
"""

    def _apply_fallback_diagnosis(self, state: AgentState, error_msg: str) -> None:
        """Apply rule-based fallback diagnosis when LLM fails.

        Args:
            state: Agent state to update
            error_msg: Error message for context
        """
        cluster_findings = state.get("cluster_findings", {})
        problem_pods = cluster_findings.get("problem_pods", [])
        recommendations = cluster_findings.get("recommendations", [])
        preliminary = cluster_findings.get("preliminary_findings", "")

        if problem_pods:
            state["root_cause"] = f"Issue detected in pods: {', '.join(problem_pods)}. {preliminary}"
        else:
            state["root_cause"] = preliminary or "Unable to determine root cause - manual investigation needed"

        state["recommended_action"] = "\n".join(recommendations) if recommendations else "Review cluster findings and ticket details manually"
        state["confidence_level"] = "Low"
        state["preventive_measures"] = DEFAULT_PREVENTIVE_MEASURES

        # Fallback remediation plan - always manual intervention (no steps)
        state["remediation_plan"] = {
            "remediation_possible": False,
            "steps": [],
            "reason": f"Fallback diagnosis due to: {error_msg}",
            "manual_instructions": state["recommended_action"],
        }

        logger.info(f"[{AGENT_NAME}] Using rule-based diagnosis (LLM unavailable)")

    async def attempt_remediation(self, state: AgentState) -> AgentState:
        """
        Attempt to remediate the issue using the structured remediation plan.

        This method:
        1. Retrieves the structured remediation plan from state (generated in run())
        2. Validates the plan can be executed
        3. Calls the remediation agent to execute the plan (NO LLM call)
        4. Updates the state with remediation results

        Architecture note:
            The remediation plan was generated during diagnosis (run() method).
            This eliminates the redundant LLM call that was previously here.
            K8sRemediationExecutor.execute_plan() is now a pure executor.

        Returns:
            Updated state with remediation results
        """
        if not self.remediation_agent:
            logger.warning("No remediation agent configured - skipping remediation")
            state["remediation_attempted"] = False
            state["remediation_result"] = {"error": "No remediation agent configured"}
            return state

        logger.info("Attempting remediation using structured plan from diagnosis")

        # Check confidence level
        confidence = state.get("confidence_level", "").lower()
        if confidence == "low":
            logger.info("Confidence too low for automated remediation")
            state["remediation_attempted"] = False
            state["remediation_result"] = {"skipped": True, "reason": "Low confidence - manual intervention recommended"}
            return state

        # Get the structured remediation plan from state
        remediation_plan = state.get("remediation_plan")
        if not remediation_plan:
            logger.warning("No remediation plan in state - cannot proceed")
            state["remediation_attempted"] = False
            state["remediation_result"] = {"error": "No remediation plan generated during diagnosis"}
            return state

        # Check if remediation is possible
        if not remediation_plan.get("remediation_possible", False):
            manual_instructions = remediation_plan.get("manual_instructions", "Manual intervention required")
            logger.info(f"Remediation not possible: {remediation_plan.get('reason', 'unknown reason')}")
            state["remediation_attempted"] = False
            state["remediation_result"] = {
                "success": False,
                "skipped": True,
                "reason": remediation_plan.get("reason", "Manual intervention required"),
                "manual_instructions": manual_instructions,
            }
            return state

        try:
            # Convert dict to RemediationPlan if needed
            plan = RemediationPlan(**remediation_plan) if isinstance(remediation_plan, dict) else remediation_plan

            step_summary = ", ".join(
                f"{s.action.value}/{s.resource_type}/{s.name}" for s in plan.steps
            ) if plan.steps else f"{plan.action.value}/{plan.resource_type}/{plan.name}"
            logger.info(
                f"Executing remediation plan: steps=[{step_summary}], namespace={plan.namespace}"
            )

            # Call remediation agent to execute the plan (NO LLM call)
            result = await self.remediation_agent.execute_plan(state, plan)

            state["remediation_attempted"] = True
            state["remediation_result"] = result

            if result.get("success"):
                logger.info(f"Remediation successful: {result.get('action_taken')}")
            else:
                logger.warning(f"Remediation failed: {result.get('error')}")

        except RemediationError as e:
            logger.error(f"[{AGENT_NAME}] Remediation error: {e}")
            state["remediation_attempted"] = True
            state["remediation_result"] = {
                "success": False,
                "error": str(e),
                "error_type": "remediation_error",
                "action": getattr(e, 'action', None)
            }
        except ValidationError as e:
            logger.error(f"[{AGENT_NAME}] Invalid remediation plan: {e}")
            state["remediation_attempted"] = False
            state["remediation_result"] = {
                "success": False,
                "error": f"Invalid remediation plan: {e}",
                "error_type": "validation_error"
            }
        except Exception as e:
            logger.error(f"[{AGENT_NAME}] Unexpected error during remediation: {e}")
            state["remediation_attempted"] = True
            state["remediation_result"] = {
                "success": False,
                "error": f"Remediation failed unexpectedly: {str(e)}",
                "error_type": "unexpected_error"
            }

        return state

    def _get_target_deployment(self, state: AgentState) -> str:
        """Extract target deployment from state.

        Args:
            state: Current agent state

        Returns:
            Target deployment name
        """
        affected_resources = state.get("affected_resources", {})
        affected_deployments = affected_resources.get("deployments", [])
        return affected_deployments[0] if affected_deployments else "unknown"

    def _build_historical_context(self, state: AgentState) -> str:
        """Build historical context from similar tickets.

        On retries (remediation_count > 0), historical context is skipped
        since it was already consumed in the initial diagnosis.

        Args:
            state: Current agent state

        Returns:
            Historical context string
        """
        if state.get("remediation_count", 0) > 0:
            return ""

        similar_tickets = state.get("similar_tickets", [])
        past_resolutions = state.get("past_resolutions", [])

        if not similar_tickets and not past_resolutions:
            return ""

        context = "\n=== HISTORICAL RESOLUTIONS (check for exact commands) ===\n"

        if past_resolutions:
            context += "Resolution insights from similar issues:\n"
            for res in past_resolutions[:5]:
                context += f"  {res}\n"

        if similar_tickets:
            context += "\nResolution comments from similar tickets:\n"
            for ticket in similar_tickets[:3]:
                ticket_key = ticket.get("key", "Unknown")
                last_comment = ticket.get("last_comment", "")
                if last_comment:
                    comment_preview = last_comment[:500] if len(last_comment) > 500 else last_comment
                    context += f"\n{ticket_key} resolution:\n{comment_preview}\n"

        return context

    def _build_retry_guidance(self, state: AgentState) -> str:
        """Build retry guidance if previous attempt succeeded but verification timed out.

        Args:
            state: Current agent state

        Returns:
            Retry guidance string
        """
        remediation_history = state.get('remediation_history', [])
        if not remediation_history:
            return ""

        last_attempt = remediation_history[-1]
        if not last_attempt or not last_attempt.get("success"):
            return ""

        return f"""
IMPORTANT: Previous action "{last_attempt.get('action', 'unknown')}" reported SUCCESS but verification timed out.
This usually means Kubernetes needs more time to reconcile. DO NOT try a different action.
Either return the SAME action to retry, or return "MANUAL_INTERVENTION_REQUIRED: [reason]" if you believe the issue is unrelated.
"""

    def _format_remediation_history(self, state: AgentState) -> str:
        """Format remediation history, summarizing all but the last entry.

        For 2+ entries the earlier ones are compressed to one-line summaries,
        keeping only the last entry in full detail.

        Args:
            state: Current agent state

        Returns:
            Formatted remediation history string
        """
        history = state.get("remediation_history", [])
        if not history:
            return "None"

        if len(history) == 1:
            return str(history[0])

        lines: list[str] = []
        for i, entry in enumerate(history[:-1]):
            action = entry.get("action", "unknown") if isinstance(entry, dict) else str(entry)
            status = "FAILED" if isinstance(entry, dict) and not entry.get("success") else "OK"
            lines.append(f"#{i + 1}: {action} -> {status}")

        lines.append(f"Last attempt (full): {history[-1]}")
        return "\n".join(lines)

    def _build_context(self, state: AgentState) -> str:
        """
        Build comprehensive context from all agent findings.
        """
        # Removed: dependency analysis (discovered_dependencies, unrelated_issues, dependency_evidence)
        # This has been simplified in the POC

        affected_resources = state.get("affected_resources", {})
        affected_deployments = affected_resources.get("deployments", [])
        affected_services = affected_resources.get("services", [])
        target_deployment = affected_deployments[0] if affected_deployments else "N/A"
        target_service = affected_services[0] if affected_services else "N/A"

        is_retry = state.get("remediation_count", 0) > 0

        if is_retry:
            historical_section = """
### Historical Analysis
*Omitted on retry*
"""
        else:
            historical_section = f"""
### Historical Analysis
**Similar Past Issues:**
{self._format_list(state.get("similar_tickets", []))}

**Resolution Patterns:**
{self._format_list(state.get("past_resolutions", []))}
"""

        context = f"""
### Original Ticket
**Summary:** {state.get("ticket_summary", "N/A")}
**Description:** {state.get("ticket_description", "N/A")}
**Labels:** {state.get("ticket_labels", [])}
**Priority:** {state.get("ticket_priority", "N/A")}
**Target Deployment:** {target_deployment}
**Target Service:** {target_service}
**Namespace:** {state.get("namespace", "N/A")}
{historical_section}
### Kubernetes Investigation
**Pod Statuses:**
{state.get("cluster_findings", {}).get("resources", {}).get("pods", "N/A")}

**Key Logs:**
{str(state.get("cluster_findings", {}).get("logs", "N/A"))[:MAX_CONTEXT_CHARS["logs"]]}

**Recent Events:**
{str(state.get("cluster_findings", {}).get("events", []))[:MAX_CONTEXT_CHARS["events"]]}

**Investigation Summary:**
{state.get("cluster_findings", {}).get("preliminary_findings", "N/A")}

### Analysis Guidelines
- Focus diagnosis on the TARGET DEPLOYMENT: {target_deployment}
- If the deployment has 0 replicas, that is the direct issue to fix
"""

        # Jenkins failure context (when available)
        jenkins_findings = state.get("jenkins_findings", {})
        if jenkins_findings and not jenkins_findings.get("error"):
            jenkins_section = f"""
### Jenkins Failure Context
**Failure Type:** {jenkins_findings.get('failure_type', 'unknown')}
**Root Cause:** {jenkins_findings.get('root_cause', 'N/A')}
**Error Snippets:**
{self._format_list(jenkins_findings.get('error_snippets', []))}
**Console Log Summary:** {jenkins_findings.get('console_log_summary', 'N/A')}
**Build Info:** {jenkins_findings.get('build_info', 'N/A')}
**Parent Build:** {jenkins_findings.get('parent_build', 'N/A')}
"""
            context += jenkins_section
        elif jenkins_findings.get("error"):
            context += f"""
### Jenkins Failure Context
**Status:** Investigation failed: {jenkins_findings.get('error')}
"""

        return context

    def _format_list(self, items: list) -> str:
        """Format a list for display"""
        if not items:
            return "None found"

        settings = get_settings()
        formatted = ""
        for item in items[:settings.max_similar_tickets]:
            formatted += f"- {item}\n"

        return formatted

    def _update_state_from_diagnosis(self, diagnosis: Diagnosis, state: AgentState) -> None:
        """
        Update state from structured Diagnosis model.

        Args:
            diagnosis: Pydantic Diagnosis model with validated fields
            state: Agent state to update
        """
        state["root_cause"] = diagnosis.root_cause
        state["confidence_level"] = diagnosis.confidence_level.value.capitalize()
        state["preventive_measures"] = (
            diagnosis.preventive_measures
            if diagnosis.preventive_measures
            else DEFAULT_PREVENTIVE_MEASURES
        )

        # Store the structured remediation plan
        if diagnosis.remediation_plan:
            plan = diagnosis.remediation_plan
            state["remediation_plan"] = plan.model_dump()

            # Generate human-readable recommended_action for HITL display and comments
            state["recommended_action"] = self._format_plan_as_text(plan, state)

        # Store additional structured data
        if diagnosis.evidence:
            state["verification_evidence"] = diagnosis.evidence

    def _format_plan_as_text(self, plan: RemediationPlan, state: AgentState = None) -> str:
        """Format a RemediationPlan as human-readable text for display.

        Supports multi-step plans: each step is formatted as a numbered item.

        Args:
            plan: The structured remediation plan
            state: Agent state for cluster context

        Returns:
            Human-readable description of the planned action(s)
        """
        if not plan.remediation_possible:
            return plan.manual_instructions or "Manual intervention required"

        cluster = state.get("target_cluster") if state else None
        cluster_suffix = f" on cluster '{cluster}'" if cluster else ""

        steps = plan.steps
        if not steps:
            return "No remediation steps defined"

        def _format_step(step: RemediationStep) -> str:
            action_descriptions = {
                ActionType.SCALE: f"Scale {step.resource_type} '{step.name}' to {step.replicas} replicas",
                ActionType.RESTART: f"Restart {step.resource_type} '{step.name}'",
                ActionType.DELETE: f"Delete {step.resource_type} '{step.name}'",
                ActionType.CREATE_CONFIGMAP: f"Create ConfigMap '{step.name}' with keys: {list(step.data.keys()) if step.data else 'N/A'}",
                ActionType.CREATE_SECRET: f"Create Secret '{step.name}'",
                ActionType.APPLY_MANIFEST: "Apply YAML manifest",
                ActionType.PATCH: f"Patch {step.resource_type} '{step.name}'",
                ActionType.MANUAL_INTERVENTION: step.reason or "Manual intervention required",
            }
            base_action = action_descriptions.get(step.action, f"Execute {step.action.value}")
            ns_suffix = f" in namespace '{step.namespace}'" if step.namespace else ""
            return f"{base_action}{ns_suffix}. Reason: {step.reason}"

        if len(steps) == 1:
            return f"{_format_step(steps[0])}{cluster_suffix}"

        lines = [f"Multi-step remediation plan ({len(steps)} steps){cluster_suffix}:"]
        for i, step in enumerate(steps, start=1):
            lines.append(f"  Step {i}: {_format_step(step)}")
        return "\n".join(lines)
