"""TicketRouter agent — assigns the ticket to a team member or back to the reporter."""

import logging
from typing import Any

from ..state import AgentState
from ..tools.jira_tools import JiraTools
from ..config import get_settings

logger = logging.getLogger(__name__)


class TicketRouter:
    """Routes a triaged ticket to its final destination.

    - Spam: reassigns to original reporter, posts explanatory comment, stamps
      triage-agent-done label.
    - Valid: assigns to next team member via round-robin, stamps
      triage-agent-done label.
    """

    def __init__(
        self,
        jira_tools: JiraTools,
        team_members: list[str],
        processed_label: str,
        in_progress_label: str,
    ):
        self._jira = jira_tools
        self._team_members = team_members
        self._processed_label = processed_label
        self._in_progress_label = in_progress_label

    async def run(self, state: AgentState, rr_index: int) -> dict[str, Any]:
        ticket_id = state.get("ticket_id", "")
        is_spam = state.get("is_spam", False)

        if is_spam:
            return await self._handle_spam(state, ticket_id)
        else:
            return await self._handle_valid(state, ticket_id, rr_index)

    async def _handle_spam(self, state: AgentState, ticket_id: str) -> dict[str, Any]:
        reporter = state.get("ticket_reporter")
        comment = state.get("triage_comment") or self._default_comment(state)

        logger.info("Ticket %s is spam — returning to reporter %s", ticket_id, reporter)

        if reporter:
            try:
                await self._jira.update_assignee(ticket_id, reporter)
                logger.info("Reassigned %s to reporter %s", ticket_id, reporter)
            except Exception as exc:
                logger.warning("Failed to reassign %s to reporter: %s", ticket_id, exc)

        try:
            await self._jira.add_comment(ticket_id, comment)
        except Exception as exc:
            logger.warning("Failed to post comment on %s: %s", ticket_id, exc)

        try:
            await self._jira.add_label(ticket_id, self._processed_label)
        except Exception as exc:
            logger.warning("Failed to stamp processed label on %s: %s", ticket_id, exc)

        try:
            await self._jira.remove_label(ticket_id, self._in_progress_label)
        except Exception as exc:
            logger.warning("Failed to remove in-progress label on %s: %s", ticket_id, exc)

        return {"triage_comment": comment, "triage_complete": True}

    async def _handle_valid(self, state: AgentState, ticket_id: str, rr_index: int) -> dict[str, Any]:
        assignee = self._team_members[rr_index % len(self._team_members)]
        logger.info("Ticket %s is valid — assigning to %s", ticket_id, assignee)

        try:
            await self._jira.update_assignee(ticket_id, assignee)
        except Exception as exc:
            logger.warning("Failed to assign %s to %s: %s", ticket_id, assignee, exc)

        try:
            await self._jira.add_label(ticket_id, self._processed_label)
        except Exception as exc:
            logger.warning("Failed to stamp processed label on %s: %s", ticket_id, exc)

        try:
            await self._jira.remove_label(ticket_id, self._in_progress_label)
        except Exception as exc:
            logger.warning("Failed to remove in-progress label on %s: %s", ticket_id, exc)

        return {"assigned_to": assignee, "triage_complete": True}

    @staticmethod
    def _default_comment(state: AgentState) -> str:
        """Fallback comment when the LLM didn't produce one."""
        reason = state.get("spam_reason") or "missing required information"
        jenkins = state.get("jenkins_link_found", False)
        server = state.get("server_name_found", False)

        missing = []
        if not jenkins:
            missing.append("a Jenkins job link (required to reproduce and debug the issue)")
        if not server:
            missing.append("a server or node name")

        if missing:
            return (
                f"Hi,\n\nThank you for submitting this ticket. Unfortunately we are unable to "
                f"proceed without: {', '.join(missing)}.\n\n"
                "Please update the ticket with the missing details and reassign it to the "
                "DevOps_K8S component when ready.\n\nThank you!"
            )

        scope = state.get("issue_scope", "other")
        return (
            f"Hi,\n\nThank you for this report. After reviewing it, it appears this issue "
            f"falls outside the Kubernetes layer our team manages (scope: {scope}). "
            "Please route it to the appropriate team.\n\nThank you!"
        )
