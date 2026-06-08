"""Verification service for K8s resources after remediation.

Uses an LLM to evaluate whether the remediation actually resolved the issue,
given the original problem context and a fresh K8s state snapshot.
Polls with a grace period to allow K8s controllers time to reconcile.
"""

import asyncio
import copy
import json
import logging
import re
import time

from langchain_core.messages import HumanMessage

from ..config import (
    get_settings,
    create_extraction_llm,
)
from ..state import AgentState


logger = logging.getLogger(__name__)

AGENT_NAME = "VerificationService"


class VerificationService:
    """Verification service that evaluates K8s resource status after remediation.

    Uses the K8sInvestigator's lightweight verification to gather a fresh K8s
    state snapshot, then sends the snapshot along with issue context to an LLM
    for structured evaluation of whether the fix resolved the problem.
    """

    def __init__(self, k8s_investigator):
        self.k8s_investigator = k8s_investigator
        self.llm = create_extraction_llm()

    async def verify_fix(self, state: AgentState) -> AgentState:
        """Verify that remediation fixed the issue.

        Uses a polling loop with grace period:
        1. Wait verification_initial_grace seconds for K8s controller reconciliation
        2. Poll every verification_poll_interval seconds
        3. Require verification_min_stable_checks consecutive successes
        4. Timeout after verification_timeout seconds
        """
        settings = get_settings()
        logger.info(
            f"[{AGENT_NAME}] Starting fix verification "
            f"(grace={settings.verification_initial_grace}s, timeout={settings.verification_timeout}s, "
            f"poll={settings.verification_poll_interval}s, stable_checks={settings.verification_min_stable_checks})"
        )

        # Grace period: let K8s controllers reconcile after remediation
        logger.info(
            f"[{AGENT_NAME}] Waiting {settings.verification_initial_grace}s "
            f"for K8s controller reconciliation"
        )
        await asyncio.sleep(settings.verification_initial_grace)

        start_time = time.monotonic()
        consecutive_successes = 0
        last_evidence = []
        poll_count = 0

        while (time.monotonic() - start_time) < settings.verification_timeout:
            poll_count += 1
            result = await self._check_once(state, settings)

            # Capture evidence from this poll unconditionally
            # (will be used if verification times out)
            last_evidence = result["evidence"]

            if result["resolved"]:
                consecutive_successes += 1
                logger.info(
                    f"[{AGENT_NAME}] Poll {poll_count}: healthy "
                    f"({consecutive_successes}/{settings.verification_min_stable_checks} stable)"
                )
                if consecutive_successes >= settings.verification_min_stable_checks:
                    state["issue_resolved"] = True
                    state["verification_evidence"] = result["evidence"]
                    # Track new issues detection for post-fix loop
                    state["new_issues_detected"] = result.get("new_issues_detected", False)
                    state["new_issues"] = result.get("new_issues", [])
                    logger.info(
                        f"[{AGENT_NAME}] Fix verified, new_issues_detected={state['new_issues_detected']}"
                    )
                    return state
            else:
                if consecutive_successes > 0:
                    logger.info(
                        f"[{AGENT_NAME}] Poll {poll_count}: not healthy "
                        f"(resetting stable count from {consecutive_successes})"
                    )
                consecutive_successes = 0

            # Check if we'd exceed timeout with another poll
            elapsed = time.monotonic() - start_time
            if (elapsed + settings.verification_poll_interval) >= settings.verification_timeout:
                break

            await asyncio.sleep(settings.verification_poll_interval)

        # Timeout reached
        elapsed = time.monotonic() - start_time
        logger.info(
            f"[{AGENT_NAME}] Verification timed out after {elapsed:.0f}s "
            f"({poll_count} polls, {consecutive_successes} consecutive successes)"
        )
        state["issue_resolved"] = False
        state["verification_evidence"] = last_evidence or [
            f"Verification timed out after {settings.verification_timeout}s"
        ]
        state["new_issues_detected"] = False
        return state

    async def _check_once(self, state: AgentState, settings) -> dict:
        """Run a single verification check using LLM evaluation.

        Gathers fresh K8s state via run_verification_only(), then sends
        that state plus issue context to the LLM for structured evaluation.

        Args:
            state: Current agent state.
            settings: Configuration settings (passed from verify_fix to avoid redundant calls).

        Returns:
            Dict with 'resolved' (bool) and 'evidence' (list of strings).
        """
        # Deep copy: run_verification_only mutates cluster_findings; prevent
        # those writes from propagating back into the caller's state between polls.
        poll_state = copy.deepcopy(dict(state))
        poll_state = await self.k8s_investigator.run_verification_only(poll_state)

        cluster_findings = poll_state.get("cluster_findings", {})

        # Check for tool/verification errors before invoking LLM
        if "error" in cluster_findings:
            logger.warning(
                f"[{AGENT_NAME}] Verification error: {cluster_findings['error']}"
            )
            return {
                "resolved": False,
                "evidence": [f"Verification error: {cluster_findings['error']}"],
            }

        # Build prompt and invoke LLM for evaluation
        prompt = self._build_verification_prompt(poll_state, cluster_findings)

        try:
            messages = [HumanMessage(content=prompt)]
            response = await asyncio.wait_for(
                self.llm.ainvoke(messages), timeout=settings.verification_llm_call_timeout
            )
            return self._parse_llm_verdict(response.content)
        except asyncio.TimeoutError:
            logger.warning(
                f"[{AGENT_NAME}] LLM call timed out after {settings.verification_llm_call_timeout}s"
            )
            return {
                "resolved": False,
                "evidence": [f"LLM evaluation timed out after {settings.verification_llm_call_timeout}s"],
            }
        except Exception as e:
            logger.warning(f"[{AGENT_NAME}] LLM evaluation error: {e}")
            return {
                "resolved": False,
                "evidence": [f"LLM evaluation error: {e}"],
            }

    def _build_verification_prompt(
        self, state: AgentState, findings: dict
    ) -> str:
        """Build a focused prompt for LLM verification evaluation.

        SECURITY: This method is a prompt injection attack surface. User-supplied
        fields from Jira (ticket_summary, ticket_description, root_cause, etc.)
        are interpolated into the prompt. An attacker who controls these fields
        could inject fake prompt sections (e.g., "CURRENT K8S STATE: {}") to
        manipulate the LLM's verdict.

        Mitigation: All user-controlled fields are sanitized via _sanitize_field()
        which collapses newlines/tabs and truncates length before interpolation.
        K8s resource values and events from cluster findings are also sanitized
        via _sanitize_field to prevent injection from adversarial cluster state.
        This prevents newline-based prompt section injection.

        Args:
            state: Current agent state with issue context.
            findings: Fresh cluster findings from run_verification_only().

        Returns:
            Prompt string for the LLM.
        """
        resources = findings.get("resources", {})
        events = findings.get("events", [])

        remediation = state.get("remediation_result") or {}
        action_taken = remediation.get("action_taken", "Unknown")

        # Sanitize user-controlled fields to prevent prompt injection
        ticket_summary = self._sanitize_field(
            state.get("ticket_summary", "Unknown")
        )
        ticket_description = self._sanitize_field(
            state.get("ticket_description", "N/A")
        )
        root_cause = self._sanitize_field(
            state.get("root_cause", "Unknown")
        )
        action_taken = self._sanitize_field(action_taken)

        # Sanitize K8s resource values from cluster findings
        pods_sanitized = self._sanitize_field(str(resources.get("pods", "")), max_len=500) or "N/A"
        deployment_sanitized = self._sanitize_field(str(resources.get("deployment", "")), max_len=500) or "N/A"
        service_sanitized = self._sanitize_field(str(resources.get("service", "")), max_len=500) or "N/A"
        endpoints_sanitized = self._sanitize_field(str(resources.get("endpoints", "")), max_len=500) or "N/A"
        events_sanitized = self._sanitize_field(str(events), max_len=500) if events else "None"

        return f"""Evaluate whether this Kubernetes remediation resolved the original issue.

ORIGINAL ISSUE:
- Summary: {ticket_summary}
- Description: {ticket_description}
- Root cause: {root_cause}

REMEDIATION ACTION: {action_taken}

CURRENT K8S STATE:
- Pods: {pods_sanitized}
- Deployment: {deployment_sanitized}
- Service: {service_sanitized}
- Endpoints: {endpoints_sanitized}
- Recent events: {events_sanitized}

IMPORTANT: After checking if the original issue is resolved, also check if there are any NEW issues in the cluster that are related to the affected resources or the fix that was applied. For example:
- New pods failing that weren't part of the original issue
- New events or errors in the affected namespace
- Resources affected by cascading effects of the fix

Respond with ONLY a JSON object (no markdown, no explanation):
{{"resolved": true/false, "confidence": "high"/"medium"/"low", "evidence": ["point1", "point2"], "reasoning": "brief explanation", "new_issues_detected": true/false, "new_issues": ["new issue 1", "new issue 2"]}}"""

    @staticmethod
    def _sanitize_field(value: str | None, max_len: int = 300) -> str:
        """Strip control characters and cap length to prevent prompt injection.

        Collapses newlines/tabs to spaces and truncates to prevent an attacker
        from injecting fake prompt sections (e.g., "CURRENT K8S STATE:") via
        Jira ticket content.

        Args:
            value: Field value from Jira or remediation result.
            max_len: Maximum length before truncation.

        Returns:
            Sanitized string safe for LLM prompt interpolation.
        """
        if not value:
            return ""
        # Collapse newlines/tabs to spaces; strip leading/trailing whitespace
        sanitized = re.sub(r"[\r\n\t]+", " ", str(value)).strip()
        return sanitized[:max_len]

    @staticmethod
    def _parse_llm_verdict(content: str) -> dict:
        """Parse LLM response into structured verification result.

        Handles raw JSON, markdown-wrapped JSON, and malformed responses.

        Args:
            content: Raw LLM response string.

        Returns:
            Dict with 'resolved' (bool) and 'evidence' (list of strings).
        """
        # Strip markdown code blocks if present
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if json_match:
            content = json_match.group(1)

        # Extract JSON object
        brace_match = re.search(r'\{[\s\S]*\}', content)
        if brace_match:
            content = brace_match.group(0)

        try:
            data = json.loads(content)
            # Handle string booleans ("true"/"false") in addition to actual booleans
            _raw = data.get("resolved", False)
            if isinstance(_raw, str):
                resolved = _raw.strip().lower() == "true"
            else:
                resolved = bool(_raw)
            confidence = data.get("confidence", "medium")
            # Low-confidence "resolved" verdicts are treated as unresolved
            # to prevent premature false-positive stable checks.
            if resolved and confidence == "low":
                resolved = False
            evidence = data.get("evidence", [])
            if not isinstance(evidence, list):
                evidence = [str(evidence)]
            reasoning = data.get("reasoning", "")
            if reasoning:
                evidence.append(f"Reasoning: {reasoning}")
            # Extract new issues detection
            new_issues_detected = bool(data.get("new_issues_detected", False))
            new_issues = data.get("new_issues", [])
            if not isinstance(new_issues, list):
                new_issues = [str(new_issues)] if new_issues else []
            return {
                "resolved": resolved,
                "evidence": evidence,
                "new_issues_detected": new_issues_detected,
                "new_issues": new_issues,
            }
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"[{AGENT_NAME}] Failed to parse LLM verdict: {e}")
            return {
                "resolved": False,
                "evidence": [f"LLM response parse error: {e}"],
                "new_issues_detected": False,
                "new_issues": [],
            }
