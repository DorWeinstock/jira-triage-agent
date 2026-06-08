# Configuration Reference

All environment variables and Kubernetes secrets for both services.

## Go Service (jira-agent) — Environment Variables

Loaded by `cmd/jira-agent/config.go` via `LoadConfig()`.

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `PORT` | `8080` | No | HTTP server port |
| `JIRA_URL` | *(none)* | **Yes** | Jira instance base URL, e.g. `https://jira.devtools.intel.com` |
| `JIRA_EMAIL` | *(none)* | **Yes** | Jira account email, e.g. `your-email@habana-labs.com` |
| `JIRA_API_TOKEN` | *(none)* | **Yes** | Jira API token (from Jira → Account Settings → Security) |
| `FILTER_PROJECT` | `GAUDISW` | No | Jira project key to poll |
| `FILTER_COMPONENT` | `DevOps_K8S` | No | Jira component filter |
| `FILTER_ISSUE_TYPE` | *(empty)* | No | Jira issue type filter (empty = all types) |
| `POLLING_INTERVAL` | `3m` | No | How often to poll Jira (Go duration string) |
| `HITL_POLL_INTERVAL` | `2m` | No | How often to poll Jira comments for HITL approval |
| `AGENT_URL` | `http://langgraph-agent:8000` | No | URL of the Python langgraph-agent service |
| `MAX_CONCURRENT_DISPATCHES` | `5` | No | Max parallel ticket investigations |
| `LOG_FORMAT` | `console` | No | Log output format: `console` or `json` |
| `LOG_LEVEL` | `info` | No | Log level: `debug`, `info`, `warn`, `error` |
| `JENKINS_USERNAME` | *(empty)* | No | Jenkins username. If empty, Jenkins MCP not registered |
| `JENKINS_API_TOKEN` | *(empty)* | No | Jenkins API token. Required when `JENKINS_USERNAME` set |
| `CLUSTER_CONFIGS` | `saas:in-cluster` | No | Multi-cluster config. Format: `name:path,name:in-cluster`. Example: `saas:in-cluster,hldc02:/etc/k8s/kubeconfigs/hldc02/config,hldc03:/etc/k8s/kubeconfigs/hldc03/config` |

## Python Agent (langgraph-agent) — Environment Variables

Loaded by `langgraph-agent/src/config.py` via `pydantic-settings`.

### LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_ENDPOINT` | `http://vllm-service:8000/v1` | OpenAI-compatible vLLM API endpoint |
| `VLLM_MODEL_NAME` | `meta-llama/Llama-3.1-70B-Instruct` | Model name passed to the vLLM API |

### MCP Endpoints

| Variable | Default | Description |
|----------|---------|-------------|
| `JIRA_MCP_ENDPOINT` | `http://jira-agent.default.svc.cluster.local:8080/mcp/jira` | Jira MCP server URL |
| `K8S_MCP_ENDPOINT` | `http://jira-agent.default.svc.cluster.local:8080/mcp/k8s` | Default (in-cluster) K8s MCP URL |
| `JENKINS_MCP_ENDPOINT` | *(empty)* | Jenkins MCP URL. If empty, JenkinsInvestigator node is not registered |
| `GO_AGENT_URL` | `http://jira-agent:8080` | Go service base URL (used for HITL registration) |
| `K8S_CLUSTERS` | `["hldc02", "hldc03"]` | JSON list of known cluster names for LLM extraction |
| `K8S_CLUSTER_MCP_BASE_URL` | `http://jira-agent:8080/mcp/k8s` | Base URL for per-cluster MCP endpoints. Appended with `/{cluster}` |

### Workflow

| Variable | Default | Description |
|----------|---------|-------------|
| `HITL_ENABLED` | `true` | Enable human-in-the-loop approval before remediation |
| `REMEDIATION_RETRY_DELAY_SECONDS` | `20` | Seconds to wait between remediation retries (allows K8s to reconcile) |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGCHAIN_TRACING_V2` | `true` | Enable LangSmith tracing |
| `LANGCHAIN_PROJECT` | `jira-k8s-agent` | LangSmith project name |
| `LANGCHAIN_API_KEY` | *(empty)* | LangSmith API key (from `ls__...`). Optional — tracing disabled if absent |
| `LANGCHAIN_ENDPOINT` | `https://api.smith.langchain.com` | LangSmith API endpoint |

### Proxy (required for external connectivity inside Intel network)

| Variable | Value | Description |
|----------|-------|-------------|
| `HTTP_PROXY` | `http://proxy-dmz.intel.com:912` | Corporate HTTP proxy |
| `HTTPS_PROXY` | `http://proxy-dmz.intel.com:912` | Corporate HTTPS proxy |
| `NO_PROXY` | *(see deployment.yaml)* | Comma-separated list of hosts that bypass proxy (cluster-internal services, Gaudi hosts) |

### Advanced / Tuning

These have sensible defaults. Only change if you know what you're doing.

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_CONNECTION_TIMEOUT` | `30.0` | MCP SSE connection timeout (seconds) |
| `MCP_SSE_READ_TIMEOUT` | `600.0` | MCP SSE read timeout (seconds) |
| `MAX_REMEDIATION_ATTEMPTS` | `2` | Max K8s remediation retries |
| `MAX_REMEDIATION_LOOPS` | `3` | Max post-fix diagnostic cycles |
| `VERIFICATION_TIMEOUT` | `90` | Seconds to wait for fix verification |
| `VERIFICATION_POLL_INTERVAL` | `5` | Seconds between verification polls |
| `CHECKPOINT_TTL_SECONDS` | `86400` | Checkpoint ConfigMap TTL (24 hours) |

## Kubernetes Secrets

### `jira-k8s-agent-secret`

Used by both deployments.

| Key | Used by | Description |
|-----|---------|-------------|
| `jira-url` | jira-agent (Go) | Maps to `JIRA_URL` |
| `jira-email` | jira-agent (Go) | Maps to `JIRA_EMAIL` |
| `jira-api-token` | jira-agent (Go) | Maps to `JIRA_API_TOKEN` |
| `jenkins-username` | jira-agent (Go) | Maps to `JENKINS_USERNAME`. Omit to disable Jenkins integration |
| `jenkins-api-token` | jira-agent (Go) | Maps to `JENKINS_API_TOKEN` |
| `langchain-api-key` | langgraph-agent (Python) | Maps to `LANGCHAIN_API_KEY`. Optional |

Create it:
```bash
kubectl create secret generic jira-k8s-agent-secret \
  --from-literal=jira-url=https://jira.devtools.intel.com \
  --from-literal=jira-email=your-email@habana-labs.com \
  --from-literal=jira-api-token=ATATT3xFfGF0... \
  --from-literal=jenkins-username=your-jenkins-user \
  --from-literal=jenkins-api-token=your-jenkins-token \
  --from-literal=langchain-api-key=ls__... \
  -n jira-k8s-agent
```

Or interactively:
```bash
make recreate-secret
```

### `kubeconfig-secret` (optional, for multi-cluster)

Stores kubeconfig files for remote clusters. Mounted into the jira-agent pod at `/etc/k8s/kubeconfigs/`.

| Key | Mount path | Description |
|-----|-----------|-------------|
| `hldc02-config` | `/etc/k8s/kubeconfigs/hldc02/config` | kubeconfig for hldc02 |
| `hldc03-config` | `/etc/k8s/kubeconfigs/hldc03/config` | kubeconfig for hldc03 |

See `deploy/overlays/cluster-a/` for a complete example including the volume mount configuration.

## Jira Transition IDs

These are specific to the `GAUDISW` Jira project:

| Transition | ID | When triggered |
|------------|-----|----------------|
| Assign | 11 | When ticket is assigned |
| In Progress | 31 | When `investigate_cluster` starts |
| In Review | 41 | When `verify_fix` confirms issue resolved |

If you're adapting this system for a different Jira project, you'll need to update these IDs in `pkg/mcp/jira/server.go`.

## Container Registry

Images are pushed to:
```
artifactory-kfs.habana-labs.com/docker-developers/users/anoah/
  ├── jira-agent:1.0.0
  ├── jira-agent:latest
  ├── jira-agent:<timestamp>   (used by deploy-remote)
  ├── langgraph-agent:1.0.0
  ├── langgraph-agent:latest
  └── langgraph-agent:<timestamp>
```

Override the registry prefix:
```bash
make push-all REGISTRY=your-registry/your-prefix
```

## Resource Limits

Defined in `deploy/base/*/deployment.yaml`:

| Service | CPU request | CPU limit | Memory request | Memory limit |
|---------|-------------|-----------|----------------|--------------|
| jira-agent | *(not set)* | *(not set)* | *(not set)* | *(not set)* |
| langgraph-agent | 500m | 1000m | 512Mi | 1Gi |

The Python agent's memory limit of 1Gi is sufficient for the current workload. If you add large in-memory caches, adjust accordingly.
