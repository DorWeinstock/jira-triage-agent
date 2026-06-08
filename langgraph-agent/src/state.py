"""Shared state definition for the triage multi-agent system."""

from typing import Optional

from pydantic import Field
from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """State that flows between agents in the triage workflow.

    Lifecycle:
        initialize -> read_ticket -> evaluate_spam -> route_ticket -> END

    After route_ticket completes, the ticket is either:
    - Reassigned to the original reporter with an explanatory comment (spam)
    - Assigned to a team member via round-robin (valid)
    In both cases the triage-agent-done label is stamped on the ticket.
    """

    # Jira context
    ticket_id: Optional[str] = None
    ticket_summary: Optional[str] = None
    ticket_description: Optional[str] = None
    ticket_labels: list[str] = Field(default_factory=list)
    ticket_reporter: Optional[str] = None   # Jira username of original reporter
    ticket_assignee: Optional[str] = None   # Current assignee username

    # Spam evaluation
    is_spam: Optional[bool] = None
    spam_reason: Optional[str] = None
    jenkins_link_found: bool = False
    server_name_found: bool = False
    issue_scope: Optional[str] = None  # "k8s" | "hardware" | "firmware" | "jenkins" | "it" | "kernel" | "other"

    # Routing
    triage_comment: Optional[str] = None   # LLM-written comment (spam tickets only)
    assigned_to: Optional[str] = None      # Team member for valid tickets
    triage_complete: bool = False

    # Error tracking
    error: Optional[str] = None
