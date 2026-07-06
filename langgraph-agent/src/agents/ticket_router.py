"""TicketRouter agent — assigns the ticket to a team member or back to the reporter."""

import logging
from typing import Any

from ..state import AgentState
from ..tools.jira_tools import JiraTools
from ..config import get_settings

logger = logging.getLogger(__name__)

# Marks the start of the appended judgment block in a ticket's description.
# update_issue replaces the whole description server-side (no native append),
# so re-triaging the same ticket must truncate at this marker first instead
# of stacking a new block on every run.
_JUDGMENT_MARKER = "---\n**Triage Judgment**"

# issue_scope -> (who should own it, terse problem statement), used for the
# INVALID path once Jenkins link + server name are both present (i.e. the
# ticket is well-formed but simply out of this team's scope).
_SCOPE_OWNERS: dict[str, tuple[str, str]] = {
    "hardware": ("Hardware/Facilities team", "physical hardware fault (e.g. disk, NIC, PSU) outside the Kubernetes layer"),
    "firmware": ("Firmware/BIOS team", "firmware or BIOS-level issue outside the Kubernetes layer"),
    "kernel": ("Linux/Kernel team", "OS kernel-level issue (e.g. panic, driver fault) outside the Kubernetes layer"),
    "network": ("Network team", "network-level fault (e.g. switch, routing, connectivity) outside the Kubernetes layer"),
    "jenkins": ("CI/Jenkins team", "the referenced Jenkins job is not owned by this team"),
    "it": ("IT support", "general IT support request outside the Kubernetes layer"),
    "other": ("Appropriate owning team (unclear from ticket content)", "issue does not fall within the Kubernetes layer this team manages"),
}

# issue_scope -> short noun-phrase, used in the VALID path's "why" line.
_SCOPE_DESCRIPTIONS: dict[str, str] = {
    "k8s": "Kubernetes-layer issue",
}


class TicketRouter:
    """Routes a triaged ticket to its final destination.

    - Spam: reassigns to original reporter, writes a judgment note into the
      description, stamps triage-agent-done + verdict-invalid labels.
    - Valid: assigns to next team member via round-robin, writes a judgment
      note into the description, stamps triage-agent-done + verdict-valid
      labels.
    """

    def __init__(
        self,
        jira_tools: JiraTools,
        team_members: list[str],
        processed_label: str,
        in_progress_label: str,
        verdict_valid_label: str,
        verdict_invalid_label: str,
    ):
        self._jira = jira_tools
        self._team_members = team_members
        self._processed_label = processed_label
        self._in_progress_label = in_progress_label
        self._verdict_valid_label = verdict_valid_label
        self._verdict_invalid_label = verdict_invalid_label

    async def run(self, state: AgentState, rr_index: int) -> dict[str, Any]:
        ticket_id = state.get("ticket_id", "")
        is_spam = state.get("is_spam", False)

        if is_spam:
            return await self._handle_spam(state, ticket_id)
        else:
            return await self._handle_valid(state, ticket_id, rr_index)

    async def _handle_spam(self, state: AgentState, ticket_id: str) -> dict[str, Any]:
        reporter = state.get("ticket_reporter")

        logger.info("Ticket %s is spam — returning to reporter %s", ticket_id, reporter)

        if reporter:
            try:
                await self._jira.update_assignee(ticket_id, reporter)
                logger.info("Reassigned %s to reporter %s", ticket_id, reporter)
            except Exception as exc:
                logger.warning("Failed to reassign %s to reporter: %s", ticket_id, exc)

        try:
            await self._jira.add_label(ticket_id, self._processed_label)
        except Exception as exc:
            logger.warning("Failed to stamp processed label on %s: %s", ticket_id, exc)

        try:
            await self._jira.remove_label(ticket_id, self._in_progress_label)
        except Exception as exc:
            logger.warning("Failed to remove in-progress label on %s: %s", ticket_id, exc)

        reason = state.get("spam_reason") or "missing required information"
        who, problem = self._invalid_who_and_problem(state)
        description = self._build_judgment_description(state, [
            "Verdict: INVALID (spam)",
            f"Why: {reason}",
            f"Who should handle it: {who}",
            f"Problem: {problem}",
        ])
        try:
            await self._jira.update_issue(ticket_id, description=description)
        except Exception as exc:
            logger.warning("Failed to update description on %s: %s", ticket_id, exc)

        try:
            await self._jira.add_label(ticket_id, self._verdict_invalid_label)
        except Exception as exc:
            logger.warning("Failed to stamp verdict label on %s: %s", ticket_id, exc)

        return {"triage_complete": True}

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

        why = self._valid_why(state)
        description = self._build_judgment_description(state, ["Verdict: VALID", f"Why: {why}"])
        try:
            await self._jira.update_issue(ticket_id, description=description)
        except Exception as exc:
            logger.warning("Failed to update description on %s: %s", ticket_id, exc)

        try:
            await self._jira.add_label(ticket_id, self._verdict_valid_label)
        except Exception as exc:
            logger.warning("Failed to stamp verdict label on %s: %s", ticket_id, exc)

        return {"assigned_to": assignee, "triage_complete": True}

    @staticmethod
    def _valid_why(state: AgentState) -> str:
        scope = state.get("issue_scope", "k8s")
        scope_desc = _SCOPE_DESCRIPTIONS.get(scope, "Kubernetes-layer issue")
        jenkins = state.get("jenkins_link_found", False)
        server = state.get("server_name_found", False)
        details = []
        if jenkins:
            details.append("Jenkins link")
        if server:
            details.append("server identified")
        suffix = f" with {' and '.join(details)}" if details else ""
        return f"{scope_desc}{suffix}."

    @staticmethod
    def _invalid_who_and_problem(state: AgentState) -> tuple[str, str]:
        jenkins = state.get("jenkins_link_found", False)
        server = state.get("server_name_found", False)

        if not jenkins or not server:
            missing = []
            if not jenkins:
                missing.append("a Jenkins job link")
            if not server:
                missing.append("a server or node name")
            return (
                "Reporter (must supply the missing information before re-triage)",
                f"ticket is missing: {', '.join(missing)}",
            )

        scope = state.get("issue_scope", "other")
        return _SCOPE_OWNERS.get(scope, _SCOPE_OWNERS["other"])

    @staticmethod
    def _strip_previous_judgment(description: str) -> str:
        idx = description.find(_JUDGMENT_MARKER)
        if idx == -1:
            return description.rstrip()
        return description[:idx].rstrip()

    @classmethod
    def _build_judgment_description(cls, state: AgentState, lines: list[str]) -> str:
        base = cls._strip_previous_judgment(state.get("ticket_description", "") or "")
        block = "\n".join(f"- {line}" for line in lines)
        return f"{base}\n\n{_JUDGMENT_MARKER}\n{block}" if base else f"{_JUDGMENT_MARKER}\n{block}"
