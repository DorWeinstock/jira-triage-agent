# Local Development

## Prerequisites

- Go 1.21+
- Python 3.12+
- Docker
- `kubectl`
- `kind` (for local cluster)
- Access to the internal Artifactory registry (`artifactory-kfs.habana-labs.com`)

## Option A: Full Local Cluster (kind)

This runs both services in a local Kubernetes cluster using `kind`. This is the recommended path for testing end-to-end changes.

### 1. Create the kind cluster

```bash
make create-cluster
# Creates a kind cluster named "kagent-test"
```

### 2. Create Kubernetes secrets

```bash
kubectl create namespace jira-k8s-agent

kubectl create secret generic jira-k8s-agent-secret \
  --from-literal=jira-url=https://jira.devtools.intel.com \
  --from-literal=jira-email=your-email@habana-labs.com \
  --from-literal=jira-api-token=ATATT3xFfGF0... \
  --from-literal=jenkins-username=your-jenkins-user \
  --from-literal=jenkins-api-token=your-jenkins-token \
  --from-literal=langchain-api-key=ls__... \
  -n jira-k8s-agent
```

Or use the interactive helper:
```bash
make recreate-secret
```

### 3. Build and load images

```bash
# Build both Docker images and load into kind
make kind-load-all
```

### 4. Deploy

```bash
make deploy-all

# Check that pods are running
make status
```

### 5. Verify

```bash
# Watch logs
make logs-jira-agent
make logs-langgraph

# Port forward for manual API calls
make port-forward-langgraph  # localhost:8000
make port-forward-jira-agent # localhost:8080
```

### Rebuilding after changes

```bash
# Rebuild images, reload into kind, restart deployments
make redeploy
```

For a clean slate (deletes namespace, rebuilds everything):
```bash
make redeploy-clean
# Then recreate secrets manually: make recreate-secret
```

---

## Option B: Run Go Locally + Python Locally

Useful when you're making changes to one side and want a fast iteration loop without Docker.

### Terminal 1: Go monolith

```bash
# Set required env vars
export JIRA_URL=https://jira.devtools.intel.com
export JIRA_EMAIL=your-email@habana-labs.com
export JIRA_API_TOKEN=ATATT3xFfGF0...
export AGENT_URL=http://localhost:8000
export CLUSTER_CONFIGS=saas:in-cluster  # or path to a kubeconfig

make run-local
# Go service starts on :8080
```

### Terminal 2: Python agent

```bash
cd langgraph-agent
pip install -e ".[dev]"

# Set required env vars
export JIRA_MCP_ENDPOINT=http://localhost:8080/mcp/jira
export K8S_MCP_ENDPOINT=http://localhost:8080/mcp/k8s
export GO_AGENT_URL=http://localhost:8080
export VLLM_ENDPOINT=http://vllm-service:8000/v1  # or your vLLM instance
export VLLM_MODEL_NAME=meta-llama/Llama-3.1-70B-Instruct

uvicorn src.main:app --reload --port 8000
```

### Send a test investigation

```bash
curl -X POST http://localhost:8000/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id": "GAUDISW-9999",
    "summary": "Pod crashing in hldc02 namespace ml-training",
    "target_cluster": "hldc02"
  }'
```

---

## Option C: LangGraph Studio (Python workflow only)

LangGraph Studio lets you visualize the graph, step through nodes, and inspect state at each step.

```bash
cd langgraph-agent

# Install LangGraph CLI
pip install langgraph-cli

# Start Studio (uses langgraph.json for config)
langgraph dev

# Studio opens at http://localhost:8123
```

`langgraph.json` points to `get_graph_for_studio()` in `supervisor.py`, which creates the graph using `localhost:8080` MCP endpoints. The Go monolith must be running for tool calls to work.

---

## Option D: Remote Cluster Deployment

To deploy to one of the real clusters (`hldc02`/`hldc03`):

```bash
# Set kubeconfig pointing to the remote cluster
export REMOTE_KUBECONFIG=~/.kube/config-sched
export REMOTE_KUBE_CONTEXT=test-sched

# Build, push to Artifactory, and deploy
make deploy-remote

# Watch logs on the remote cluster
make logs-jira-agent-remote
make logs-langgraph-remote
```

The `deploy-remote` target:
1. Builds both Docker images
2. Tags with a timestamp (`IMAGE_TAG`)
3. Pushes to `artifactory-kfs.habana-labs.com/docker-developers/users/anoah/`
4. Updates the Deployment image via `kubectl set image`
5. Waits for rollout to complete

---

## Go-Specific Development

### Build the Go binary

```bash
make build
# Outputs: bin/jira-agent
```

### Run Go tests

```bash
go test -v ./...
```

### Adding a new MCP tool

1. Add the tool handler in `pkg/mcp/jira/server.go` or `pkg/mcp/k8s/server.go`
2. Register it in the MCP server's tool list
3. Add the corresponding Python client method in `langgraph-agent/src/tools/jira_tools.py` or `k8s_tools.py`
4. Write tests in `tests/integration/`

---

## Python-Specific Development

### Install dependencies

```bash
cd langgraph-agent
pip install -e ".[dev]"
```

### Run Python tests

```bash
# All Python tests
pytest tests/ -v

# Unit tests only (fast, no I/O)
pytest tests/unit/ -v

# With coverage
pytest tests/unit/ --cov=langgraph-agent/src --cov-report=html
```

### Key development patterns

**Importing config:** Use `get_settings()` not module-level constants. See [MIGRATION_GUIDE.md](../../MIGRATION_GUIDE.md) for the in-progress migration.

```python
# Good
from .config import get_settings
settings = get_settings()
max_attempts = settings.max_remediation_attempts

# Avoid (deprecated, being migrated)
from .config import MAX_REMEDIATION_ATTEMPTS
```

**Adding a state field:** Edit `state.py`. The field is immediately available to all agents. Use `Optional[X] = None` for fields that may not be set.

**LLM calls:** Use the factory functions, not raw `ChatOpenAI`:

```python
from .config import create_llm, create_extraction_llm, create_diagnosis_llm

# Low temperature, structured output
llm = create_extraction_llm()

# Higher temperature, narrative output
llm = create_diagnosis_llm()
```

---

## Proxy Configuration

The Python container needs the Intel corporate proxy to reach LangSmith for tracing:

```
HTTP_PROXY=http://proxy-dmz.intel.com:912
HTTPS_PROXY=http://proxy-dmz.intel.com:912
NO_PROXY=10.96.0.1,jira-agent,vllm-service,...
```

When running locally, set these if your workstation is inside the Intel network and you want LangSmith tracing to work.

---

## LangSmith Tracing

Set `LANGCHAIN_API_KEY` to your LangSmith API key and `LANGCHAIN_TRACING_V2=true`. Each investigation run creates a trace in project `jira-webhook-agent`.

```bash
# View recent traces via CLI
make traces LIMIT=10

# View a specific trace
make trace ID=<trace-id>

# View recent errors
make traces-errors
```
