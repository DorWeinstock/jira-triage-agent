# Local LLM Deployment - Quick Start Guide

## Summary of Changes

✅ **Removed all Gemini dependencies** - system now uses only local vLLM
✅ **Consolidated to single namespace** - vLLM runs in `jira-k8s-agent` namespace
✅ **Integrated with Vault** - using ExternalSecret pattern (same as other infra)
✅ **Updated all endpoints** - simplified to `http://vllm-service:8000/v1`

## Pre-Deployment: Add Secrets to Vault

**REQUIRED: Add these 4 secrets to Vault before deploying:**

```bash
vault kv put internal/jira-k8s-agent \
  jira-url="https://jira.devtools.intel.com" \
  jira-email="your-email@intel.com" \
  jira-api-token="YOUR_JIRA_API_TOKEN" \
  huggingface-token="YOUR_HF_TOKEN"
```

**Get tokens:**
- **Jira API token**: https://id.atlassian.com/manage-profile/security/api-tokens
- **HuggingFace token**: https://huggingface.co/settings/tokens (Read access)

See [vault-setup.md](./vault-setup.md) for detailed instructions.

## Deployment Steps

### 1. Verify Vault Secrets

```bash
# Check secrets exist in Vault
vault kv get internal/jira-k8s-agent

# Should show all 4 keys
```

### 2. Deploy vLLM and Updated Agents

```bash
cd /home/anoah/habana/habana-internal/jira-jenkins-agent

# Deploy ExternalSecret (syncs from Vault)
kubectl apply -k deploy/base/ --kubeconfig ~/.kube/config-saas

# Verify secret was created
kubectl get secret jira-k8s-agent-secret -n jira-k8s-agent --kubeconfig ~/.kube/config-saas
kubectl describe secret jira-k8s-agent-secret -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Should show keys: jira-url, jira-email, jira-api-token, huggingface-token, jenkins-username, jenkins-api-token, langchain-api-key
```

### 3. Deploy vLLM

```bash
# Deploy vLLM to jira-k8s-agent namespace
kubectl apply -k deploy/base/llm-serving/ --kubeconfig ~/.kube/config-saas

# Watch vLLM pod start (takes 5-10 min to download 20GB model)
kubectl get pods -n jira-k8s-agent -w --kubeconfig ~/.kube/config-saas

# Check logs
kubectl logs -f -n jira-k8s-agent -l app=vllm-qwen32b --kubeconfig ~/.kube/config-saas
```

### 4. Test vLLM Endpoint

```bash
# Port-forward
kubectl port-forward -n jira-k8s-agent svc/vllm-service 8000:8000 --kubeconfig ~/.kube/config-saas

# Test (in another terminal)
curl http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-32B-Instruct-AWQ",
    "prompt": "What is Kubernetes?",
    "max_tokens": 100
  }'
```

### 5. Rebuild and Deploy LangGraph Agent

```bash
# Rebuild with new dependencies (langchain-openai)
make build-langgraph-docker

# Deploy updated agent
kubectl apply -k deploy/base/langgraph-agent/ --kubeconfig ~/.kube/config-saas
kubectl rollout restart deployment/langgraph-agent -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Watch logs for vLLM connection
kubectl logs -f -l app=langgraph-agent -n jira-k8s-agent --kubeconfig ~/.kube/config-saas
```

Look for:
```
INFO:root:Creating LLM: Qwen/Qwen3-32B-Instruct-AWQ at http://vllm-service:8000/v1
```

## Verification Checklist

- [ ] Vault has all secrets (`vault kv get internal/jira-k8s-agent`)
- [ ] ExternalSecret is synced (`kubectl get externalsecret -n jira-k8s-agent`)
- [ ] jira-k8s-agent-secret exists with 7 keys
- [ ] vLLM pod is Running
- [ ] vLLM health endpoint works (`curl http://localhost:8000/health`)
- [ ] vLLM completion works (test above)
- [ ] langgraph-agent logs show vLLM endpoint
- [ ] No Gemini API calls in logs

## Architecture

```
┌─────────────────────┐
│   Vault (Secrets)   │
│  - jira-url         │
│  - jira-email       │
│  - jira-api-token   │
│  - huggingface-token│
└──────────┬──────────┘
           │
           ▼
┌─────────────────────────────┐
│  ExternalSecret (Syncer)    │
│  Refreshes every 1 hour     │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│  Secret: jira-k8s-agent-secret      │
│  namespace: jira-k8s-agent          │
└──────────┬──────────────────────────┘
           │
           ├─────────────────┐
           ▼                 ▼
┌──────────────────┐  ┌─────────────────┐
│   jira-agent     │  │  vllm-qwen32b   │
│  (Go monolith)   │  │  (Model server) │
└──────────────────┘  └─────────┬───────┘
                                │
                                ▼
                      ┌─────────────────┐
                      │ langgraph-agent │
                      │ (AI workflows)  │
                      └─────────────────┘
```

## Performance Expectations

### Current (CPU):
- Inference: 30-60 seconds per call
- Throughput: ~1-2 requests/minute
- Status: ⚠️ Testing only

### With GPU (A10G 24GB):
- Inference: 2-5 seconds per call
- Throughput: ~10-15 requests/minute
- Status: ✅ Production ready

## Troubleshooting

### ExternalSecret not syncing

```bash
kubectl describe externalsecret jira-k8s-agent-external-secret -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Check for errors in status.conditions
# Common: Vault path doesn't exist or missing properties
```

### vLLM OOMKilled

Increase memory limit in `deploy/base/llm-serving/vllm-deployment.yaml`:
```yaml
resources:
  limits:
    memory: "48Gi"  # Increase from 32Gi
```

### Connection refused from langgraph-agent

```bash
# Test connectivity
kubectl exec -n jira-k8s-agent deployment/langgraph-agent --kubeconfig ~/.kube/config-saas -- \
  curl -v http://vllm-service:8000/health
```

## Cost Comparison

| Setup | Infra Cost/mo | Per Investigation | Latency |
|-------|--------------|-------------------|---------|
| **Gemini (before)** | $0 | ~$0.50 | 5-8s |
| **vLLM CPU (now)** | ~$200 | ~$0.01 | 30-60s |
| **vLLM GPU (future)** | ~$800 (A10G) | ~$0.05 | 2-5s |

## Next Steps

1. ✅ Add secrets to Vault
2. ✅ Deploy system
3. ⏳ Test with real Jira tickets
4. ⏳ Monitor performance and errors
5. ⏳ Add GPU node for production speed

## Files Modified

```
✅ deploy/base/jira-agent/externalsecret.yaml (NEW - Vault integration)
✅ deploy/base/jira-agent/kustomization.yaml (added externalsecret.yaml)
✅ deploy/base/llm-serving/vllm-deployment.yaml (namespace: jira-k8s-agent)
✅ deploy/base/langgraph-agent/deployment.yaml (simplified endpoint)
✅ langgraph-agent/src/config.py (vLLM only, no Gemini)
✅ langgraph-agent/pyproject.toml (added langchain-openai)
```

## Documentation

- [vault-setup.md](./vault-setup.md) - Detailed Vault instructions
- [local-llm-deployment.md](./local-llm-deployment.md) - Full implementation details
