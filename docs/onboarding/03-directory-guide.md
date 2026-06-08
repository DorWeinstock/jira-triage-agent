# Directory Guide

Every file that matters, and what it owns.

## Top-Level Layout

```
jira-jenkins-agent/
├── cmd/                     Go entrypoints
├── pkg/                     Go shared packages
├── langgraph-agent/         Python LangGraph agent (separate deployable)
├── tests/                   All tests (Go + Python, centralized)
├── deploy/                  Kubernetes manifests (kustomize)
├── docs/                    Documentation
│   └── onboarding/          ← you are here
├── hack/                    Dev scripts
├── CLAUDE.md                AI agent development guide
├── MIGRATION_GUIDE.md       In-progress config migration (Phase 3 pending)
├── Makefile                 All build, test, deploy targets
├── Dockerfile               Go monolith container
├── go.mod                   Go module
└── README.md                Project overview
```

## Go Monolith (`cmd/` and `pkg/`)

### `cmd/jira-agent/`

| File | Owns |
|------|------|
| `main.go` | Wires everything together: loads config, sets up MCP servers, starts poller, starts HTTP server |
| `config.go` | `LoadConfig()` — reads all env vars for the Go service |

### `pkg/poller/`

| File | Owns |
|------|------|
| `poller.go` | Jira polling loop. Runs JQL query, filters by label `ai-investigate`, dispatches to langgraph-agent via HTTP POST, manages label lifecycle (`ai-investigate` → `ai-investigate-in-progress` → `ai-agent-investigated`), enforces `MAX_CONCURRENT_DISPATCHES` semaphore |

### `pkg/mcp/jira/`

| File | Owns |
|------|------|
| `server.go` | MCP SSE server for Jira tools. Implements: `get_ticket`, `search_tickets`, `add_comment`, `move_to_in_progress`, `move_to_in_review`. Calls Jira REST API using `JIRA_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN` |

### `pkg/mcp/k8s/`

| File | Owns |
|------|------|
| `server.go` | MCP SSE server for Kubernetes tools. Implements kubectl operations. Supports multi-cluster via `CLUSTER_CONFIGS` env var. Routes by URL path: `/mcp/k8s` (default cluster) and `/mcp/k8s/{cluster}` (named cluster) |

### `pkg/api/`

| File | Owns |
|------|------|
| `hitl_handlers.go` | HITL REST endpoints: `POST /hitl/pending` (register ticket), `GET /hitl/pending/{id}` (check status). Stores pending HITL state, polls Jira comments for approval/rejection keywords |
| `transition_handlers.go` | Jira transition helpers used by the HITL flow |

## Python Agent (`langgraph-agent/`)

### `langgraph-agent/src/`

| File | Owns |
|------|------|
| `main.py` | Application entrypoint. Initializes all dependencies (tools, checkpointer, lock service), creates the LangGraph, starts FastAPI via uvicorn |
| `server.py` | FastAPI app. Defines routes: `POST /investigate`, `GET /health`, `POST /hitl/approve`, `POST /hitl/reject`. Imports `HITL_INTERRUPT_NODES` from `supervisor.py` |
| `supervisor.py` | **Most important Python file.** Defines the LangGraph `StateGraph`, all nodes, all conditional routing functions. Creates and caches per-cluster agent instances |
| `state.py` | `AgentState` — the shared Pydantic model that flows through every node. Adding a new field here makes it available to all agents |
| `config.py` | `Settings` class (pydantic-settings). `get_settings()` cached accessor. LLM factory functions `create_llm()`, `create_diagnosis_llm()`, `create_extraction_llm()`. All configuration lives here |

### `langgraph-agent/src/agents/`

| File | Owns |
|------|------|
| `jira_agent.py` | `JiraAgent` class. Methods: `read_ticket` (graph node), `post_comment` (graph node). Internally owns history search via `HistoryAgent` composition |
| `history_agent.py` | `HistoryAgent` class. Not a graph node. Used by `JiraAgent` to search similar past Jira tickets using JQL + LLM re-ranking. Composite scoring: LLM similarity (55%), component match (10%), status score (20%), recency (15%) |
| `k8s_investigator.py` | `K8sInvestigator` class. Method: `run`. Fetches pod status, logs, events. Uses read-only K8s tools |
| `diagnostician.py` | `Diagnostician` class. Methods: `run` (diagnosis), `attempt_remediation` (execute fix). Owns the remediation logic |
| `k8s_remediation_executor.py` | `K8sRemediationExecutor` class. Executes structured `remediation_plan` using write-capable K8s tools. Acquires remediation lock before executing |
| `jenkins_investigator.py` | `JenkinsInvestigator` class. Method: `run`. Fetches Jenkins console logs. Only instantiated when `JENKINS_MCP_ENDPOINT` is set |

### `langgraph-agent/src/tools/`

| File | Owns |
|------|------|
| `base_mcp_client.py` | `BaseMCPClient` — shared SSE connection logic, timeout config, error handling |
| `jira_tools.py` | `JiraTools` — Python client wrapping the Go Jira MCP server |
| `k8s_tools.py` | `K8sTools` — Python client wrapping the Go K8s MCP server. `readonly=True` parameter filters available tools |
| `jenkins_tools.py` | `JenkinsTools` — Python client wrapping the Go Jenkins MCP server |

### `langgraph-agent/src/services/`

| File | Owns |
|------|------|
| `remediation_lock_service.py` | `RemediationLockService` — in-memory lock preventing concurrent remediation on the same ticket. **POC: not suitable for multi-pod deployments.** |
| `verification_service.py` | `VerificationService` — re-investigates after remediation using read-only tools. Polls until pods are stable or timeout (90s default). Uses LLM to interpret findings |
| `approval_comment.py` | `format_approval_comment()` — formats the HITL approval comment posted to Jira |

### `langgraph-agent/src/checkpoint/`

| File | Owns |
|------|------|
| `k8s_configmap_saver.py` | `K8sConfigMapSaver` — LangGraph `BaseCheckpointSaver` implementation backed by a Kubernetes ConfigMap. TTL-based expiry, 900KB soft limit per entry |

### `langgraph-agent/` root

| File | Owns |
|------|------|
| `pyproject.toml` | Python dependencies and project metadata |
| `langgraph.json` | LangGraph Studio configuration. Points Studio at `get_graph_for_studio()` in `supervisor.py` |
| `Dockerfile` | Python container. Multi-stage build; final image is `python:3.12-slim` |

## Tests (`tests/`)

```
tests/
├── conftest.py          Shared pytest fixtures (sys.path, mock tools, mock state)
├── unit/                Test individual functions in isolation (no I/O)
│   ├── test_config.py   39 tests for Settings class, LLM factories, weight validation
│   ├── test_supervisor.py
│   ├── test_jira_agent.py
│   └── ...
├── integration/         Test component interactions (may use real or mock MCP)
│   ├── test_mcp_jira.py
│   ├── test_mcp_k8s.py
│   └── ...
├── e2e/                 Full workflow tests (requires running cluster)
└── smoke/               Quick sanity checks (<30s total)
```

## Deployment (`deploy/`)

```
deploy/
├── base/
│   ├── jira-agent/
│   │   ├── deployment.yaml    Go monolith Deployment + Service
│   │   ├── rbac.yaml          ServiceAccount + ClusterRole for K8s API access
│   │   └── kustomization.yaml
│   └── langgraph-agent/
│       ├── deployment.yaml    Python Deployment + Service
│       ├── rbac.yaml          ServiceAccount for ConfigMap read/write
│       └── kustomization.yaml
└── overlays/
    ├── cluster-a/             hldc02-specific overrides (image, env, cluster config)
    └── cluster-b/             hldc03-specific overrides
```

Deploy with: `kubectl apply -k deploy/base/`

For cluster-specific overlays: `kubectl apply -k deploy/overlays/cluster-a/`

## Hack Scripts (`hack/scripts/`)

| Script | Purpose |
|--------|---------|
| `quick-start.sh` | Creates a `kind` cluster named `kagent-test` with the correct config |
