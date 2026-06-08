# Config Module-Level Constants Migration Guide

## Status

✅ **Phase 1-2 Complete:**
- Config validation added (weight constraints, simplified defaults)
- Comprehensive test coverage (39 tests, 100% passing)

⏳ **Phase 3 Pending:**
- Migrate remaining 12 source files from module-level constants to `get_settings()`

## Why Migrate?

**Problem:** Module-level constants are loaded once at import time. Any environment changes after startup are ignored.

**Solution:** Use `get_settings()` function (with @lru_cache) for dynamic, runtime access.

## Files to Migrate

### 1. `langgraph-agent/src/supervisor.py`
**Old imports:**
```python
from .config import (
    K8S_MCP_ENDPOINT,
    MAX_REMEDIATION_ATTEMPTS,
    MAX_REMEDIATION_LOOPS,
    REMEDIATION_RETRY_DELAY_SECONDS,
    GO_AGENT_URL,
)
```

**New approach:**
```python
from .config import get_settings

# In each function using these constants:
settings = get_settings()
# Then use: settings.k8s_mcp_endpoint, settings.max_remediation_attempts, etc.
```

**Functions to update:**
- `_log_remediation_preparation()` - uses MAX_REMEDIATION_ATTEMPTS
- `_apply_retry_delay_if_configured()` - uses REMEDIATION_RETRY_DELAY_SECONDS
- `create_prepare_hitl_node()` → returned function - uses GO_AGENT_URL
- `_get_cluster_endpoint()` - uses K8S_MCP_ENDPOINT
- `should_attempt_remediation()` - uses MAX_REMEDIATION_ATTEMPTS
- `check_remediation_result()` - uses MAX_REMEDIATION_ATTEMPTS

---

### 2. `langgraph-agent/src/server.py`
**Old imports:**
```python
from .config import CHECKPOINT_ENABLED, CHECKPOINT_NAMESPACE, HITL_ENABLED
```

**New approach:**
```python
from .config import get_settings

# In functions:
settings = get_settings()
# Use: settings.checkpoint_enabled, settings.checkpoint_namespace, settings.hitl_enabled
```

---

### 3. `langgraph-agent/src/agents/jira_agent.py`
**Old imports:**
```python
from ..config import (
    create_extraction_llm,
    get_settings,
    MAX_SIMILAR_TICKETS,
    MAX_DESCRIPTION_FOR_EXTRACTION,
)
```

**New approach:**
```python
from ..config import create_extraction_llm, get_settings

# In functions:
settings = get_settings()
# Use: settings.max_similar_tickets, settings.max_description_for_extraction
```

---

### 4. `langgraph-agent/src/agents/k8s_investigator.py`
**Migrates multiple truncation/verification constants** (~8 constants)

---

### 5. `langgraph-agent/src/agents/diagnostician.py`
**Migrates multiple truncation constants** (~6 constants)

---

### 6. `langgraph-agent/src/services/verification_service.py`
**Old imports:**
```python
from ..config import (
    VERIFICATION_TIMEOUT,
    VERIFICATION_POLL_INTERVAL,
    VERIFICATION_MIN_STABLE_CHECKS,
    VERIFICATION_INITIAL_GRACE,
)
```

**New approach:**
```python
from ..config import get_settings

# In each function:
settings = get_settings()
# Use: settings.verification_timeout, settings.verification_poll_interval, etc.
```

---

### 7. `langgraph-agent/src/tools/base_mcp_client.py`
**Old imports:**
```python
from ..config import MCP_CONNECTION_TIMEOUT, MCP_SSE_READ_TIMEOUT
```

**New approach:**
```python
from ..config import get_settings

# In __init__ or methods:
settings = get_settings()
# Use: settings.mcp_connection_timeout, settings.mcp_sse_read_timeout
```

---

### 8. `langgraph-agent/src/tools/k8s_tools.py`
**Old imports:**
```python
from ..config import K8S_MCP_ENDPOINT
```

**New approach:**
```python
from ..config import get_settings

# In methods:
settings = get_settings()
# Use: settings.k8s_mcp_endpoint
```

---

### 9. `langgraph-agent/src/checkpoint/k8s_configmap_saver.py`
**Old imports:**
```python
from ..config import CHECKPOINT_NAMESPACE, CHECKPOINT_TTL_SECONDS
```

**New approach:**
```python
from ..config import get_settings

# In __init__:
settings = get_settings()
# Use: settings.checkpoint_namespace, settings.checkpoint_ttl_seconds
```

---

### 10. `tests/conftest.py`
**Check for any config imports** and migrate if present

---

## Migration Checklist

For each file:

- [ ] Replace multi-line import with `from ..config import get_settings`
- [ ] Add `settings = get_settings()` at the start of each function using constants
- [ ] Replace all `CONSTANT_NAME` with `settings.constant_name` (note: snake_case attribute names)
- [ ] Run `python3 -m py_compile <file.py>` to verify syntax
- [ ] Run `pytest tests/` to verify no regressions
- [ ] Commit with message: `refactor(<file>): migrate to dynamic get_settings()`

## Attribute Name Mapping

| Module Constant | Settings Attribute |
|---|---|
| K8S_MCP_ENDPOINT | `settings.k8s_mcp_endpoint` |
| MAX_REMEDIATION_ATTEMPTS | `settings.max_remediation_attempts` |
| MAX_REMEDIATION_LOOPS | `settings.max_remediation_loops` |
| REMEDIATION_RETRY_DELAY_SECONDS | `settings.remediation_retry_delay` |
| GO_AGENT_URL | `settings.go_agent_url` |
| CHECKPOINT_ENABLED | `settings.checkpoint_enabled` |
| CHECKPOINT_NAMESPACE | `settings.checkpoint_namespace` |
| HITL_ENABLED | `settings.hitl_enabled` |
| MAX_SIMILAR_TICKETS | `settings.max_similar_tickets` |
| MAX_DESCRIPTION_FOR_EXTRACTION | `settings.max_description_for_extraction` |
| VERIFICATION_TIMEOUT | `settings.verification_timeout` |
| VERIFICATION_POLL_INTERVAL | `settings.verification_poll_interval` |
| VERIFICATION_MIN_STABLE_CHECKS | `settings.verification_min_stable_checks` |
| VERIFICATION_INITIAL_GRACE | `settings.verification_initial_grace` |
| MCP_CONNECTION_TIMEOUT | `settings.mcp_connection_timeout` |
| MCP_SSE_READ_TIMEOUT | `settings.mcp_sse_read_timeout` |

## Final Step: Remove Module Constants Block

After all files are migrated, delete lines 140-201 from `langgraph-agent/src/config.py`:

```python
# Backward compatibility constants (deprecated - use get_settings() instead)
settings = get_settings()
VLLM_ENDPOINT = settings.vllm_endpoint
... (all constant assignments)
```

## Verify Migration Complete

```bash
# Should return no results (no old imports)
grep -r "from config import [A-Z]" langgraph-agent/src tests/
grep -r "from \.config import [A-Z]" langgraph-agent/src tests/
grep -r "from \.\.config import [A-Z]" langgraph-agent/src tests/
```

## Testing

All 39 config tests pass:
- Weight validation (5 tests)
- Environment overrides (10 tests)
- LLM factories (7 tests)
- Default values (7 tests)
- Caching behavior (3 tests)
- History search settings (2 tests)
- Backward compatibility (5 tests)

Run: `pytest tests/unit/test_config.py -v`
