# Jira-to-Kubernetes Multi-Agent AI System

An intelligent LangGraph-based multi-agent system that automatically investigates and resolves Kubernetes issues reported in Jira tickets.

## What This Does

When a Jira ticket is created about a K8s issue, a **supervisor agent** orchestrates specialized agents that collaborate to investigate and resolve the problem:

1. **JiraAgent** - Reads the Jira ticket, searches for similar past issues, and posts results
2. **K8sInvestigator** - Systematically debugs the Kubernetes cluster
3. **Diagnostician** - Synthesizes findings and generates a remediation plan
4. **K8sRemediationExecutor** - Applies fixes with human approval (HITL)
5. **JenkinsInvestigator** *(optional)* - Investigates CI/CD build failures

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Jira                                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                           Polls for new tickets
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       jira-agent (Go Monolith)                          │
│                                                                         │
│  • Polls Jira for tickets matching criteria                            │
│  • Exposes MCP endpoints (/mcp/jira, /mcp/k8s)                        │
│  • Dispatches investigations to LangGraph agent                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                        HTTP POST /investigate
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  LangGraph Multi-Agent System (Python)                  │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐ │
│  │                    LangGraph Supervisor                          │ │
│  │                                                                  │ │
  │  │  ┌──────────┐    ┌──────────┐    ┌──────────┐                 │ │
  │  │  │  Jira    │ -> │   K8s    │ -> │Diagnos-  │                 │ │
  │  │  │  Agent   │    │Investigator   │tician    │                 │ │
  │  │  │(+history)│    └──────────┘    └──────────┘                 │ │
  │  │  └──────────┘                         │                        │ │
  │  │                                        ▼                        │ │
  │  │                                  ┌──────────┐                  │ │
  │  │                             HITL │  K8s     │                  │ │
  │  │                           pause→ │Remediation                  │ │
  │  │                                  │Executor  │                  │ │
  │  │                                  └──────────┘                  │ │
  │  │                                        │                        │ │
  │  │                                        ▼                        │ │
  │  │                                  ┌──────────┐                  │ │
  │  │                                  │  Jira    │                  │ │
  │  │                                  │  Agent   │                  │ │
  │  │                                  │(comment) │                  │ │
  │  │                                  └──────────┘                  │ │
  │  └──────────────────────────────────────────────────────────────────┘ │
  │                                                                         │
  │  Tools: Calls jira-agent MCP endpoints for Jira/K8s operations        │
  │  LLM: vLLM (meta-llama/Llama-3.1-70B-Instruct on Gaudi2)             │
└─────────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

- **jira-agent (Go)**: Monolith service - Jira polling, MCP servers, HTTP dispatch
- **LangGraph (Python)**: Multi-agent workflow orchestration
- **MCP (Model Context Protocol)**: Tool integration for Jira/K8s
- **vLLM + Llama-3.1-70B-Instruct**: LLM running locally on Gaudi2 hardware
- **LangSmith**: Observability and tracing

## Quick Start

### Prerequisites

- `kubectl` and `kind` installed
- Docker for building images
- Access to Habana vLLM service (or configure `VLLM_ENDPOINT` to point to your LLM)

### Step 1: Create Kind Cluster

```bash
# Run quick start script
./hack/scripts/quick-start.sh

# Or manually:
kind create cluster --name jira-agent-test
```

### Step 2: Create Secrets

```bash
# Create the unified agent secret
kubectl create secret generic jira-k8s-agent-secret \
  --from-literal=jira-url=https://jira.devtools.intel.com \
  --from-literal=jira-email=your-email@habana-labs.com \
  --from-literal=jira-api-token=ATATT3xFfGF0... \
  --from-literal=jenkins-username=your-jenkins-user \
  --from-literal=jenkins-api-token=your-jenkins-token \
  --from-literal=langchain-api-key=YOUR_LANGSMITH_KEY \
  -n jira-k8s-agent
```

### Step 3: Build and Deploy

```bash
# Build all images
make build-all

# Load to kind cluster
make kind-load-all

# Deploy
make deploy-all

# Check status
make status
```

### Step 4: Test

```bash
# Run E2E test
make e2e-test

# View logs
make logs-jira-agent
make logs-langgraph
```

## Testing

### Quick E2E Test

```bash
make e2e-full
```

This will:
1. Setup kind cluster
2. Build and deploy both services
3. Run health checks
4. Show logs

### Manual Testing

```bash
# Setup and deploy
make e2e-setup
make e2e-deploy

# Run test
make e2e-test

# Cleanup
make e2e-cleanup
```

## Repository Structure

```
jira-jenkins-agent/
├── README.md                      # This file
├── CLAUDE.md                      # Development guide
├── Makefile                       # Build automation
├── Dockerfile                     # Go monolith container
├── go.mod                         # Go module
│
├── cmd/                           # Go entrypoints
│   └── jira-agent/               # Main Go monolith
│
├── pkg/                           # Go shared packages
│   ├── mcp/                      # MCP server implementations
│   │   ├── jira/                 # Jira MCP tools
│   │   └── k8s/                  # K8s MCP tools
│   └── poller/                   # Jira polling logic
│
├── langgraph-agent/               # Python LangGraph agent
│   ├── src/
│   │   ├── agents/               # 4 specialized agents
│   │   ├── tools/                # MCP tool clients
│   │   ├── supervisor.py         # LangGraph workflow
│   │   └── main.py               # FastAPI entry point
│   ├── Dockerfile
│   └── pyproject.toml
│
├── tests/                         # Centralized tests
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── deploy/                        # K8s manifests
│   └── base/
│       ├── jira-agent/
│       └── langgraph-agent/
│
├── docs/                          # Documentation
│
└── hack/                          # Dev scripts
    └── scripts/
```

## How It Works

### 1. Jira Polling (Go)

The jira-agent polls Jira for new tickets matching criteria:

```go
filter := &TicketFilter{
    Projects:    []string{"GAUDISW"},
    Components:  []string{"DevOps_K8S"},
    IssueTypes:  []string{"Bug"},
}
```

### 2. HTTP-Based Triggering

When a ticket matches, jira-agent POSTs to langgraph-agent:

```
POST http://langgraph-agent-service:8000/investigate
{
  "ticket_id": "GAUDISW-123",
  "summary": "Pod crashing in production"
}
```

### 3. Multi-Agent Workflow (Python + LangGraph)

The supervisor orchestrates specialized agents with a remediation loop:

```python
workflow = StateGraph(AgentState)
workflow.add_node("initialize", initialize_state)
workflow.add_node("read_ticket", jira_agent.read_ticket)   # includes history search
workflow.add_node("investigate_cluster", investigate_cluster_wrapper)
workflow.add_node("diagnose", diagnose_wrapper)
workflow.add_node("prepare_hitl", prepare_hitl)            # optional: HITL flow
workflow.add_node("attempt_remediation", attempt_remediation_node)
workflow.add_node("verify_fix", verify_fix_wrapper)
workflow.add_node("increment_remediation", increment_remediation_count)
workflow.add_node("post_comment", jira_agent.post_comment)
```

### 4. MCP Tool Integration

Agents call jira-agent's MCP endpoints:
- **Jira MCP** (`/mcp/jira`): get_ticket, search_tickets, add_comment
- **K8s MCP** (`/mcp/k8s`): kubectl operations (get, describe, logs, events)

## Safety & Guardrails

The system operates under strict safety controls:

**Allowed Operations**
- Reading pod logs and events
- Describing resources
- Viewing metrics

**Restricted (Manual Approval)**
- Restarting pods (max 3 at a time)
- Applying configuration changes

**Blocked**
- Deleting namespaces or persistent volumes
- Accessing `kube-system` namespace
- Destructive system commands

## Observability

### LangSmith Tracing

All agent executions are traced in LangSmith:
1. Visit https://smith.langchain.com
2. Select your project
3. View detailed agent-by-agent execution trace

### Kubernetes Monitoring

```bash
# View pods
kubectl get pods -n jira-k8s-agent

# Agent logs
kubectl logs -l app=jira-agent -n jira-k8s-agent -f
kubectl logs -l app=langgraph-agent -n jira-k8s-agent -f
```

## Testing Scenarios

### CrashLoopBackOff

```bash
kubectl run crashpod --image=busybox --restart=Always -- sh -c "exit 1"
```

### ImagePullBackOff

```bash
kubectl run imagepull --image=nonexistent/image:v1
```

## Example Investigation

### Input: Jira Ticket

```
Title: api-server pods crashing in production
Description: Users reporting 502 errors. Pods restarting continuously.
```

### Agent Workflow

1. **JiraAgent**: Reads ticket, extracts K8s resources, namespace, and searches similar past tickets
2. **K8sInvestigator**: Checks pod status, logs, events on the target cluster
3. **Diagnostician**: Root cause analysis, generates remediation plan
4. **K8sRemediationExecutor**: Applies fix after human approval (HITL)
5. **JiraAgent**: Posts findings and remediation results to ticket

### Output: Jira Comment

```markdown
## AI Agent Investigation Results

### Root Cause Analysis

OutOfMemory error due to insufficient memory allocation (256Mi)
for Java application with heap requirements.

### Recommended Action

Increase memory limit in deployment to 512Mi:
kubectl set resources deployment api-server --limits=memory=512Mi

### Confidence Level

High - Clear OOM pattern, matches 3 similar resolved incidents
```

## Documentation

- **[CLAUDE.md](./CLAUDE.md)** - Complete development guide
- **[docs/](./docs/)** - Architecture and deployment docs

## Contributing

**To extend:**

1. Add new specialized agents in `langgraph-agent/src/agents/`
2. Add new MCP tools in `pkg/mcp/`
3. Modify workflow graph in `langgraph-agent/src/supervisor.py`

## License

See [LICENSE](./LICENSE) file.

## Acknowledgments

- **LangGraph** - LangChain team
- **MCP** - Anthropic's Model Context Protocol
- **vLLM** - Efficient LLM serving on Gaudi2

---

**Ready to start?** Run `./hack/scripts/quick-start.sh` to set up your environment!

For detailed instructions, see [CLAUDE.md](./CLAUDE.md)
