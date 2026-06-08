# JiraAgent Cleanup Plan

> Optimized for Claude Haiku: each step is small, self-contained, and independently verifiable.
> File: `langgraph-agent/src/agents/jira_agent.py`

---

## Fix 1 — Remove unused `ChatOpenAI` import and dead comment (lines 11, 34)

**Why:** Production code must not import symbols purely for test patching. Tests should use
`unittest.mock.patch("langgraph_agent.src.agents.jira_agent.ChatOpenAI")` on the actual symbol path.

**Steps:**
1. Delete line 11: `from langchain_openai import ChatOpenAI  # For test patching`
2. Verify no other reference to `ChatOpenAI` exists in this file (`rg 'ChatOpenAI' langgraph-agent/src/agents/jira_agent.py`).
3. Search any test that imports or patches `ChatOpenAI` via this module and update its patch target to
   `langgraph_agent.src.config.ChatOpenAI` (which is where `create_llm` actually imports it).
4. Run `pytest tests/unit/` to confirm no import errors.

**Haiku prompt:**
> "In jira_agent.py remove the line `from langchain_openai import ChatOpenAI  # For test patching`
> and its associated comment. Do not change any other lines."

---

## Fix 2 — Remove in-method duplicate `import json` / `import re` (lines 164, 473–474)

**Why:** Both `json` and `re` are already imported at the top of the file (lines 7–8). Repeating them
inside methods is misleading (implies they are not available at module scope) and wastes a dict lookup
on every call.

**Steps:**
1. In `_parse_ticket_response` (around line 164), delete the line `import json`.
2. In `_parse_json_response` (around lines 473–474), delete both `import json` and `import re`.
3. Confirm the file still has `import json` and `import re` at the top level.
4. Run `pytest tests/unit/` and `pytest tests/integration/` to confirm no regressions.

**Haiku prompt:**
> "In jira_agent.py, delete the `import json` inside `_parse_ticket_response` and the `import json`
> and `import re` lines inside `_parse_json_response`. Keep all other code identical."

---

## Fix 3 — Replace hard-coded cluster endpoint and cluster names with settings (line 374)

**Why:** Hard-coded hostnames and cluster identifiers break portability, make unit testing require
network access, and require a code change for every environment change.

**Context:** `config.py` already has `k8s_mcp_endpoint` (default `http://jira-agent.default.svc.cluster.local:8080/mcp/k8s`).

**Steps:**

### 3a — Add cluster list to `Settings` in `config.py`
Add two new fields after the existing MCP endpoint fields:
```python
k8s_clusters: list[str] = Field(
    default=["hldc02", "hldc03"],
    alias="K8S_CLUSTERS"
)
k8s_cluster_mcp_base_url: str = Field(
    default="http://jira-agent:8080/mcp/k8s",
    alias="K8S_CLUSTER_MCP_BASE_URL"
)
```

### 3b — Update `_discover_cluster_from_resources` in `jira_agent.py`
Replace:
```python
for cluster in ["hldc02", "hldc03"]:
    endpoint = f"http://jira-agent:8080/mcp/k8s/{cluster}"
```
With:
```python
settings = get_settings()
for cluster in settings.k8s_clusters:
    endpoint = f"{settings.k8s_cluster_mcp_base_url}/{cluster}"
```

### 3c — Update `_detect_cluster_from_keywords` (lines 334, 341)
Replace the hard-coded pattern lists with constants derived from settings cluster names, or at minimum
move the pattern strings to `constants.py` so they are defined in one place:
```python
# In constants.py
CLUSTER_KEYWORD_PATTERNS: dict[str, list[str]] = {
    "hldc02": [r'\bhls2\b', r'\bg2\b', r'\bhldc02\b'],
    "hldc03": [r'\bg3\b', r'\bhldc03\b'],
}
```
Then in `_detect_cluster_from_keywords`:
```python
from ..constants import CLUSTER_KEYWORD_PATTERNS
for cluster, patterns in CLUSTER_KEYWORD_PATTERNS.items():
    for pattern in patterns:
        if re.search(pattern, combined_text, re.IGNORECASE):
            return cluster
```
This makes adding a third cluster a one-line change in `constants.py`.

### 3d — Test
- Add a unit test that sets `K8S_CLUSTERS=["test-cluster"]` and `K8S_CLUSTER_MCP_BASE_URL=http://mock:9999/mcp/k8s`
  and verifies the endpoint constructed is `http://mock:9999/mcp/k8s/test-cluster`.
- Run `pytest tests/unit/`.

**Haiku prompt (3a):**
> "In config.py, inside the Settings class after the `go_agent_url` field, add two new fields:
> `k8s_clusters` (list[str], default ['hldc02','hldc03'], alias 'K8S_CLUSTERS') and
> `k8s_cluster_mcp_base_url` (str, default 'http://jira-agent:8080/mcp/k8s', alias 'K8S_CLUSTER_MCP_BASE_URL').
> Do not change any other lines."

**Haiku prompt (3b):**
> "In `_discover_cluster_from_resources` in jira_agent.py, replace the hard-coded list
> `['hldc02', 'hldc03']` and the f-string `f'http://jira-agent:8080/mcp/k8s/{cluster}'`
> with `settings.k8s_clusters` and `f'{settings.k8s_cluster_mcp_base_url}/{cluster}'`
> using `get_settings()`. Add `settings = get_settings()` before the for loop."

---

## Fix 4 — Structured logging for silent LLM/parse error swallowing (lines 450–465)

**Why:** Bare `except Exception` that only logs a warning makes it impossible to alert on or
measure LLM failure rates. At minimum, log the exception *type* and a counter-friendly key.

**Steps:**

### 4a — Inner `except` (parse error, ~line 450)
Replace:
```python
except Exception as parse_error:
    logger.warning(
        f"Pydantic structured output failed: {parse_error}, using raw ticket"
    )
```
With:
```python
except Exception as parse_error:
    logger.warning(
        "[%s] LLM parse failed",
        AGENT_NAME,
        extra={
            "error_type": type(parse_error).__name__,
            "error_event": "llm_parse_failure",
            "ticket_id": state.get("ticket_id"),
        },
        exc_info=True,
    )
```

### 4b — Outer `except` (LLM error, ~line 459)
Replace:
```python
except Exception as llm_error:
    logger.warning(f"LLM parsing failed, using raw ticket data: {llm_error}")
```
With:
```python
except Exception as llm_error:
    logger.warning(
        "[%s] LLM invocation failed",
        AGENT_NAME,
        extra={
            "error_type": type(llm_error).__name__,
            "error_event": "llm_invocation_failure",
            "ticket_id": state.get("ticket_id"),
        },
        exc_info=True,
    )
```

### 4c — Test
- Add a unit test that mocks `self.llm.ainvoke` to raise `RuntimeError("timeout")` and asserts
  that the logger emits a record with `error_event == "llm_invocation_failure"`.
- Run `pytest tests/unit/`.

**Haiku prompt:**
> "In `_extract_resources_with_llm` in jira_agent.py, replace both `except Exception` warning log
> calls with structured `logger.warning(...)` calls that include `extra={'error_type': ..., 'error_event': ..., 'ticket_id': ...}`
> and `exc_info=True`. Keep all fallback logic unchanged."

---

## Fix 5 — Remove `_format_comment` pass-through wrapper (lines 794–803)

**Why:** A method that does nothing but call another method with the same arguments is pure noise.
It adds an extra stack frame to traces and misleads readers into thinking it applies transformation.

**Steps:**
1. Find all callers of `_format_comment` inside this file (search: `self._format_comment`).
   Currently only one caller: line 762 `comment = self._format_comment(state)`.
2. Replace `comment = self._format_comment(state)` with `comment = self._build_comment(state)`.
3. Delete the entire `_format_comment` method (lines 794–803).
4. Run `rg '_format_comment'` across the entire repo to confirm no external callers remain.
5. Run `pytest tests/`.

**Haiku prompt:**
> "In jira_agent.py, replace `comment = self._format_comment(state)` with
> `comment = self._build_comment(state)`, then delete the entire `_format_comment` method.
> Do not change any other lines."

---

## Execution Order

Run fixes in this order to minimize merge conflicts:

| Order | Fix | Risk |
|-------|-----|------|
| 1 | Fix 5 (delete pass-through) | Trivial — no logic change |
| 2 | Fix 2 (remove in-method imports) | Trivial — no logic change |
| 3 | Fix 1 (remove unused import) | Trivial — update test patch targets |
| 4 | Fix 4 (structured error logging) | Low — logging only, no logic |
| 5 | Fix 3 (settings-driven config) | Medium — touches config.py + constants.py + agent |

Each fix should be a separate commit following the convention `refactor(jira-agent): <subject>`.

---

## Verification Checklist

After all fixes:
- [ ] `rg 'ChatOpenAI' langgraph-agent/src/agents/jira_agent.py` → no results
- [ ] `rg 'import json' langgraph-agent/src/agents/jira_agent.py` → exactly 1 result (top-level)
- [ ] `rg 'import re' langgraph-agent/src/agents/jira_agent.py` → exactly 1 result (top-level)
- [ ] `rg 'hldc02\|hldc03\|jira-agent:8080' langgraph-agent/src/agents/jira_agent.py` → no results
- [ ] `rg '_format_comment' langgraph-agent/` → no results
- [ ] `pytest tests/` passes
