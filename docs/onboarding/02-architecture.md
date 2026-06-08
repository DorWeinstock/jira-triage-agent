# Architecture

## System Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Intel/Habana Jira  (jira.devtools.intel.com)                           │
│  Project: GAUDISW  Component: DevOps_K8S                                │
└───────────────────────────┬─────────────────────────────────────────────┘
                            │  polls every 3m
                            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  jira-agent  (Go monolith, port 8080)                                   │
│                                                                         │
│  • Jira poller — JQL query, label lifecycle management                  │
│  • POST /investigate  → langgraph-agent                                 │
│  • GET  /hitl/pending — register tickets awaiting approval              │
│  • POST /hitl/resume  — resume workflow after human approval            │
│  • MCP /mcp/jira      — Jira tools (read ticket, search, add comment)  │
│  • MCP /mcp/k8s       — K8s tools (kubectl operations, multi-cluster)  │
│  • MCP /mcp/jenkins   — Jenkins tools (optional)                        │
└───────────┬───────────────────────────┬─────────────────────────────────┘
            │  HTTP POST /investigate   │  MCP SSE tool calls
            ▼                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  langgraph-agent  (Python FastAPI, port 8000)                           │
│                                                                         │
│  • FastAPI server — /investigate, /health, /hitl/approve, /hitl/reject │
│  • LangGraph StateGraph — multi-agent workflow                          │
│  • K8sConfigMapSaver — checkpoint persistence in ConfigMap              │
│  • RemediationLockService — in-memory lock (POC, single-pod only)       │
└─────────────────────────────────────────────────────────────────────────┘
            │  vLLM API (OpenAI-compatible)
            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  vllm-service  (Gaudi2 hardware)                                        │
│  Model: meta-llama/Llama-3.1-70B-Instruct                               │
└─────────────────────────────────────────────────────────────────────────┘
```

## LangGraph Workflow

### Full Node Graph

```
initialize
    │
read_ticket  ────────────────────────────────────────────┐
    │                                                     │ (no namespace)
    │ (has resources + namespace)                         ▼
    │ [if Jenkins configured]                 skip_investigation_no_namespace
investigate_jenkins                                       │
    │                                                     ▼
investigate_cluster          (no resources         END
    │                         but has context)
    │                              │
    │◄─────────────────────────────┘ synthesize_from_context
    │                                         │
diagnose                                      │
    │                                         ▼
    │ (HITL=true)                        post_comment ──► END
prepare_hitl                                  ▲
    │                                         │ (low confidence or max attempts)
    ▼                                         │
attempt_remediation ──────────────────────────┘
    │                         │
    │ (success)          (failed, retries left)
    ▼                         │
verify_fix              increment_remediation
    │                         │
    │ (resolved,              ▼
    │  no new issues)      diagnose (loop back)
    │
    ▼
post_comment ──► END
```

### Routing Logic

| From | Condition | To |
|------|-----------|-----|
| `read_ticket` | no `ticket_id` | `post_comment` |
| `read_ticket` | no `namespace` | `skip_investigation_no_namespace` |
| `read_ticket` | has resources + namespace, Jenkins configured | `investigate_jenkins` |
| `read_ticket` | has resources + namespace, no Jenkins | `investigate_cluster` |
| `read_ticket` | no K8s resources but has symptoms/similar tickets | `synthesize_from_context` |
| `diagnose` | confidence=Low OR max attempts reached | `post_comment` |
| `diagnose` | HITL enabled, has action | `prepare_hitl` |
| `diagnose` | HITL disabled, has action | `attempt_remediation` |
| `prepare_hitl` | no action | `post_comment` |
| `prepare_hitl` | has action | `attempt_remediation` (then pauses for HITL) |
| `attempt_remediation` | HITL rejected | `post_comment` |
| `attempt_remediation` | remediation not attempted | `post_comment` |
| `attempt_remediation` | success | `verify_fix` |
| `attempt_remediation` | failed, retries left | `increment_remediation` |
| `verify_fix` | resolved, no new issues | `post_comment` |
| `verify_fix` | resolved, new issues, loops left | `increment_remediation` |
| `increment_remediation` | always | `diagnose` |

### HITL Flow in Detail

1. `diagnose` produces `recommended_action` and `remediation_plan`
2. `prepare_hitl` posts an approval comment to Jira with the proposed fix
3. LangGraph pauses at `interrupt_before=["attempt_remediation"]`
4. The Go service is notified via `POST /hitl/pending`
5. Go polls Jira comments every 2 minutes looking for "approved"/"rejected"
6. On approval: Go calls `POST /investigate` with `thread_id` + `hitl_diagnosis_approved=true`
7. LangGraph resumes from checkpoint, `attempt_remediation` runs
8. On rejection: Go calls with `hitl_diagnosis_approved=false`, workflow routes to `post_comment`

## Specialized Agents

### JiraAgent (`agents/jira_agent.py`)

**Runs twice:** once as `read_ticket` node (reads + searches history), once as `post_comment` node.

`read_ticket` does:
- Fetches full ticket text via Jira MCP
- Extracts: namespace, target cluster, affected K8s resources, symptoms, error messages, Jenkins URLs
- Calls `HistoryAgent.search()` internally to find similar past tickets
- Sets `state["target_cluster"]` — controls which cluster all subsequent agents target

`post_comment` does:
- Formats diagnosis, remediation result, verification evidence into a structured Jira comment
- Applies Jira transitions: In Progress (transition 31) during investigation, In Review (transition 41) after verified fix

**HistoryAgent** is not a graph node. It is a helper class instantiated inside `JiraAgent.__init__()` and called inside `read_ticket`. It uses JQL to search Jira for tickets with similar components/labels, then re-ranks results with an LLM.

### K8sInvestigator (`agents/k8s_investigator.py`)

Receives **read-only** K8s tools. Cannot modify cluster state.

Investigation sequence:
1. Get deployment/pod status for all resources in `affected_resources`
2. Fetch logs for up to `max_pods_to_log` (default: 3) pods
3. Get events for the namespace
4. Deduplicate log lines (fuzzy dedup, threshold 85) to reduce token usage
5. Write all findings into `state["cluster_findings"]`

### Diagnostician (`agents/diagnostician.py`)

Synthesizes `cluster_findings` + `similar_tickets` + `past_resolutions` into:
- `root_cause` (string)
- `recommended_action` (string, for HITL display)
- `remediation_plan` (structured dict with tool calls)
- `confidence_level` (High/Medium/Low)
- `preventive_measures` (list)

A `confidence_level` of Low skips remediation entirely.

### K8sRemediationExecutor (`agents/k8s_remediation_executor.py`)

The **only** agent with write-capable K8s tools.

Receives the `remediation_plan` from state and executes it step by step. Each step is a K8s MCP tool call (e.g., `kubectl_apply`, `kubectl_scale`, `kubectl_set_image`). Acquires the `RemediationLockService` lock before executing to prevent concurrent conflicting changes.

### JenkinsInvestigator (`agents/jenkins_investigator.py`)

**Optional** — only registered as a graph node when `JENKINS_MCP_ENDPOINT` is set in the Python environment and `JENKINS_USERNAME`/`JENKINS_API_TOKEN` are set in the Go environment.

Extracts Jenkins build URLs from the ticket, fetches console logs (up to 100KB), and writes `state["jenkins_findings"]`.

## MCP Tool Architecture

MCP (Model Context Protocol) is used as the interface between the Python agents and the Go-implemented tools. This decouples the AI logic from the infrastructure-specific code.

```
Python agent                  Go MCP server
──────────────                ─────────────
K8sTools.call_tool("kubectl_get", ...) 
    → SSE POST /mcp/k8s
                              ← SSE response with tool result
```

**Jira MCP tools** (`/mcp/jira`): `get_ticket`, `search_tickets`, `add_comment`, `move_to_in_progress`, `move_to_in_review`

**K8s MCP tools** (`/mcp/k8s` and `/mcp/k8s/{cluster}`): `kubectl_get`, `kubectl_describe`, `kubectl_logs`, `kubectl_events`, `kubectl_apply`, `kubectl_scale`, `kubectl_set_image`, `kubectl_rollout_restart`

**Multi-cluster routing:** K8s MCP routes by URL path. The Go server registers one MCP handler per cluster: `saas` is the in-cluster kubeconfig, `hldc02`/`hldc03` use kubeconfig files mounted from a Kubernetes Secret.

## State Model (`state.py`)

`AgentState` extends LangGraph's `MessagesState`. Key field groups:

| Group | Fields |
|-------|--------|
| Jira context | `ticket_id`, `ticket_summary`, `ticket_description`, `ticket_labels`, `ticket_priority`, `ticket_components` |
| Multi-cluster | `target_cluster` — values: `"hldc02"`, `"hldc03"`, `"saas"` |
| Resources | `affected_resources` (dict of lists by resource type), `namespace`, `symptoms`, `error_messages` |
| History | `similar_tickets`, `past_resolutions` |
| Jenkins | `jenkins_urls`, `jenkins_findings` |
| Investigation | `cluster_findings` |
| Diagnosis | `root_cause`, `recommended_action`, `remediation_plan`, `confidence_level`, `preventive_measures` |
| Remediation tracking | `remediation_count`, `remediation_history`, `remediation_result`, `remediation_attempted`, `issue_resolved`, `verification_evidence` |
| Post-fix loop | `remediation_loop_count`, `max_remediation_loops`, `new_issues_detected`, `new_issues` |
| HITL | `hitl_diagnosis_approved`, `hitl_rejection_reason`, `hitl_requested_at`, `action_risk_level` |
| Workflow | `thread_id`, `resumed_from_checkpoint` |

## Checkpointing

The `K8sConfigMapSaver` stores LangGraph checkpoint state in a Kubernetes ConfigMap (`agent-checkpoints` in namespace `jira-k8s-agent`). Each workflow run is identified by `thread_id` (the Jira ticket ID).

**Limits and caveats:**
- ConfigMap max size is ~1MB; the saver enforces a soft 900KB limit per entry
- TTL: 24 hours (`CHECKPOINT_TTL_SECONDS=86400`)
- On resume from checkpoint, `initialize` clears stale cluster data (findings, diagnosis, remediation state) to force a fresh investigation

## Security Model

| Agent | K8s Tool Access |
|-------|-----------------|
| K8sInvestigator | `K8sTools(readonly=True)` — MCP server rejects write tools |
| VerificationService | `K8sTools(readonly=True)` |
| K8sRemediationExecutor | `K8sTools()` — full read+write access |

Read-only enforcement is at the MCP server level in Go, not just in the Python client. The Go MCP server checks the tool name against an allowlist before executing.

Protected namespaces (all write operations rejected): `kube-system`, `kube-public`, `kube-node-lease`.

## Jira Label Lifecycle

The Go poller tracks investigation state via Jira labels:

```
(new ticket) → ai-investigate
                   ↓  (poller picks it up)
           ai-investigate-in-progress
                   ↓  (investigation complete)
           ai-agent-investigated
```

The poller only processes tickets with label `ai-investigate` and skips those already `ai-investigate-in-progress` or `ai-agent-investigated`.
