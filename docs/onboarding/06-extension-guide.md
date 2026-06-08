# Extension Guide

How to extend the system: add agents, MCP tools, workflow nodes, or change routing.

## Adding a New Specialized Agent

### 1. Create the agent class

```python
# langgraph-agent/src/agents/security_agent.py
import logging
from ..state import AgentState
from ..tools.k8s_tools import K8sTools
from ..config import create_extraction_llm

logger = logging.getLogger(__name__)

class SecurityAgent:
    """Checks for security policy violations in cluster resources."""

    def __init__(self, k8s_tools: K8sTools):
        self.k8s_tools = k8s_tools

    async def run(self, state: AgentState) -> dict:
        """Investigate security posture for the affected namespace.

        Returns a partial dict; LangGraph merges it into state.
        """
        namespace = state.get("namespace")
        if not namespace:
            logger.warning("No namespace in state — skipping security check")
            return {}

        try:
            findings = await self._check_pod_security(namespace)
            return {"security_findings": findings}
        except Exception as e:
            logger.warning(f"Security check failed (non-fatal): {e}")
            return {"security_findings": {"error": str(e)}}

    async def _check_pod_security(self, namespace: str) -> dict:
        result = await self.k8s_tools.call_tool(
            "kubectl_get",
            {"resource": "pods", "namespace": namespace, "output": "json"}
        )
        # ... analyze result with LLM or rule-based logic
        llm = create_extraction_llm()
        # ...
        return {"violations": []}
```

**Rules:**
- Return a `dict` of partial state updates. Do NOT spread the entire state dict — that duplicates `messages` (LangGraph reducer issue).
- Accept `AgentState` as input, return `dict`.
- Catch all exceptions; never let an agent crash the workflow.

### 2. Add a state field for the new data

```python
# langgraph-agent/src/state.py
class AgentState(MessagesState):
    # ... existing fields ...
    security_findings: dict[str, Any] = Field(default_factory=dict)
```

### 3. Register the node in the supervisor

```python
# langgraph-agent/src/supervisor.py

# In create_conditional_supervisor_graph():
from .agents.security_agent import SecurityAgent

security_agent = SecurityAgent(readonly_k8s_tools)  # read-only!

workflow.add_node("check_security", security_agent.run)

# Add an edge — e.g., after investigate_cluster
workflow.add_edge("investigate_cluster", "check_security")
workflow.add_edge("check_security", "diagnose")  # replace existing diagnose edge
```

If the node is optional (like JenkinsInvestigator), gate it on a config flag:

```python
if settings.security_checks_enabled:
    workflow.add_node("check_security", security_agent.run)
    workflow.add_edge("investigate_cluster", "check_security")
    workflow.add_edge("check_security", "diagnose")
else:
    workflow.add_edge("investigate_cluster", "diagnose")  # original edge
```

### 4. Write tests

```python
# tests/unit/test_security_agent.py
import pytest
from unittest.mock import AsyncMock
from langgraph_agent.src.agents.security_agent import SecurityAgent

@pytest.mark.asyncio
async def test_returns_empty_dict_without_namespace():
    agent = SecurityAgent(AsyncMock())
    result = await agent.run({"namespace": None})
    assert result == {}

@pytest.mark.asyncio
async def test_captures_exception():
    mock_tools = AsyncMock()
    mock_tools.call_tool.side_effect = RuntimeError("connection failed")
    agent = SecurityAgent(mock_tools)
    result = await agent.run({"namespace": "ml-training"})
    assert "error" in result["security_findings"]
```

---

## Adding a New MCP Tool (Go Side)

### 1. Implement the tool handler in Go

```go
// pkg/mcp/k8s/server.go

// Add to the tool registration list:
server.AddTool(mcp.Tool{
    Name:        "kubectl_top_pods",
    Description: "Get CPU and memory usage for pods in a namespace",
    InputSchema: mcp.ToolInputSchema{
        Type: "object",
        Properties: map[string]mcp.Property{
            "namespace": {Type: "string", Description: "Kubernetes namespace"},
        },
        Required: []string{"namespace"},
    },
}, func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
    namespace := req.Params.Arguments["namespace"].(string)
    output, err := runKubectl(ctx, "top", "pods", "-n", namespace)
    if err != nil {
        return mcp.NewToolResultError(err.Error()), nil
    }
    return mcp.NewToolResultText(output), nil
})
```

### 2. Add the Python client method

```python
# langgraph-agent/src/tools/k8s_tools.py

class K8sTools(BaseMCPClient):
    # ... existing methods ...

    async def top_pods(self, namespace: str) -> dict:
        """Get CPU and memory usage for pods."""
        return await self.call_tool("kubectl_top_pods", {"namespace": namespace})
```

### 3. If the tool is write-capable, add it to WRITE_TOOLS

```python
# langgraph-agent/src/tools/k8s_tools.py
WRITE_TOOLS = frozenset([
    "kubectl_apply",
    "kubectl_delete",
    "kubectl_scale",
    "kubectl_set_image",
    "kubectl_rollout_restart",
    "kubectl_my_new_write_tool",  # add here
])
```

Tools in `WRITE_TOOLS` are blocked when `K8sTools(readonly=True)` is used.

### 4. Rebuild and redeploy

```bash
make redeploy
```

---

## Adding a New Jira MCP Tool

Same pattern as K8s, but in `pkg/mcp/jira/server.go` and `langgraph-agent/src/tools/jira_tools.py`.

---

## Modifying Workflow Routing

Routing functions are simple Python functions that return a string (the name of the next node).

### Add a conditional branch

```python
# langgraph-agent/src/supervisor.py

def should_run_security_check(state: AgentState) -> str:
    """Run security check only for high-priority tickets."""
    if state.get("ticket_priority") in ("High", "Critical"):
        return "check_security"
    return "diagnose"

# Replace:
#   workflow.add_edge("investigate_cluster", "diagnose")
# With:
workflow.add_conditional_edges(
    "investigate_cluster",
    should_run_security_check,
    {
        "check_security": "check_security",
        "diagnose": "diagnose",
    }
)
```

The second argument to `add_conditional_edges` is the routing function. The third is a mapping of return values to node names (required by LangGraph for graph visualization).

### Change an existing route

Find the relevant routing function in `supervisor.py`. They are named clearly:

- `should_continue_after_ticket_read` — routes after `read_ticket`
- `should_attempt_remediation` — routes after `diagnose` (non-HITL path)
- `should_skip_remediation` — routes after `prepare_hitl` (HITL path)
- `check_remediation_result` — routes after `attempt_remediation`
- `check_fix_verified` — routes after `verify_fix`

---

## Adding a New Go Configuration Variable

1. Add the field to the `Config` struct in `cmd/jira-agent/config.go`:

```go
type Config struct {
    // ... existing fields ...
    MyNewFeatureEnabled bool
}
```

2. Load it in `LoadConfig()`:

```go
func LoadConfig() *Config {
    return &Config{
        // ... existing fields ...
        MyNewFeatureEnabled: getenv("MY_NEW_FEATURE_ENABLED", "false") == "true",
    }
}
```

3. Add it to the deployment manifest:

```yaml
# deploy/base/jira-agent/deployment.yaml
env:
- name: MY_NEW_FEATURE_ENABLED
  value: "true"
```

---

## Adding a New Python Configuration Variable

1. Add to `Settings` in `langgraph-agent/src/config.py`:

```python
class Settings(BaseSettings):
    # ... existing fields ...
    my_feature_timeout: int = Field(default=60, alias="MY_FEATURE_TIMEOUT")
```

2. Access it via `get_settings()`:

```python
from .config import get_settings

settings = get_settings()
timeout = settings.my_feature_timeout
```

3. Add to deployment manifest if it needs a non-default value:

```yaml
# deploy/base/langgraph-agent/deployment.yaml
env:
- name: MY_FEATURE_TIMEOUT
  value: "120"
```

4. Add tests to `tests/unit/test_config.py`:

```python
def test_my_feature_timeout_default():
    settings = Settings()
    assert settings.my_feature_timeout == 60

def test_my_feature_timeout_env_override(monkeypatch):
    monkeypatch.setenv("MY_FEATURE_TIMEOUT", "30")
    settings = Settings()
    assert settings.my_feature_timeout == 30
```

---

## Adding a New Target Cluster

The system currently targets `hldc02`, `hldc03`, and `saas`. To add a new cluster:

### Go side

```bash
# Add to CLUSTER_CONFIGS env var
# Format: "name:kubeconfig-path" or "name:in-cluster"
CLUSTER_CONFIGS=saas:in-cluster,hldc02:/etc/k8s/kubeconfigs/hldc02/config,hldc03:/etc/k8s/kubeconfigs/hldc03/config,newcluster:/etc/k8s/kubeconfigs/newcluster/config
```

Mount the kubeconfig as a Kubernetes Secret and volume mount it into the jira-agent pod. See `deploy/overlays/cluster-a/` for an example.

### Python side

Add the cluster name to `K8S_CLUSTERS` setting:

```python
# config.py default, or override via env var
k8s_clusters: list[str] = Field(
    default=["hldc02", "hldc03", "newcluster"],
    alias="K8S_CLUSTERS"
)
```

The supervisor automatically generates the MCP endpoint URL as:
`http://jira-agent:8080/mcp/k8s/{cluster}`

### JiraAgent extraction

`JiraAgent.read_ticket` uses an LLM to extract the cluster name from the ticket text. The LLM is prompted with the list of known cluster names from `K8S_CLUSTERS`. Adding `newcluster` to that list is sufficient for the agent to recognize it.

---

## Multi-Pod Considerations

**RemediationLockService** is currently in-memory only. If you scale `langgraph-agent` to multiple replicas, two pods could attempt remediation on the same ticket concurrently. To fix this, replace `RemediationLockService` with a Kubernetes-ConfigMap-backed distributed lock (similar to how checkpointing works, but with a lease mechanism).

**K8sConfigMapSaver** checkpoints are stored in Kubernetes ConfigMaps — shared across pods. Checkpointing already works correctly in a multi-pod setup.
