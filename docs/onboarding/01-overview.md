# Overview

## What This System Does

The jira-jenkins-agent watches a Jira project for new tickets reporting Kubernetes problems, automatically investigates those problems in the real cluster, and either posts a diagnosis or applies a fix, with human approval required before any write operations.

**Concrete example:**

1. An engineer opens Jira ticket `GAUDISW-1234`: _"api-server pods crashing in hldc02 namespace ml-training"_
2. The system detects the ticket within 3 minutes (polling interval).
3. An AI agent reads the ticket, recognizes `hldc02` as the target cluster, and `ml-training` as the namespace.
4. A second agent checks pod status, logs, and events on `hldc02`.
5. A third agent diagnoses: _OOMKilled, memory limit 256Mi too low for this workload._
6. The system posts an approval comment to the Jira ticket: _"Proposed fix: increase memory limit to 512Mi. Approve?"_
7. An engineer comments "approved" on the ticket.
8. The system applies the fix and verifies that pods stabilize.
9. A final comment is posted with the remediation result.

## Problem It Solves

Habana Gaudi2 training clusters run dozens of ML workloads. When things break, engineers spend time on repetitive diagnostics: `kubectl describe pod`, `kubectl logs`, searching Jira for similar past incidents, writing up root causes. This system automates that loop.

## Design Principles

**Least-privilege write access.** Investigation agents (K8sInvestigator, VerificationService) receive read-only Kubernetes tool access. Only K8sRemediationExecutor gets write tools, and only after human approval.

**Human-in-the-loop (HITL) by default.** `HITL_ENABLED=true` (the default). Before any remediation action, the system pauses the LangGraph workflow, posts an approval comment to Jira, and waits. The Go service polls Jira comments for an "approved" or "rejected" keyword.

**Local LLM, no cloud inference.** All AI reasoning uses a vLLM server running `meta-llama/Llama-3.1-70B-Instruct` inside the cluster (`http://vllm-service:8000/v1`). No data leaves the on-premise environment for LLM calls.

**Stateful remediation loop.** If a fix doesn't work the first time, the system re-investigates, re-diagnoses, and retries — up to `MAX_REMEDIATION_ATTEMPTS` (default: 2 retries). Post-fix, it checks for new issues introduced by the remediation and loops again if needed (up to `max_remediation_loops`, default: 3).

**Multi-cluster routing.** A single deployment can target multiple clusters (`hldc02`, `hldc03`, `saas`). The LangGraph agent extracts the target cluster from the ticket text and routes K8s tool calls to the correct MCP endpoint.

## Scope and Limitations

**In scope:**

- Tickets in Jira project `GAUDISW` with component `DevOps_K8S`
- Kubernetes issues: pod crashes, image pull failures, OOM, pending deployments, config errors
- Jenkins build failures (when Jenkins credentials are configured)
- Clusters: `hldc02`, `hldc03`, `saas`

**Out of scope:**

- Tickets without a recognizable namespace in the description (the system posts an error comment and stops)
- Changes to `kube-system`, `kube-public`, `kube-node-lease` namespaces (blocked at tool level)
- Deleting PVCs, PVs, namespaces, or cluster-scoped resources
- Multi-tenant or multi-org Jira projects

## Technology Choices

| Component             | Choice                       | Reason                                                             |
| --------------------- | ---------------------------- | ------------------------------------------------------------------ |
| Go monolith           | Go                           | Efficient polling loop, low memory overhead for always-on service  |
| Multi-agent framework | LangGraph                    | Native support for stateful graphs, checkpointing, HITL interrupts |
| LLM                   | vLLM/Llama-3.1-70B on Gaudi2 | On-prem, no data egress, uses available Gaudi hardware             |
| Tool protocol         | MCP (Model Context Protocol) | Decouples tool implementation (Go) from agent logic (Python)       |
| Observability         | LangSmith                    | Per-agent step tracing for debugging multi-agent flows             |
| State persistence     | Kubernetes ConfigMap         | No external dependency; survives pod restarts within TTL window    |
