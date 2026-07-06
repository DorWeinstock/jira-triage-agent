"""HTTP server for the triage LangGraph agent."""

import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from .supervisor import get_default_graph
from .tools.jira_tools import JiraTools
from .config import create_langfuse_handler, get_settings

logger = logging.getLogger(__name__)

RATE_LIMIT = os.getenv("TRIAGE_RATE_LIMIT", "20/minute")
limiter = Limiter(key_func=get_remote_address)

_TICKET_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+-\d+$")


def _validate_ticket_id(ticket_id: str) -> None:
    if not _TICKET_ID_RE.match(ticket_id):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid ticket_id '{ticket_id}'. Expected format: PROJECT-123",
        )


# Global state initialised at startup
_jira_tools: JiraTools | None = None
_graph = None
_langfuse_handler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _jira_tools, _graph, _langfuse_handler

    settings = get_settings()
    jira_mcp_url = os.getenv("JIRA_MCP_ENDPOINT", settings.jira_mcp_endpoint)

    _jira_tools = JiraTools(mcp_endpoint=jira_mcp_url)
    _graph = get_default_graph(_jira_tools)
    _langfuse_handler = create_langfuse_handler()

    logger.info("Triage agent started. Jira MCP: %s", jira_mcp_url)

    yield

    if _jira_tools is not None:
        await _jira_tools.close()
    logger.info("Triage agent stopped")


app = FastAPI(title="Jira Triage Agent", lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    raise HTTPException(status_code=429, detail="Rate limit exceeded. Please retry later.")


@app.get("/health")
async def health():
    return {"status": "ok"}


class TriageRequest(BaseModel):
    ticket_id: str


class TriageResponse(BaseModel):
    status: str
    ticket_id: str
    is_spam: bool | None = None
    assigned_to: str | None = None
    error: str | None = None


@app.post("/triage", response_model=TriageResponse)
@limiter.limit(RATE_LIMIT)
async def triage(request: Request, body: TriageRequest):
    """Triage a Jira ticket: evaluate spam, then route to team member or back to reporter."""
    _validate_ticket_id(body.ticket_id)

    logger.info("Triaging ticket %s", body.ticket_id)

    try:
        config = {"callbacks": [_langfuse_handler]} if _langfuse_handler else {}
        result = await _graph.ainvoke(
            {"ticket_id": body.ticket_id, "messages": []},
            config=config,
        )

        if result.get("error"):
            return TriageResponse(
                status="failed",
                ticket_id=body.ticket_id,
                error=result["error"],
            )

        return TriageResponse(
            status="completed",
            ticket_id=body.ticket_id,
            is_spam=result.get("is_spam"),
            assigned_to=result.get("assigned_to"),
        )

    except Exception as exc:
        logger.exception("Triage failed for %s", body.ticket_id)
        return TriageResponse(status="failed", ticket_id=body.ticket_id, error=str(exc))
