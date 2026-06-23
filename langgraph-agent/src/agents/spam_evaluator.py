"""SpamEvaluator agent — determines whether a ticket is actionable or spam."""

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

from ..config import create_extraction_llm
from ..state import AgentState

logger = logging.getLogger(__name__)

# Prompt instructs the LLM to return structured JSON without chain-of-thought.
# Reasoning is disabled via chat_template_kwargs(enable_thinking=False) in
# config.create_llm — the prompt-level `/no_think` directive is ignored by Qwen3.6
# on the vLLM-Gaudi build (verified), so it's removed here to avoid confusion.
_SYSTEM_PROMPT = """You are a DevOps triage assistant that evaluates Jira tickets for the DevOps_K8S team.
Your job is to decide whether a ticket is actionable (valid) or should be returned to the reporter (spam).

SPAM rules — a ticket is spam if ANY of these are true:
1. No Jenkins job link is present (we cannot debug or reproduce without it).
2. No server/node name is identifiable — either directly in the body OR derivable from the Jenkins URL.
3. The issue is outside our scope: hardware, firmware, BIOS, IT support, Linux kernel, or network problems that are NOT Kubernetes-level (e.g. physical NIC failure, OS kernel panic, IPMI).
4. The Jenkins link points to a CI system we do not own (e.g. customer CI, external PR checks).

VALID — a ticket is actionable if it describes a real Kubernetes-layer problem (pod failures,
deployment issues, resource constraints, scheduler problems, CNI/storage plugin issues, etc.)
AND contains a Jenkins link AND a server/node name can be determined.

Respond with ONLY a valid JSON object — no markdown, no explanation:
{
  "is_spam": true|false,
  "jenkins_link_found": true|false,
  "server_name_found": true|false,
  "issue_scope": "k8s"|"hardware"|"firmware"|"jenkins"|"it"|"kernel"|"network"|"other",
  "spam_reason": "<one sentence explaining why this is spam, or null if valid>",
  "triage_comment": "<polite comment to post on the ticket if spam — written as if from a helpful colleague, never uses the word spam, explains what information is missing or why it is out of scope. null if valid>"
}"""

_USER_TEMPLATE = """Ticket: {ticket_id}
Summary: {summary}
Description:
{description}"""


class SpamEvaluator:
    """Evaluates whether a ticket is actionable using the Qwen3 LLM."""

    def __init__(self):
        self._llm = None  # lazy-init so import doesn't fail without VLLM_ENDPOINT

    def _get_llm(self):
        if self._llm is None:
            self._llm = create_extraction_llm()
        return self._llm

    async def run(self, state: AgentState) -> dict[str, Any]:
        ticket_id = state.get("ticket_id", "UNKNOWN")
        summary = state.get("ticket_summary", "")
        description = state.get("ticket_description", "") or ""

        logger.info("Evaluating ticket %s for spam", ticket_id)

        try:
            result = await self._evaluate(ticket_id, summary, description)
        except Exception as exc:
            logger.error("SpamEvaluator failed for %s: %s", ticket_id, exc)
            # On LLM failure, mark as valid so ticket is not silently dropped.
            return {
                "is_spam": False,
                "error": f"LLM evaluation failed: {exc}",
            }

        logger.info(
            "Ticket %s: is_spam=%s jenkins=%s server=%s scope=%s",
            ticket_id,
            result.get("is_spam"),
            result.get("jenkins_link_found"),
            result.get("server_name_found"),
            result.get("issue_scope"),
        )

        return {
            "is_spam": result.get("is_spam", False),
            "spam_reason": result.get("spam_reason"),
            "jenkins_link_found": result.get("jenkins_link_found", False),
            "server_name_found": result.get("server_name_found", False),
            "issue_scope": result.get("issue_scope", "other"),
            "triage_comment": result.get("triage_comment"),
        }

    async def _evaluate(self, ticket_id: str, summary: str, description: str) -> dict[str, Any]:
        llm = self._get_llm()
        user_msg = _USER_TEMPLATE.format(
            ticket_id=ticket_id,
            summary=summary,
            description=description[:3000],  # cap to avoid huge prompts
        )

        response = await llm.ainvoke([
            HumanMessage(content=_SYSTEM_PROMPT + "\n\n" + user_msg)
        ])

        content = response.content
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"LLM returned empty response: {content!r}")

        # Strip markdown code fences if present
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        if match:
            content = match.group(1)
        else:
            # Extract first JSON object
            brace_match = re.search(r"\{[\s\S]*\}", content)
            if brace_match:
                content = brace_match.group(0)

        return json.loads(content)
