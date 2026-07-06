"""Triage supervisor — simple linear workflow: read → evaluate → route."""

import logging
from typing import Any

from langgraph.graph import StateGraph, END

from .state import AgentState
from .agents.spam_evaluator import SpamEvaluator
from .agents.ticket_router import TicketRouter
from .tools.jira_tools import JiraTools
from .config import get_settings

logger = logging.getLogger(__name__)


def create_triage_graph(jira_tools: JiraTools) -> Any:
    """Build and compile the triage StateGraph.

    Workflow nodes:
        initialize  → Validate state, set defaults
        read_ticket → Fetch ticket from Jira via MCP
        evaluate    → LLM decides spam vs valid
        route       → Reassign/comment/label via Jira MCP
        END
    """
    settings = get_settings()
    evaluator = SpamEvaluator()
    router = TicketRouter(
        jira_tools=jira_tools,
        team_members=settings.team_members,
        processed_label=settings.processed_label,
        in_progress_label=settings.in_progress_label,
        verdict_valid_label=settings.verdict_valid_label,
        verdict_invalid_label=settings.verdict_invalid_label,
    )

    # Shared round-robin counter (single server process, good enough for our scale)
    _rr_counter = {"index": 0}

    # -------------------------------------------------------------------------
    # Node: initialize
    # -------------------------------------------------------------------------
    def initialize(state: AgentState) -> dict:
        if not state.get("ticket_id"):
            return {"error": "No ticket_id provided"}
        return {}

    # -------------------------------------------------------------------------
    # Node: read_ticket
    # -------------------------------------------------------------------------
    async def read_ticket(state: AgentState) -> dict:
        ticket_id = state.get("ticket_id", "")
        if state.get("error"):
            return {}

        logger.info("Reading ticket %s", ticket_id)
        try:
            result = await jira_tools.get_ticket(ticket_id)
            content = result.get("content", "")
        except Exception as exc:
            logger.error("Failed to read ticket %s: %s", ticket_id, exc)
            return {"error": f"Failed to read ticket: {exc}"}

        # Parse the formatted output from the MCP server
        updates: dict = {}
        for line in content.splitlines():
            if line.startswith("Key:"):
                pass
            elif line.startswith("Summary:"):
                updates["ticket_summary"] = line.removeprefix("Summary:").strip()

        # Description spans multiple lines — grab everything after "Description:"
        if "Description:" in content:
            desc_start = content.index("Description:") + len("Description:")
            # Stop at the next field header or comments section
            desc_end = len(content)
            for marker in ["\nResolution:", "\nComponents:", "\n**Comments**"]:
                idx = content.find(marker, desc_start)
                if idx != -1 and idx < desc_end:
                    desc_end = idx
            updates["ticket_description"] = content[desc_start:desc_end].strip()

        # Fetch reporter/assignee from a separate get_ticket call that returns raw JSON
        # The MCP server returns formatted text; we re-fetch with the raw Jira API via
        # a dedicated search to get the reporter field.
        try:
            reporter_result = await jira_tools.get_reporter(ticket_id)
            if reporter_result:
                updates["ticket_reporter"] = reporter_result
        except Exception:
            pass  # non-fatal — reporter defaults to None

        logger.info(
            "Read %s: summary=%r reporter=%s",
            ticket_id,
            updates.get("ticket_summary", "")[:60],
            updates.get("ticket_reporter"),
        )
        return updates

    # -------------------------------------------------------------------------
    # Node: evaluate
    # -------------------------------------------------------------------------
    async def evaluate(state: AgentState) -> dict:
        if state.get("error"):
            return {}
        return await evaluator.run(state)

    # -------------------------------------------------------------------------
    # Node: route
    # -------------------------------------------------------------------------
    async def route(state: AgentState) -> dict:
        if state.get("error"):
            return {"triage_complete": True}
        idx = _rr_counter["index"]
        _rr_counter["index"] += 1
        return await router.run(state, rr_index=idx)

    # -------------------------------------------------------------------------
    # Build graph
    # -------------------------------------------------------------------------
    workflow = StateGraph(AgentState)

    workflow.add_node("initialize", initialize)
    workflow.add_node("read_ticket", read_ticket)
    workflow.add_node("evaluate", evaluate)
    workflow.add_node("route", route)

    workflow.set_entry_point("initialize")
    workflow.add_edge("initialize", "read_ticket")
    workflow.add_edge("read_ticket", "evaluate")
    workflow.add_edge("evaluate", "route")
    workflow.add_edge("route", END)

    logger.info("Triage graph compiled (4 nodes: initialize→read_ticket→evaluate→route)")
    return workflow.compile()


def get_default_graph(jira_tools: JiraTools) -> Any:
    return create_triage_graph(jira_tools)
