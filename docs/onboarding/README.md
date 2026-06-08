# Developer Onboarding

Welcome to the **jira-jenkins-agent** project. This guide helps you get productive as quickly as possible, regardless of whether your background is Go or Python.

## Reading Order

| # | File | What You'll Learn |
|---|------|-------------------|
| 1 | [01-overview.md](./01-overview.md) | What the system does and why it's built this way |
| 2 | [02-architecture.md](./02-architecture.md) | All components, the LangGraph workflow, and data flow |
| 3 | [03-directory-guide.md](./03-directory-guide.md) | Every file that matters and what it owns |
| 4 | [04-local-dev.md](./04-local-dev.md) | Getting the system running locally, step by step |
| 5 | [05-testing.md](./05-testing.md) | How to run and write tests |
| 6 | [06-extension-guide.md](./06-extension-guide.md) | Adding agents, MCP tools, or workflow nodes |
| 7 | [07-config-reference.md](./07-config-reference.md) | Every environment variable and Kubernetes secret |

## Quick Orientation

**Two services, one system:**

- `jira-agent` (Go) — polls Jira, exposes MCP endpoints, dispatches HTTP requests
- `langgraph-agent` (Python) — receives requests, runs the multi-agent AI workflow

**The workflow in one line:**
Jira ticket → Go polls → Python investigates cluster → LLM diagnoses → human approves → Python remediates → Jira comment posted.

**LLM stack:** Local vLLM server (`http://vllm-service:8000/v1`) running `meta-llama/Llama-3.1-70B-Instruct` on Gaudi2 hardware. No external API calls for inference.

## Background Reading by Role

**Coming from Go (new to Python/LangGraph):**
Read overview → architecture → `langgraph-agent/src/supervisor.py` → extension guide.

**Coming from Python (new to Go):**
Read overview → architecture → `cmd/jira-agent/main.go` → `pkg/poller/poller.go`.

**Coming from neither (new to both):**
Read all files in order. Estimated time: 2–3 hours.

## Active Work

There is an in-progress migration from module-level config constants to `get_settings()`. See [MIGRATION_GUIDE.md](../../MIGRATION_GUIDE.md) for context and status (Phase 3 pending).
