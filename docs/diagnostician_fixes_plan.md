# Diagnostician Fix Plan

## Overview
Fix 5 identified issues in `langgraph-agent/src/agents/diagnostician.py`:
1. Stale import (line 17)
2. Brittle JSON parsing (line 188–199)
3. Type inconsistency in multi-step plan logging (line 321)
4. Broad exception handling masking failures (line 181–183)
5. Missing test coverage for critical paths

---

## Fix 1 — Remove stale `ChatOpenAI` import (line 17)

### Files changed
- `langgraph-agent/src/agents/diagnostician.py`

### Steps
1. Delete line 17: `from langchain_openai import ChatOpenAI  # For test patching`
2. Verify no tests patch `ChatOpenAI` directly (all should patch `create_diagnosis_llm`)
3. Run: `pytest tests/unit/langgraph_agent/ -x -q`

---

## Fix 2 — Harden `_parse_json_response` (lines 188–199)

### Files changed
- `langgraph-agent/src/agents/diagnostician.py`
- `tests/unit/langgraph_agent/test_diagnostician_parse_json.py` *(new file)*

### Steps
1. Move `import json` and `import re` to module top-level (after line 14).
2. Replace method body with explicit error handling and `ToolError` raises.
3. Create new test file with cases:
   - Fenced JSON block
   - Bare JSON object
   - No JSON found → ToolError
   - Invalid JSON → ToolError
   - Valid JSON, bad schema → ValidationError
4. Run: `pytest tests/unit/langgraph_agent/test_diagnostician_parse_json.py -v`

---

## Fix 3 — Fix type inconsistency in `attempt_remediation` log (line 321)

### Files changed
- `langgraph-agent/src/agents/diagnostician.py`

### Steps
1. Replace log on lines 320–323 to show actual steps instead of flat fields.
2. No new tests needed.
3. Run: `pytest tests/unit/langgraph_agent/test_multi_step_remediation.py -v`

---

## Fix 4 — Improve broad `except` in `run()` (lines 181–183)

### Files changed
- `langgraph-agent/src/agents/diagnostician.py`
- `tests/unit/langgraph_agent/test_diagnostician_error_handling.py` *(new file)*

### Steps
1. Change `logger.warning` to `logger.exception` on the broad except block.
2. Create new test file with error cases and fallback validation.
3. Run: `pytest tests/unit/langgraph_agent/test_diagnostician_error_handling.py -v`

---

## Fix 5 — Add missing tests for parse and multi-step remediation

### Files changed
- `tests/unit/langgraph_agent/test_multi_step_remediation.py` *(extend)*

### Steps
1. Add test cases for JSON parsing errors and low-confidence multi-step skipping.
2. Run full test suite: `pytest tests/unit/langgraph_agent/ -v`

---

## Execution Order

| Order | Fix | Duration |
|-------|-----|----------|
| 1 | Remove stale import | 2 min |
| 2 | Harden JSON parsing + tests | 10 min |
| 3 | Fix log type inconsistency | 3 min |
| 4 | Improve exception handling + tests | 8 min |
| 5 | Add residual test coverage | 5 min |

**Total:** ~30 minutes
