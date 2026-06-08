# Testing

## Test Structure

```
tests/
├── conftest.py          Shared fixtures for all test types
├── unit/                Test individual functions, no I/O
├── integration/         Test component interactions
├── e2e/                 Full workflow tests (requires cluster)
└── smoke/               Quick sanity checks (<30s total)
```

All Python tests use `pytest`. Go tests use `go test`.

## Running Tests

### All tests

```bash
make test         # Go tests + Python integration tests
```

### Go tests only

```bash
make test-go
# Equivalent: go test -v ./...
```

### Python tests by type

```bash
# Unit tests (fast, no network, no cluster)
pytest tests/unit/ -v

# Integration tests
make test-integration
# Equivalent: pytest tests/integration/ -v

# Smoke tests (quick validation, <30s)
make test-smoke

# All Python tests with coverage
pytest tests/ --cov=langgraph-agent/src --cov-report=html

# Parallel execution (faster on multi-core)
pytest -n auto tests/unit/
```

### Specific test file

```bash
pytest tests/unit/test_config.py -v
pytest tests/unit/test_supervisor.py -v -k "test_routing"
```

## Test Types

### Unit Tests (`tests/unit/`)

Test individual Python functions in isolation. No network calls, no Kubernetes, no LLM calls. Mocks provided in `conftest.py`.

Key unit test files:

| File | Tests |
|------|-------|
| `test_config.py` | 39 tests: Settings validation, LLM factories, weight constraints, env overrides, caching |
| `test_supervisor.py` | Routing functions (`should_continue_after_ticket_read`, `check_remediation_result`, `check_fix_verified`), `initialize_state` |
| `test_jira_agent.py` | `read_ticket`, `post_comment`, history search logic |
| `test_k8s_investigator.py` | Investigation logic, log deduplication |
| `test_diagnostician.py` | Diagnosis generation, remediation plan structure |

### Integration Tests (`tests/integration/`)

Test that components work together. May start a local MCP server or use a mock. Slower than unit tests.

### E2E Tests (`tests/e2e/`)

Full workflow tests. Require a running cluster with both services deployed. Used in CI before merging to main.

### Smoke Tests (`tests/smoke/`)

Minimal tests that verify the system is operational. Run after deployment to confirm basic health.

## Shared Fixtures (`tests/conftest.py`)

`conftest.py` provides:

- `mock_jira_tools` — `JiraTools` with all methods stubbed
- `mock_k8s_tools` — `K8sTools` with all methods stubbed
- `sample_agent_state` — A pre-populated `AgentState` with a real-looking ticket
- `sample_cluster_findings` — Example K8s investigation output

Import them by parameter name in any test:

```python
async def test_my_agent(mock_jira_tools, sample_agent_state):
    agent = JiraAgent(mock_jira_tools)
    result = await agent.read_ticket(sample_agent_state)
    assert result["ticket_id"] == "GAUDISW-1234"
```

## Writing New Tests

### Unit test for a new routing function

```python
# tests/unit/test_supervisor.py
from langgraph_agent.src.supervisor import should_continue_after_ticket_read

def test_routes_to_post_comment_when_no_ticket_id():
    state = {"ticket_id": None}
    assert should_continue_after_ticket_read(state) == "post_comment"

def test_routes_to_skip_when_no_namespace():
    state = {
        "ticket_id": "GAUDISW-1",
        "namespace": None,
        "affected_resources": {"deployments": ["api-server"]},
    }
    assert should_continue_after_ticket_read(state) == "skip_investigation_no_namespace"
```

### Unit test for a new agent method

```python
# tests/unit/test_my_agent.py
import pytest
from unittest.mock import AsyncMock
from langgraph_agent.src.agents.my_agent import MyAgent

@pytest.mark.asyncio
async def test_my_agent_run(sample_agent_state):
    mock_tools = AsyncMock()
    mock_tools.some_tool.return_value = {"result": "ok"}

    agent = MyAgent(mock_tools)
    result = await agent.run(sample_agent_state)

    assert "my_findings" in result
    mock_tools.some_tool.assert_called_once()
```

### Integration test for a new MCP tool

```python
# tests/integration/test_mcp_my_tool.py
import pytest
from langgraph_agent.src.tools.k8s_tools import K8sTools

@pytest.mark.integration
async def test_my_new_tool(k8s_mcp_endpoint):
    tools = K8sTools(mcp_endpoint=k8s_mcp_endpoint)
    result = await tools.call_tool("my_new_tool", {"param": "value"})
    assert result is not None
```

## Config Tests

The `test_config.py` file is the most comprehensive test file with 39 tests. It covers:

- All `Settings` fields have correct defaults
- Environment variable overrides work
- `validate_weights_sum` raises on invalid weight config
- `get_settings()` is properly cached (same instance on repeated calls)
- `create_llm()`, `create_extraction_llm()`, `create_diagnosis_llm()` return `ChatOpenAI` instances

Run it after any changes to `config.py`:

```bash
pytest tests/unit/test_config.py -v
```

## CI Notes

Tests run in CI on every pull request. The CI pipeline:

1. `go test -v ./...` — Go unit tests
2. `pytest tests/unit/ tests/integration/` — Python tests (no cluster required)
3. E2E tests run separately on merge to main against a real cluster

Keep unit tests fast (<5s per test). Use `pytest.mark.slow` for tests that take longer and are excluded from the default run.
