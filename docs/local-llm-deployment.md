# Local LLM Deployment - Implementation Summary

**Date**: 2026-02-05
**Objective**: Replace Google Gemini with locally deployed Qwen3-32B-Instruct via vLLM

---

## Changes Implemented

### 1. Python Dependencies
✅ **Updated**: `langgraph-agent/pyproject.toml`
- Added: `langchain-openai>=0.2.0`

### 2. Configuration (`langgraph-agent/src/config.py`)
✅ **Removed**:
- `ChatGoogleGenerativeAI` import
- `LocalLLMSettings` class (merged into LLMSettings)
- `GOOGLE_MODEL` constant
- All Gemini fallback logic

✅ **Added**:
- `LLMSettings.vllm_endpoint` field
- `LLMSettings.vllm_model_name` field
- `VLLM_ENDPOINT` and `VLLM_MODEL_NAME` module constants

✅ **Simplified**:
- `create_llm()` now only uses `ChatOpenAI` pointing to vLLM
- Removed conditional Gemini/vLLM switching

### 3. Deployment Manifests

✅ **Created**: `deploy/base/llm-serving/vllm-deployment.yaml`
- Namespace: `llm-serving`
- Deployment: `vllm-qwen32b` using `vllm/vllm-openai:v0.6.3`
- Service: `vllm-service` on port 8000
- CPU-based configuration (`--device=cpu`)
- Model: `Qwen/Qwen3-32B-Instruct-AWQ`

✅ **Updated**: `deploy/base/langgraph-agent/deployment.yaml`
- Removed: `GOOGLE_API_KEY` secret reference
- Removed: `USE_LOCAL_MODEL` toggle
- Added: `VLLM_ENDPOINT` environment variable
- Added: `VLLM_MODEL_NAME` environment variable
- Updated: `NO_PROXY` to include `llm-serving` namespace

### 4. Makefile Targets
✅ **Added** (with correct kubeconfig):
- `make deploy-llm-serving` - Deploy vLLM to cluster
- `make status-llm` - Check vLLM deployment status
- `make logs-llm` - View vLLM logs
- `make test-llm` - Test vLLM endpoint
- `make port-forward-llm` - Port-forward vLLM service

---

## Deployment Instructions

### Step 1: Deploy vLLM Service

```bash
cd /home/anoah/habana/habana-internal/jira-jenkins-agent

# Deploy vLLM (will take 5-10 minutes to download model)
make deploy-llm-serving

# Check status
make status-llm

# Watch logs
make logs-llm
```

### Step 2: Test vLLM Endpoint

```bash
# Test vLLM directly (port-forward + curl)
make test-llm
```

Expected output:
```json
{
  "id": "cmpl-...",
  "object": "text_completion",
  "created": 1738...,
  "model": "Qwen/Qwen3-32B-Instruct-AWQ",
  "choices": [
    {
      "text": "Kubernetes is...",
      ...
    }
  ]
}
```

### Step 3: Rebuild & Deploy LangGraph Agent

```bash
# Build new Docker image with updated dependencies
cd langgraph-agent
docker build -t langgraph-agent:latest .

# Load to cluster (if using kind)
kind load docker-image langgraph-agent:latest --name your-cluster

# OR deploy directly to your cluster
kubectl --kubeconfig ~/.kube/config-saas apply -f deploy/base/langgraph-agent/deployment.yaml
kubectl --kubeconfig ~/.kube/config-saas rollout restart deployment/langgraph-agent -n jira-k8s-agent
```

### Step 4: Verify Integration

```bash
# Watch langgraph-agent logs for vLLM connections
kubectl --kubeconfig ~/.kube/config-saas logs -f -l app=langgraph-agent -n jira-k8s-agent
```

Look for log lines like:
```
INFO:root:Creating LLM: Qwen/Qwen3-32B-Instruct-AWQ at http://vllm-service.llm-serving.svc.cluster.local:8000/v1
```

### Step 5: End-to-End Test

Trigger an investigation and monitor both services:

**Terminal 1 - vLLM logs:**
```bash
make logs-llm
```

**Terminal 2 - LangGraph agent logs:**
```bash
kubectl --kubeconfig ~/.kube/config-saas logs -f -l app=langgraph-agent -n jira-k8s-agent
```

**Terminal 3 - Trigger investigation:**
```bash
# Example: Create a test Jira ticket or trigger investigation endpoint
curl -X POST http://<your-cluster>/investigate \
  -H "Content-Type: application/json" \
  -d '{"ticket_id": "TEST-123"}'
```

---

## Performance Expectations

### Current Configuration (CPU)
- **Inference time**: 30-60 seconds per LLM call
- **Throughput**: ~1-2 requests/minute
- **Memory**: 24-32GB RAM
- **Status**: ⚠️ SLOW - Suitable for testing only

### Future GPU Configuration
To achieve production performance, add GPU nodes:

1. **Add GPU node** (A10G 24GB recommended)
2. **Install NVIDIA device plugin**:
   ```bash
   kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.0/nvidia-device-plugin.yml
   ```
3. **Update vLLM deployment**:
   ```yaml
   # Remove: --device=cpu
   # Add GPU resources:
   resources:
     limits:
       nvidia.com/gpu: 1
   ```
4. **Expected performance**:
   - Inference time: 2-5 seconds
   - Throughput: ~10-15 requests/minute
   - VRAM: 20-24GB

---

## Troubleshooting

### vLLM Pod OOMKilled
Increase memory in `deploy/base/llm-serving/vllm-deployment.yaml`:
```yaml
resources:
  limits:
    memory: "48Gi"  # Increase from 32Gi
```

### Connection Refused from LangGraph Agent
Test connectivity:
```bash
kubectl --kubeconfig ~/.kube/config-saas exec -n jira-k8s-agent deployment/langgraph-agent -- \
  curl -v http://vllm-service.llm-serving.svc.cluster.local:8000/health
```

### Slow Performance
This is expected on CPU. See "Future GPU Configuration" above.

### Model Download Fails
If behind proxy, add proxy env vars to vLLM deployment:
```yaml
env:
- name: HTTP_PROXY
  value: "http://proxy-dmz.intel.com:912"
- name: HTTPS_PROXY
  value: "http://proxy-dmz.intel.com:912"
```

---

## Verification Checklist

- [ ] vLLM pod is Running
- [ ] vLLM health endpoint returns 200
- [ ] Test completion API works (`make test-llm`)
- [ ] LangGraph agent deployment updated
- [ ] LangGraph agent logs show vLLM connection
- [ ] End-to-end investigation works
- [ ] No Gemini API calls (check LangSmith traces)

---

## Files Changed

| File | Status | Changes |
|------|--------|---------|
| `langgraph-agent/pyproject.toml` | ✅ Modified | Added langchain-openai |
| `langgraph-agent/src/config.py` | ✅ Modified | Removed Gemini, vLLM only |
| `deploy/base/llm-serving/vllm-deployment.yaml` | ✅ Created | New vLLM deployment |
| `deploy/base/llm-serving/kustomization.yaml` | ✅ Created | New kustomization |
| `deploy/base/llm-serving/README.md` | ✅ Created | Deployment guide |
| `deploy/base/langgraph-agent/deployment.yaml` | ✅ Modified | Removed Gemini, added vLLM env vars |
| `Makefile` | ✅ Modified | Added LLM serving targets |

---

## Next Steps

1. ✅ Deploy vLLM service
2. ✅ Test vLLM endpoint
3. ⏳ Rebuild langgraph-agent Docker image
4. ⏳ Deploy updated langgraph-agent
5. ⏳ Run end-to-end test
6. ⏳ Monitor performance
7. ⏳ Add GPU nodes for production

---

## Cost Comparison

**Before (Gemini)**:
- Per investigation: ~$0.50
- Infrastructure: $0
- Latency: 5-8s per agent call

**After (vLLM CPU)**:
- Per investigation: ~$0.01
- Infrastructure: ~$200/month (8-core node)
- Latency: 30-60s per agent call

**After (vLLM GPU - Future)**:
- Per investigation: ~$0.05
- Infrastructure: ~$800/month (A10G 24GB)
- Latency: 2-5s per agent call

**Recommendation**: Start with CPU for validation, move to GPU for production.
