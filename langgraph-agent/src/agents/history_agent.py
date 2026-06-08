"""Historical analysis agent for finding similar past issues.

Simplified 2-stage retrieval (reduced from 4 LLM calls to 2):
1. LLM extracts keywords from ticket (quality improvement)
2. JQL search with status-based ranking (resolved/done/closed first)
3. LLM analyzes resolutions from top matches (core value)

Removed: CommentScorer, RankedTicket, semantic re-ranking (785 → ~400 lines)
"""

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..config import create_extraction_llm, get_settings
from ..state import AgentState
from ..tools.jira_tools import JiraTools

logger = logging.getLogger(__name__)


# ============================================================================
# Structured Models
# ============================================================================

class SearchKeywords(BaseModel):
    """Structured output from LLM for keyword extraction"""
    error_patterns: list[str] = Field(
        default_factory=list,
        description="K8s error patterns like CrashLoopBackOff, OOMKilled, ImagePullBackOff"
    )
    components: list[str] = Field(
        default_factory=list,
        description="Service/pod/deployment names mentioned in the ticket"
    )
    symptoms: list[str] = Field(
        default_factory=list,
        description="Symptom keywords like 'not responding', 'timeout', 'connection refused'"
    )


class ScoredTicket(BaseModel):
    """A ticket with composite relevance score."""
    key: str
    summary: str = ""
    description: str = ""
    resolution: str = ""
    last_comment: str = ""
    updated: str = ""
    is_resolved: bool = False
    components: list[str] = Field(default_factory=list)

    # Score breakdown
    llm_similarity: float = Field(default=0.0, ge=0, le=100)
    component_match: float = Field(default=0.0, ge=0, le=100)
    status_score: float = Field(default=0.0, ge=0, le=100)
    recency_bonus: float = Field(default=0.0, ge=0, le=100)
    final_score: float = Field(default=0.0, ge=0, le=100)


# ============================================================================
# Scoring Functions (Pure, Deterministic)
# ============================================================================

# Status score mapping: Jira status position -> score (0-100)
STATUS_SCORE_MAP = {
    "open": 0,
    "to do": 25,
    "in progress": 50,
    "in review": 75,
    "resolved": 100,
    "done": 100,
    "closed": 100,
}


def parse_ticket_response(raw: Any) -> Any:
    """Parse a raw Go-server ticket response into a typed JiraTicketResponse.

    Handles two formats:
    1. dict with structured fields (future/preferred)
    2. str in Go server text format (current)

    Raises ValueError with a descriptive message if neither format matches,
    so callers get an explicit error instead of silent empty data.
    """
    from ..models.llm_outputs import JiraTicketResponse

    if isinstance(raw, dict):
        content = raw.get("content") or raw.get("raw")
        if isinstance(content, dict):
            return JiraTicketResponse(**content)
        if content is None:
            # Ensure is_resolved is included so validator runs
            if "is_resolved" not in raw:
                raw = {**raw, "is_resolved": False}
            return JiraTicketResponse(**raw)
        # Fall through to string parsing of content
        raw = content

    if not isinstance(raw, str):
        raise ValueError(f"Unsupported ticket response type: {type(raw)}")

    # Empty string is invalid
    if not raw or not raw.strip():
        raise ValueError("Empty ticket response")

    if raw.startswith("Error") or "No tickets found" in raw:
        raise ValueError(f"Go server returned error: {raw[:200]}")

    result: dict[str, Any] = {
        "key": "",
        "summary": "",
        "description": "",
        "status": "Unknown",
        "resolution": "",
        "last_comment": "",
        "updated": "",
        "is_resolved": False,
        "components": [],
    }

    summary_match = re.search(r'Summary:\s*(.+?)(?:\n|$)', raw)
    if summary_match:
        result["summary"] = summary_match.group(1).strip()

    status_match = re.search(r'Status:\s*(.+?)(?:\n|$)', raw)
    if status_match:
        result["status"] = status_match.group(1).strip()

    resolution_match = re.search(r'Resolution:\s*(.+?)(?:\n|$)', raw)
    if resolution_match:
        result["resolution"] = resolution_match.group(1).strip()

    comment_pattern = (
        r'--- Comment \d+ \(by ([^)]+) on ([^)]+)\) ---\n'
        r'([\s\S]*?)(?=\n?--- Comment|\n\*\*|\n?$)'
    )
    comment_matches = re.findall(comment_pattern, raw)
    if comment_matches:
        _, _, body = comment_matches[0]
        result["last_comment"] = body.strip()[:500]

    return JiraTicketResponse(**result)



def compute_status_score(status: str) -> float:
    """Map Jira status to score (0-100).

    Open = 0, To Do = 25, In Progress = 50, In Review = 75, Done = 100.
    """
    return STATUS_SCORE_MAP.get(status.lower().strip(), 0)


def compute_component_match(
    current_components: list[str],
    historical_components: list[str],
) -> float:
    """Binary component match: 100 if any overlap, 0 otherwise.

    Case-insensitive comparison.
    """
    if not current_components or not historical_components:
        return 0
    current_set = {c.lower().strip() for c in current_components}
    historical_set = {c.lower().strip() for c in historical_components}
    return 100 if current_set & historical_set else 0


def compute_recency_bonus(updated_date: str, max_days: int = 365) -> float:
    """Tiered recency score based on age cutoffs.

    Tiers:
    - <30 days  -> 100
    - <90 days  -> 75
    - <180 days -> 50
    - <max_days -> 25
    - >=max_days -> 0

    The max_days parameter controls the final cutoff (>=max_days returns 0).

    Parses Jira date formats:
    - "2026-01-15T10:30:00.000+0000"
    - "2026-01-15"
    """
    if not updated_date:
        return 0

    try:
        from datetime import datetime, timezone
        date_str = updated_date.split(".")[0]  # Remove milliseconds
        if "T" in date_str:
            dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        dt = dt.replace(tzinfo=timezone.utc)

        age_days = (datetime.now(timezone.utc) - dt).days
        if age_days < 0:
            return 100  # Future date = max recency
        if age_days >= max_days:
            return 0
        if age_days < 30:
            return 100
        if age_days < 90:
            return 75
        if age_days < 180:
            return 50
        return 25
    except (ValueError, TypeError):
        return 0


def compute_composite_score(
    llm_similarity: float,
    component_match: float,
    status_score: float,
    recency_bonus: float,
    w_llm: float | None = None,
    w_component: float | None = None,
    w_status: float | None = None,
    w_recency: float | None = None,
) -> float:
    """Compute weighted composite score (0-100).

    Weights default to config values (Settings class):
    llm=0.55, component=0.10, status=0.20, recency=0.15

    Formula: llm*w_llm + component*w_component + status*w_status + recency*w_recency
    """
    settings = get_settings()
    w_llm = w_llm if w_llm is not None else settings.history_weight_llm_similarity
    w_component = w_component if w_component is not None else settings.history_weight_component_match
    w_status = w_status if w_status is not None else settings.history_weight_status_score
    w_recency = w_recency if w_recency is not None else settings.history_weight_recency_bonus

    score = (
        llm_similarity * w_llm
        + component_match * w_component
        + status_score * w_status
        + recency_bonus * w_recency
    )
    return round(score, 1)


# ============================================================================
# JQL Query Builder
# ============================================================================

class JQLQueryBuilder:
    """Build JQL queries for historical ticket search"""

    # Known K8s error patterns
    K8S_ERROR_PATTERNS = {
        "CrashLoopBackOff", "ImagePullBackOff", "OOMKilled",
        "Evicted", "ContainerCreating", "ErrImagePull",
        "InvalidImageName", "PodInitializing", "RunContainerError",
        "CreateContainerConfigError", "CreateContainerError"
    }

    @staticmethod
    def escape_jql_string(value: str) -> str:
        """Escape special characters for JQL text search."""
        special_chars = ['\\', '"', "'"]
        result = value
        for char in special_chars:
            result = result.replace(char, f'\\{char}')
        return result

    @staticmethod
    def build_search_query(
        error_patterns: list[str] = None,
        components: list[str] = None
    ) -> str:
        """Build JQL query for searching similar tickets.

        Searches ALL statuses - client-side ranking prioritizes resolved tickets.
        """
        terms = []

        if error_patterns:
            for e in error_patterns[:3]:
                escaped = JQLQueryBuilder.escape_jql_string(e)
                terms.append(f'text ~ "{escaped}"')

        if components:
            for c in components[:3]:
                escaped = JQLQueryBuilder.escape_jql_string(c)
                terms.append(f'text ~ "{escaped}"')

        if not terms:
            return ""

        # Search all statuses, order by updated (client-side status ranking below)
        query = f"({' OR '.join(terms)}) ORDER BY updated DESC"
        return query


# ============================================================================
# History Agent with Simplified 2-Stage Retrieval
# ============================================================================

class HistoryAgent:
    """Searches for similar historical issues using simplified 2-stage retrieval.

    Flow:
    1. LLM extracts keywords from current ticket (quality improvement)
    2. JQL search with status-based ranking (resolved first, then recent)
    3. LLM analyzes resolutions from top matches (core value)

    Simplified from original 4 LLM calls to 2 by removing semantic re-ranking.
    """

    def __init__(self, jira_tools: JiraTools):
        self.llm = create_extraction_llm()
        self.tools = jira_tools
        self.settings = get_settings()

    async def run(self, state: AgentState) -> AgentState:
        """Search for similar past tickets using simplified 2-stage retrieval.

        Stage 1: LLM keyword extraction
        Stage 2: JQL search with status-based ranking + LLM resolution analysis
        """
        logger.info("Searching for similar historical issues (simplified 2-stage)")

        try:
            # Stage 1: Extract keywords using LLM (quality improvement over regex)
            keywords = await self._extract_search_keywords(state)
            logger.info(f"Extracted keywords: errors={keywords.error_patterns}, "
                       f"components={keywords.components}")

            if not keywords.error_patterns and not keywords.components:
                logger.warning("No searchable keywords extracted from ticket")
                state["similar_tickets"] = []
                state["past_resolutions"] = ["Insufficient information to search historical tickets"]
                return state

            # Stage 2: Composite scoring (LLM similarity + component + status + recency)
            tickets = await self._composite_ranked_search(keywords, state)
            logger.info(f"Found {len(tickets)} similar tickets (composite scored)")

            if not tickets:
                logger.info("No similar tickets found")
                state["similar_tickets"] = []
                state["past_resolutions"] = ["No similar resolved issues found in historical data"]
                return state

            # Stage 3: LLM analyzes resolutions (core value - extracting actionable insights)
            analysis = await self._analyze_resolutions(tickets, state)

            state["similar_tickets"] = tickets
            state["past_resolutions"] = analysis

            logger.info(f"Completed history search with {len(tickets)} relevant tickets")

        except Exception as e:
            logger.error(f"Error in history agent: {e}", exc_info=True)
            state["similar_tickets"] = []
            state["past_resolutions"] = [f"Historical search failed: {str(e)}"]

        return state

    async def _extract_search_keywords(self, state: AgentState) -> SearchKeywords:
        """Stage 1: LLM extracts keywords for JQL search."""
        prompt = f"""Extract K8s search keywords. JSON only.

Issue: {state.get("ticket_summary", "")}
Errors: {state.get("error_messages", [])}
Services: {state.get("affected_services", [])}
Deployments: {state.get("affected_deployments", [])}

Return: {{"error_patterns": ["CrashLoopBackOff","OOMKilled",...], "components": ["service-name",...], "symptoms": ["timeout",...]}}"""

        try:
            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            content = response.content.strip()

            # Handle markdown code blocks
            if content.startswith('```'):
                lines = content.split('\n')
                content = '\n'.join(lines[1:-1])

            data = json.loads(content)
            return SearchKeywords(
                error_patterns=data.get("error_patterns", []),
                components=data.get("components", []),
                symptoms=data.get("symptoms", [])
            )
        except Exception as e:
            logger.warning(f"Failed to extract keywords with LLM: {e}")
            return self._fallback_keyword_extraction(state)

    def _fallback_keyword_extraction(self, state: AgentState) -> SearchKeywords:
        """Fallback keyword extraction without LLM."""
        summary = state.get("ticket_summary", "").lower()

        error_patterns = [
            p for p in JQLQueryBuilder.K8S_ERROR_PATTERNS
            if p.lower() in summary
        ]

        components = []
        components.extend(state.get("affected_services", []))
        components.extend(state.get("affected_deployments", []))

        return SearchKeywords(
            error_patterns=error_patterns,
            components=components[:5],
            symptoms=[]
        )

    def _parse_tickets(self, response) -> list[dict[str, Any]]:
        """Parse Jira search response into ticket list.

        Supports formats:
        1. List (direct JSON)
        2. Go server: "- KEY [DATE] (STATUS): Summary"
        3. Legacy: "- KEY: Summary"
        """
        tickets = []

        # Handle wrapped response
        if isinstance(response, dict):
            response = response.get("content") or response.get("raw") or response

        if isinstance(response, list):
            return response
        elif isinstance(response, str):
            if "No tickets found" in response or response.startswith("Error"):
                return []

            # Parse Go server format
            for line in response.split('\n'):
                line = line.strip()
                if line.startswith('- ') and ': ' in line:
                    line_content = line[2:]  # Remove "- " prefix

                    # Try format with date, status, and optional components:
                    # KEY [DATE] (STATUS) {comp1,comp2}: Summary
                    match = re.match(
                        r'^([A-Z]+-\d+)\s+\[([^\]]+)\]\s+\(([^)]+)\)(?:\s+\{([^}]+)\})?\s*:\s*(.*)$',
                        line_content
                    )
                    if match:
                        ticket_key = match.group(1)
                        updated_date = match.group(2)
                        status = match.group(3)
                        components_str = match.group(4)  # May be None
                        summary = match.group(5)
                        is_resolved = status.lower() in ("resolved", "done", "closed")

                        # Parse components from comma-separated tag
                        components = []
                        if components_str:
                            components = [c.strip() for c in components_str.split(",") if c.strip()]

                        tickets.append({
                            'key': ticket_key,
                            'summary': summary,
                            'status': status,
                            'updated': updated_date,
                            'is_resolved': is_resolved,
                            'components': components,
                        })
                        continue

                    # Fallback: simple format KEY: Summary
                    key_summary = line_content.split(': ', 1)
                    if len(key_summary) == 2 and re.match(r'^[A-Z]+-\d+$', key_summary[0].strip()):
                        tickets.append({
                            'key': key_summary[0].strip(),
                            'summary': key_summary[1].strip(),
                            'status': 'Unknown',
                            'updated': '',
                            'is_resolved': False,
                            'components': [],
                        })

        return tickets

    async def _analyze_resolutions(
        self,
        similar_tickets: list[dict[str, Any]],
        state: AgentState
    ) -> list[str]:
        """Stage 3: LLM analyzes resolutions from similar tickets.

        This is the core value - extracting actionable insights from
        historical resolutions. Prioritizes resolved tickets (✓) with
        proven solutions over speculation.
        """
        if not similar_tickets:
            return ["No similar tickets to analyze"]

        # Format tickets with resolved indicator
        tickets_summary = "\n\n".join([
            f"{'✓' if t.get('is_resolved') else '○'} {t.get('key', 'Unknown')}:\n"
            f"  Title: {t.get('summary', 'No summary')}\n"
            f"  Resolution: {t.get('resolution', 'None')}\n"
            f"  Last comment: {t.get('last_comment', 'None')[:300]}"
            for t in similar_tickets[:5]
        ])

        prompt = f"""Extract actionable insights from similar incidents.

{tickets_summary}

Current: {state.get("ticket_summary", "")}

Prioritize insights from resolved tickets (✓) with proven solutions.
Provide 3-5 bullets: root cause, resolution steps, specific commands/fixes."""

        try:
            response = await self.llm.ainvoke([
                SystemMessage(content="You are analyzing historical K8s incident resolutions."),
                HumanMessage(content=prompt)
            ])

            insights = [
                line.strip()
                for line in response.content.split("\n")
                if line.strip() and (line.strip().startswith("-") or line.strip().startswith("*"))
            ]

            return insights if insights else [response.content]
        except Exception as e:
            logger.error(f"Resolution analysis failed: {e}")
            return ["Failed to analyze resolutions"]

    async def _composite_ranked_search(
        self,
        keywords: SearchKeywords,
        state: AgentState,
    ) -> list[dict[str, Any]]:
        """Search with composite scoring: LLM similarity + component + status + recency.

        Pipeline:
        1. JQL search to get raw candidates
        2. Filter out current ticket
        3. Trim to max_candidates_to_rerank
        4. LLM re-rank for similarity scores
        5. Compute composite scores per candidate
        6. Sort by final_score descending
        7. Fetch details for top N
        """
        query = JQLQueryBuilder.build_search_query(
            error_patterns=keywords.error_patterns,
            components=keywords.components,
        )

        if not query:
            return []

        logger.info(f"Executing JQL search: {query}")

        try:
            response = await self.tools.search_tickets(jql=query, limit=50)
            candidates = self._parse_tickets(response)

            # Filter out current ticket
            current_ticket_id = state.get("ticket_id", "")
            if current_ticket_id:
                candidates = [
                    t for t in candidates if t.get("key") != current_ticket_id
                ]

            if not candidates:
                return []

            # Trim to max candidates for LLM re-ranking
            max_candidates = self.settings.history_max_candidates_to_rerank
            candidates = candidates[:max_candidates]

            # LLM re-ranking for semantic similarity
            llm_scores = await self._llm_rerank_candidates(candidates, state)

            # Current ticket components for matching
            current_components = state.get("ticket_components", [])

            # Score each candidate using ScoredTicket model
            scored: list[ScoredTicket] = []
            for c in candidates:
                key = c.get("key", "")
                llm_sim = llm_scores.get(key, 50.0)
                comp_match = compute_component_match(
                    current_components, c.get("components", [])
                )
                status_sc = compute_status_score(c.get("status", ""))
                recency = compute_recency_bonus(
                    c.get("updated", ""),
                    max_days=self.settings.history_recency_max_days,
                )
                final = compute_composite_score(
                    llm_similarity=llm_sim,
                    component_match=comp_match,
                    status_score=status_sc,
                    recency_bonus=recency,
                    w_llm=self.settings.history_weight_llm_similarity,
                    w_component=self.settings.history_weight_component_match,
                    w_status=self.settings.history_weight_status_score,
                    w_recency=self.settings.history_weight_recency_bonus,
                )
                scored.append(ScoredTicket(
                    key=key,
                    summary=c.get("summary", ""),
                    updated=c.get("updated", ""),
                    is_resolved=c.get("is_resolved", False),
                    components=c.get("components", []),
                    llm_similarity=llm_sim,
                    component_match=comp_match,
                    status_score=status_sc,
                    recency_bonus=recency,
                    final_score=final,
                ))

            # Sort by final_score descending
            scored.sort(key=lambda t: t.final_score, reverse=True)

            # Filter by min relevance score
            min_score = self.settings.history_min_relevance_score
            scored = [t for t in scored if t.final_score >= min_score]

            # Fetch full details for top tickets
            max_fetch = self.settings.history_max_tickets_to_fetch
            top_tickets = []

            for ticket in scored[:max_fetch]:
                details = await self._fetch_ticket_details(ticket.key)

                top_tickets.append({
                    "key": ticket.key,
                    "summary": details.get("summary") or ticket.summary,
                    "description": details.get("description", ""),
                    "resolution": details.get("resolution", ""),
                    "last_comment": details.get("last_comment", ""),
                    "updated": ticket.updated,
                    "is_resolved": ticket.is_resolved,
                    "components": ticket.components,
                    "llm_similarity": ticket.llm_similarity,
                    "component_match": ticket.component_match,
                    "status_score": ticket.status_score,
                    "recency_bonus": ticket.recency_bonus,
                    "final_score": ticket.final_score,
                })

            logger.info(f"Composite scoring: {len(top_tickets)} tickets above threshold")
            # Note: at this point, top_tickets are dicts, so use dict access for logging
            for t in top_tickets[:5]:
                logger.info(
                    f"  {t['key']}: final={t['final_score']:.1f} "
                    f"(llm={t['llm_similarity']:.0f}, comp={t['component_match']:.0f}, "
                    f"status={t['status_score']:.0f}, recency={t['recency_bonus']:.0f})"
                )

            return top_tickets

        except Exception as e:
            logger.error(f"Composite ranked search failed: {e}")
            return []

    async def _llm_rerank_candidates(
        self,
        candidates: list[dict[str, Any]],
        state: AgentState,
    ) -> dict[str, float]:
        """Score candidates by semantic similarity to current ticket using LLM.

        One lightweight LLM call to score top candidates.

        Args:
            candidates: List of ticket dicts with key, summary, status.
            state: Current agent state with ticket_summary.

        Returns:
            Dict mapping ticket key -> similarity score (0-100).
            On failure, returns default score of 50 for all candidates.
        """
        default_score = 50
        keys = [c.get("key", "") for c in candidates]
        defaults = {k: default_score for k in keys}

        if not candidates:
            return {}

        # Build compact candidate list for prompt
        candidate_lines = []
        for c in candidates:
            candidate_lines.append(
                f"- {c.get('key')}: {c.get('summary', '')[:100]}"
            )
        candidates_text = "\n".join(candidate_lines)

        current_summary = state.get("ticket_summary", "")

        prompt = (
            "Score each candidate ticket by semantic similarity to the current issue.\n"
            "Return JSON only. Score 0-100 (100 = identical issue, 0 = completely unrelated).\n\n"
            f"Current issue: {current_summary[:300]}\n\n"
            f"Candidates:\n{candidates_text}\n\n"
            'Return: {"scores": [{"key": "TICKET-1", "similarity": 85}, ...]}'
        )

        try:
            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            content = response.content.strip()

            # Standard JSON parsing: strip markdown, extract braces
            if content.startswith('```'):
                lines = content.split('\n')
                content = '\n'.join(lines[1:-1])

            brace_match = re.search(r'\{[\s\S]*\}', content)
            if brace_match:
                content = brace_match.group(0)

            data = json.loads(content)
            scores_list = data.get("scores", [])

            result = {}
            for entry in scores_list:
                key = entry.get("key", "")
                score = entry.get("similarity", default_score)
                # Clamp to 0-100
                score = max(0, min(100, float(score)))
                result[key] = score

            # Fill in missing keys with default
            for k in keys:
                if k not in result:
                    result[k] = default_score

            return result

        except Exception as e:
            logger.warning(f"LLM re-ranking failed: {e}")
            return defaults

    async def _fetch_ticket_details(self, ticket_key: str) -> dict[str, Any]:
        """Fetch full ticket details including resolution and comments.

        Uses parse_ticket_response() helper for robust parsing with validation.
        Returns dict format for backward compatibility with callers.
        """
        try:
            response = await self.tools.get_ticket(ticket_key)
            ticket = parse_ticket_response(response)
            
            # Convert Pydantic model to dict for backward compatibility
            return {
                "summary": ticket.summary,
                "description": ticket.description,
                "resolution": ticket.resolution,
                "last_comment": ticket.last_comment
            }

        except ValueError as e:
            logger.warning(f"Failed to parse ticket {ticket_key}: {e}")
            return {
                "summary": "", "description": "",
                "resolution": "", "last_comment": ""
            }
        except Exception as e:
            logger.warning(f"Failed to fetch ticket {ticket_key}: {e}")
            return {
                "summary": "", "description": "",
                "resolution": "", "last_comment": ""
            }
