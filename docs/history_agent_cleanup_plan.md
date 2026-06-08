# History Agent Cleanup Plan

Execution plan for fixing 5 issues in `langgraph-agent/src/agents/history_agent.py`.
Optimized for Claude Haiku: each step is a single, focused, self-contained instruction.

---

## Issue 1 — Dead code: `_status_ranked_search` (line 348)

**Problem:** `_status_ranked_search` is defined but never called. `run()` calls
`_composite_ranked_search` exclusively. Keeping it causes confusion about which
path is active and inflates the file by ~75 lines.

**Decision:** Delete it. `_composite_ranked_search` is strictly superior (adds
LLM re-ranking on top of the same status-sorting logic).

### Step 1.1 — Delete the dead method

```
In langgraph-agent/src/agents/history_agent.py, delete the entire
_status_ranked_search method (lines 348–424, inclusive).

The method starts at:
    async def _status_ranked_search(

and ends just before:
    def _parse_tickets(

Verify the file still parses: python -c "import ast; ast.parse(open('langgraph-agent/src/agents/history_agent.py').read()); print('OK')"
```

### Step 1.2 — Verify no callers

```
Run: rg "_status_ranked_search" langgraph-agent/ tests/
Expected: zero matches. If any exist, update the caller to use
_composite_ranked_search instead, then delete.
```

---

## Issue 2 — Unused import `ChatOpenAI` (line 17)

**Problem:** `from langchain_openai import ChatOpenAI` is imported only so tests
can do `patch("src.agents.history_agent.ChatOpenAI", ...)`. The correct pattern
is to patch `create_extraction_llm` at its source, not import an unused symbol.

### Step 2.1 — Remove the import

```
In langgraph-agent/src/agents/history_agent.py, delete line 17:
    from langchain_openai import ChatOpenAI  # For test patching
```

### Step 2.2 — Find and fix any tests using the old patch target

```
Run: rg "ChatOpenAI" tests/
For each match that patches "history_agent.ChatOpenAI", change the patch target to:
    "src.agents.history_agent.create_extraction_llm"
and make the mock return a Mock LLM instance instead.

Example before:
    with patch("src.agents.history_agent.ChatOpenAI") as mock_cls:
        mock_cls.return_value = mock_llm

Example after:
    with patch("src.agents.history_agent.create_extraction_llm") as mock_fn:
        mock_fn.return_value = mock_llm
```

### Step 2.3 — Run affected tests

```
pytest tests/unit/langgraph_agent/ -k "history" -x
All tests must pass.
```

---

## Issue 3 — Mutable dict mutation in scoring loop (lines 618–622)

**Problem:** In `_composite_ranked_search`, score fields are stamped directly
onto the candidate dicts from the search results:

    c["llm_similarity"] = llm_sim
    c["component_match"] = comp_match
    ...

This mutates shared dicts in place. The `ScoredTicket` Pydantic model is
defined in the same file but never used. Use it.

### Step 3.1 — Use `ScoredTicket` in the scoring loop

```
In _composite_ranked_search, replace the in-place mutation block (roughly lines
595–623) with construction of a ScoredTicket per candidate.

Replace:
    scored = []
    for c in candidates:
        key = c.get("key", "")
        llm_sim = llm_scores.get(key, 50)
        comp_match = compute_component_match(...)
        status_sc = compute_status_score(...)
        recency = compute_recency_bonus(...)
        final = compute_composite_score(...)

        c["llm_similarity"] = llm_sim      # <-- mutates shared dict
        c["component_match"] = comp_match
        c["status_score"] = status_sc
        c["recency_bonus"] = recency
        c["final_score"] = final
        scored.append(c)

With:
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
```

### Step 3.2 — Update sort and filter to use typed attribute access

```
Replace:
    scored.sort(key=lambda t: t["final_score"], reverse=True)
    scored = [t for t in scored if t["final_score"] >= min_score]

With:
    scored.sort(key=lambda t: t.final_score, reverse=True)
    scored = [t for t in scored if t.final_score >= min_score]
```

### Step 3.3 — Update the top_tickets construction to read from ScoredTicket

```
The loop that builds top_tickets currently reads from dict keys.
Change it to read from ScoredTicket attributes:

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
```

### Step 3.4 — Update the debug logging block

```
The logger.info loop after building top_tickets reads dict keys.
Update it to use attribute access:

    for t in top_tickets[:5]:
        logger.info(
            f"  {t['key']}: final={t['final_score']:.1f} "
            f"(llm={t['llm_similarity']:.0f}, comp={t['component_match']:.0f}, "
            f"status={t['status_score']:.0f}, recency={t['recency_bonus']:.0f})"
        )
```

### Step 3.5 — Run tests

```
pytest tests/unit/langgraph_agent/test_composite_scoring.py
pytest tests/unit/langgraph_agent/test_composite_pipeline.py
All must pass.
```

---

## Issue 4 — Fragile regex parsing in `_parse_tickets` and `_fetch_ticket_details`

**Problem:** Both methods parse unstructured text from the Go server using
`re.search` / `re.findall`. A minor Go-side formatting change silently produces
empty dicts with no error. The fix is a typed Pydantic response model shared
between the Go response and the Python parser.

### Step 4.1 — Add `JiraTicketResponse` Pydantic model to `models/llm_outputs.py`

```
Append to langgraph-agent/src/models/llm_outputs.py:

class JiraTicketResponse(BaseModel):
    """Structured ticket data from the Go Jira MCP server.

    Used in _fetch_ticket_details and _parse_tickets to replace
    brittle regex parsing with validated structured data.
    """
    key: str = Field(default="", description="Jira ticket key, e.g. SP-123")
    summary: str = Field(default="", description="Ticket title/summary")
    description: str = Field(default="", description="Full ticket description")
    status: str = Field(default="Unknown", description="Jira workflow status")
    resolution: str = Field(default="", description="Resolution text if resolved")
    last_comment: str = Field(default="", description="Most recent comment body")
    updated: str = Field(default="", description="ISO-8601 last-updated timestamp")
    is_resolved: bool = Field(default=False, description="True if status is Done/Resolved/Closed")
    components: list[str] = Field(default_factory=list, description="Jira component names")

    @field_validator("is_resolved", mode="before")
    @classmethod
    def infer_is_resolved(cls, v: Any, info: Any) -> bool:
        if isinstance(v, bool):
            return v
        # Infer from status if not explicitly set
        status = (info.data or {}).get("status", "")
        return str(status).lower() in ("resolved", "done", "closed")
```

### Step 4.2 — Add `parse_ticket_response` helper to `history_agent.py`

```
Add a module-level function below the ScoredTicket model in history_agent.py:

def parse_ticket_response(raw: Any) -> JiraTicketResponse:
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
            return JiraTicketResponse(**raw)
        # Fall through to string parsing of content
        raw = content

    if not isinstance(raw, str):
        raise ValueError(f"Unsupported ticket response type: {type(raw)}")

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
```

### Step 4.3 — Rewrite `_fetch_ticket_details` to use `parse_ticket_response`

```
Replace the body of _fetch_ticket_details with:

    async def _fetch_ticket_details(self, ticket_key: str) -> dict[str, Any]:
        try:
            response = await self.tools.get_ticket(ticket_key)
            ticket = parse_ticket_response(response)
            return {
                "summary": ticket.summary,
                "description": ticket.description,
                "resolution": ticket.resolution,
                "last_comment": ticket.last_comment,
            }
        except ValueError as e:
            logger.warning(f"Failed to parse ticket {ticket_key}: {e}")
            return {"summary": "", "description": "", "resolution": "", "last_comment": ""}
        except Exception as e:
            logger.warning(f"Failed to fetch ticket {ticket_key}: {e}")
            return {"summary": "", "description": "", "resolution": "", "last_comment": ""}
```

### Step 4.4 — Write a unit test for `parse_ticket_response`

```
Create tests/unit/langgraph_agent/test_history_agent_response_parsing.py:

"""Tests for parse_ticket_response helper in history_agent."""
import pytest
from src.agents.history_agent import parse_ticket_response


class TestParseTicketResponse:

    def test_parses_go_text_format(self):
        raw = (
            "Summary: order-service CrashLoopBackOff\n"
            "Resolution: Restarted deployment\n"
            "--- Comment 1 (by alice on 2026-01-15) ---\n"
            "Fixed by increasing memory limits\n"
            "**\n"
        )
        ticket = parse_ticket_response(raw)
        assert ticket.summary == "order-service CrashLoopBackOff"
        assert ticket.resolution == "Restarted deployment"
        assert "memory limits" in ticket.last_comment

    def test_parses_dict_format(self):
        raw = {"summary": "pod OOMKilled", "status": "Done", "resolution": "scaled up"}
        ticket = parse_ticket_response(raw)
        assert ticket.summary == "pod OOMKilled"
        assert ticket.is_resolved is True

    def test_go_error_response_raises(self):
        with pytest.raises(ValueError, match="Go server returned error"):
            parse_ticket_response("Error: Jira request failed")

    def test_no_tickets_found_raises(self):
        with pytest.raises(ValueError, match="Go server returned error"):
            parse_ticket_response("No tickets found matching query")

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported ticket response type"):
            parse_ticket_response(12345)

    def test_empty_string_returns_empty_model(self):
        ticket = parse_ticket_response("")
        # Empty string is valid text; regex finds nothing -> empty fields
        assert ticket.summary == ""
        assert ticket.resolution == ""
```

### Step 4.5 — Run parsing tests

```
pytest tests/unit/langgraph_agent/test_history_agent_response_parsing.py -v
pytest tests/unit/langgraph_agent/test_history_agent_parsing.py -v
All must pass.
```

---

## Issue 5 — Missing tests for critical paths

**Context check:** `tests/unit/langgraph_agent/test_composite_scoring.py` already
covers `compute_status_score`, `compute_component_match`, `compute_recency_bonus`,
and `compute_composite_score` comprehensively. `test_llm_reranking.py` covers
the happy path and failure modes of `_llm_rerank_candidates`.

**Gaps to fill:**
- Markdown-wrapped JSON in `_llm_rerank_candidates` (e.g. ` ```json\n...\n``` `)
- Markdown-wrapped JSON in `_extract_search_keywords`
- `_extract_search_keywords` fallback path

### Step 5.1 — Add markdown JSON edge-case tests to `test_llm_reranking.py`

```
Append to tests/unit/langgraph_agent/test_llm_reranking.py:

    @pytest.mark.asyncio
    async def test_rerank_markdown_wrapped_json(self, history_agent, sample_candidates, sample_state):
        """LLM sometimes wraps JSON in ```json ... ``` code fences."""
        wrapped = '```json\n{"scores": [{"key": "SP-100", "similarity": 80}]}\n```'
        mock_response = MagicMock()
        mock_response.content = wrapped
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_response)

        scores = await history_agent._llm_rerank_candidates(sample_candidates, sample_state)

        assert scores["SP-100"] == 80
        # Missing keys default to 50
        assert scores["SP-101"] == 50
        assert scores["SP-102"] == 50

    @pytest.mark.asyncio
    async def test_rerank_scores_list_not_in_response(self, history_agent, sample_candidates, sample_state):
        """JSON with wrong shape (no 'scores' key) should fall back to defaults."""
        bad_json = '{"result": []}'
        mock_response = MagicMock()
        mock_response.content = bad_json
        history_agent.llm = MagicMock()
        history_agent.llm.ainvoke = AsyncMock(return_value=mock_response)

        scores = await history_agent._llm_rerank_candidates(sample_candidates, sample_state)
        assert all(v == 50 for v in scores.values())
```

### Step 5.2 — Add tests for `_extract_search_keywords`

```
Create tests/unit/langgraph_agent/test_history_agent_keyword_extraction.py:

"""Tests for _extract_search_keywords in HistoryAgent."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, Mock


class TestExtractSearchKeywords:

    @pytest.fixture
    def agent(self):
        import sys
        sys.path.insert(0, 'langgraph-agent')
        from src.agents.history_agent import HistoryAgent
        return HistoryAgent(Mock())

    @pytest.fixture
    def state(self):
        return {
            "ticket_summary": "order-service CrashLoopBackOff in production",
            "error_messages": ["CrashLoopBackOff"],
            "affected_services": ["order-service"],
            "affected_deployments": [],
        }

    @pytest.mark.asyncio
    async def test_parses_llm_json_response(self, agent, state):
        payload = {"error_patterns": ["CrashLoopBackOff"], "components": ["order-service"], "symptoms": []}
        mock_resp = MagicMock()
        mock_resp.content = json.dumps(payload)
        agent.llm = MagicMock()
        agent.llm.ainvoke = AsyncMock(return_value=mock_resp)

        result = await agent._extract_search_keywords(state)
        assert result.error_patterns == ["CrashLoopBackOff"]
        assert result.components == ["order-service"]

    @pytest.mark.asyncio
    async def test_strips_markdown_code_fence(self, agent, state):
        payload = {"error_patterns": ["OOMKilled"], "components": [], "symptoms": ["restart"]}
        mock_resp = MagicMock()
        mock_resp.content = f"```json\n{json.dumps(payload)}\n```"
        agent.llm = MagicMock()
        agent.llm.ainvoke = AsyncMock(return_value=mock_resp)

        result = await agent._extract_search_keywords(state)
        assert result.error_patterns == ["OOMKilled"]

    @pytest.mark.asyncio
    async def test_falls_back_on_llm_failure(self, agent, state):
        agent.llm = MagicMock()
        agent.llm.ainvoke = AsyncMock(side_effect=Exception("timeout"))

        result = await agent._extract_search_keywords(state)
        # Fallback finds "CrashLoopBackOff" in summary
        assert "CrashLoopBackOff" in result.error_patterns

    @pytest.mark.asyncio
    async def test_fallback_uses_state_services(self, agent, state):
        agent.llm = MagicMock()
        agent.llm.ainvoke = AsyncMock(side_effect=Exception("error"))

        result = await agent._extract_search_keywords(state)
        assert "order-service" in result.components
```

### Step 5.3 — Run all new and existing history-agent tests

```
pytest tests/unit/langgraph_agent/test_llm_reranking.py \
       tests/unit/langgraph_agent/test_composite_scoring.py \
       tests/unit/langgraph_agent/test_history_agent_keyword_extraction.py \
       tests/unit/langgraph_agent/test_history_agent_response_parsing.py \
       tests/unit/langgraph_agent/test_history_agent_parsing.py \
       -v

All tests must pass with exit code 0.
```

---

## Final validation

```
# 1. No syntax errors
python -c "import ast; ast.parse(open('langgraph-agent/src/agents/history_agent.py').read()); print('syntax OK')"

# 2. No dead symbol remains
rg "_status_ranked_search|ChatOpenAI" langgraph-agent/src/agents/history_agent.py
# Expected: zero matches

# 3. Full unit suite
pytest tests/unit/ -x -q
# Expected: all green
```
