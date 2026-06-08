# Deploy jira-k8s-agent to SaaS Cluster - From Scratch

Complete deployment guide assuming nothing exists yet.

## Prerequisites

✅ Access to `~/.kube/config-saas` cluster
✅ Docker installed
✅ kubectl configured
✅ Vault access

## Step 1: Add Secrets to Vault

**REQUIRED FIRST - Do this before deploying anything**

```bash
# Login to Vault
vault login

# Add all 4 secrets to Vault at path: internal/jira-k8s-agent
vault kv put internal/jira-k8s-agent \
  jira-url="https://jira.devtools.intel.com" \
  jira-email="your-email@intel.com" \
  jira-api-token="YOUR_JIRA_API_TOKEN" \
  huggingface-token="YOUR_HF_TOKEN"

# Verify
vault kv get internal/jira-k8s-agent
```

**Get tokens:**
- Jira: https://id.atlassian.com/manage-profile/security/api-tokens
- HuggingFace: https://huggingface.co/settings/tokens (Read access)

## Step 2: Build Docker Images

```bash
cd /home/anoah/habana/habana-internal/jira-jenkins-agent

# Build both images using Makefile
make build-all
```

## Step 3: Push Images to Artifactory

The saas cluster needs to pull images from Artifactory. Push both images:

```bash
# Push both images
make push-all

# This runs:
#   docker push artifactory-kfs.habana-labs.com/docker-developers/users/anoah/jira-agent:latest
#   docker push artifactory-kfs.habana-labs.com/docker-developers/users/anoah/langgraph-agent:latest
```

**Note**: Images are already tagged correctly by `make build-all`.

## Step 4: Create Namespace

```bash
# Create namespace
kubectl create namespace jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Verify
kubectl get namespace jira-k8s-agent --kubeconfig ~/.kube/config-saas
```

## Step 5: Deploy Everything

```bash
cd /home/anoah/habana/habana-internal/jira-jenkins-agent

# Deploy all resources (includes ExternalSecret, RBAC, services, deployments)
kubectl apply -k deploy/base/ --kubeconfig ~/.kube/config-saas

# This deploys:
# - Namespace: jira-k8s-agent
# - ExternalSecret: syncs from Vault
# - RBAC: ServiceAccounts, Roles, RoleBindings
# - jira-agent: Go monolith
# - langgraph-agent: Python AI workflow
# - vllm-qwen32b: Local LLM server
```

## Step 6: Verify Deployment

### 6.1 Check ExternalSecret Synced

```bash
# Check ExternalSecret status
kubectl get externalsecret -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Should show:
# NAME                               STORE   REFRESH INTERVAL   STATUS         READY
# jira-k8s-agent-external-secret     vault   1h                 SecretSynced   True

# Verify Kubernetes secret was created
kubectl get secret jira-k8s-agent-secret -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Check secret has all keys
kubectl describe secret jira-k8s-agent-secret -n jira-k8s-agent --kubeconfig ~/.kube/config-saas
# Should show: jira-url, jira-email, jira-api-token, huggingface-token, jenkins-username, jenkins-api-token, langchain-api-key
```

**If ExternalSecret shows error:**
```bash
kubectl describe externalsecret jira-k8s-agent-external-secret -n jira-k8s-agent --kubeconfig ~/.kube/config-saas
# Check status.conditions for error details
# Common: Vault path doesn't exist - go back to Step 1
```

### 6.2 Check All Pods are Running

```bash
kubectl get pods -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Expected output:
# NAME                              READY   STATUS    RESTARTS   AGE
# jira-agent-xxx                    1/1     Running   0          2m
# langgraph-agent-xxx               1/1     Running   0          2m
# vllm-qwen32b-xxx                  1/1     Running   0          10m  # Takes 5-10min to download model
```

### 6.3 Check Services

```bash
kubectl get svc -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Expected:
# NAME                      TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)
# jira-agent                ClusterIP   10.x.x.x        <none>        8080/TCP
# langgraph-agent-service   ClusterIP   10.x.x.x        <none>        8000/TCP
# vllm-service              ClusterIP   10.x.x.x        <none>        8000/TCP
```

## Step 7: Test Each Component

### 7.1 Test jira-agent (Go monolith)

```bash
# Port-forward
kubectl port-forward -n jira-k8s-agent svc/jira-agent 8080:8080 --kubeconfig ~/.kube/config-saas &

# Test health
curl http://localhost:8080/health

# Expected: {"status":"ok"}
```

### 7.2 Test vLLM (Model server)

```bash
# Port-forward
kubectl port-forward -n jira-k8s-agent svc/vllm-service 8001:8000 --kubeconfig ~/.kube/config-saas &

# Test health
curl http://localhost:8001/health

# Test completion
curl http://localhost:8001/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-32B-Instruct-AWQ",
    "prompt": "What is Kubernetes?",
    "max_tokens": 50
  }'

# Expected: JSON response with generated text
# NOTE: On CPU this will take 30-60 seconds
```

### 7.3 Test langgraph-agent

```bash
# Port-forward
kubectl port-forward -n jira-k8s-agent svc/langgraph-agent-service 8002:8000 --kubeconfig ~/.kube/config-saas &

# Test health
curl http://localhost:8002/health

# Expected: {"status":"healthy"}
```

## Step 8: Monitor Logs

**Terminal 1 - jira-agent logs:**
```bash
kubectl logs -f -l app=jira-agent -n jira-k8s-agent --kubeconfig ~/.kube/config-saas
```

**Terminal 2 - langgraph-agent logs:**
```bash
kubectl logs -f -l app=langgraph-agent -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Look for:
# INFO:root:Creating LLM: Qwen/Qwen3-32B-Instruct-AWQ at http://vllm-service:8000/v1
```

**Terminal 3 - vLLM logs:**
```bash
kubectl logs -f -l app=vllm-qwen32b -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Watch for model download progress
```

## Step 9: End-to-End Test

**Trigger an investigation (once jira-agent is polling):**

The jira-agent will automatically poll Jira for tickets matching:
- Project: `GAUDISW`
- Component: `DevOps_K8S`
- Status: Open/In Progress

Or manually trigger:
```bash
kubectl port-forward -n jira-k8s-agent svc/jira-agent 8080:8080 --kubeconfig ~/.kube/config-saas

curl -X POST http://localhost:8080/investigate \
  -H "Content-Type: application/json" \
  -d '{"ticket_id": "GAUDISW-1234"}'
```

**Monitor the full workflow:**
```bash
# Watch all pods
kubectl get pods -n jira-k8s-agent -w --kubeconfig ~/.kube/config-saas

# Check LangSmith for traces: https://smith.langchain.com
```

## Troubleshooting

### ExternalSecret not syncing

```bash
kubectl describe externalsecret jira-k8s-agent-external-secret -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Common issues:
# 1. Vault path doesn't exist → Run Step 1
# 2. Missing properties in Vault → Check vault kv get jira-k8s-agent
# 3. ClusterSecretStore not accessible → Contact infra team
```

### Pods not starting

```bash
# Check pod status
kubectl describe pod -l app=jira-agent -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Common issues:
# 1. Image pull error → Check image exists and credentials
# 2. Secret not found → Check Step 6.1
# 3. Resource limits → Check node resources
```

### vLLM OOMKilled

```bash
# Increase memory limit
# Edit deploy/base/llm-serving/vllm-deployment.yaml:
# resources.limits.memory: "48Gi"  # Increase from 32Gi

# Redeploy
kubectl apply -k deploy/base/llm-serving/ --kubeconfig ~/.kube/config-saas
```

### Connection refused between services

```bash
# Test connectivity from one pod to another
kubectl exec -n jira-k8s-agent deployment/langgraph-agent --kubeconfig ~/.kube/config-saas -- \
  curl -v http://vllm-service:8000/health

# Should succeed if services are configured correctly
```

## Update/Redeploy

### Update Docker images

```bash
# Rebuild and push images
make build-all
make push-all

# Restart deployments to pull new images
kubectl rollout restart deployment/jira-agent -n jira-k8s-agent --kubeconfig ~/.kube/config-saas
kubectl rollout restart deployment/langgraph-agent -n jira-k8s-agent --kubeconfig ~/.kube/config-saas

# Wait for rollout to complete
kubectl rollout status deployment/jira-agent -n jira-k8s-agent --kubeconfig ~/.kube/config-saas
kubectl rollout status deployment/langgraph-agent -n jira-k8s-agent --kubeconfig ~/.kube/config-saas
```

### Update configuration

```bash
# Edit manifests in deploy/base/

# Apply changes
kubectl apply -k deploy/base/ --kubeconfig ~/.kube/config-saas
```

## Clean Up (if needed)

```bash
# Delete everything
kubectl delete namespace jira-k8s-agent --kubeconfig ~/.kube/config-saas

# This deletes:
# - All deployments
# - All services
# - All secrets (Vault data remains)
# - All ConfigMaps
# - All pods
```

## Security Configuration

### Remediation Permissions

The agent has **cluster-wide remediation permissions** with the following security controls:

#### Layer 1: RBAC (Kubernetes)
- **ClusterRole**: `jira-agent-k8s-remediator`
- **Scope**: Cluster-wide write access (pods, deployments, configmaps, secrets)
- **Binding**: ClusterRoleBinding to `jira-agent-sa` ServiceAccount

#### Layer 2: Namespace Allowlist (Application)
Configure which namespaces allow remediation via environment variable:

```bash
# Allow specific namespaces only
SECURITY_ALLOWED_REMEDIATION_NAMESPACES="production,staging,jira-k8s-agent"

# Or allow all namespaces (default)
SECURITY_ALLOWED_REMEDIATION_NAMESPACES="*"

# Protected namespaces (always blocked, even with "*")
SECURITY_PROTECTED_NAMESPACES="kube-system,kube-public,kube-node-lease"
```

**Default behavior**: All namespaces allowed except kube-system, kube-public, kube-node-lease

#### Layer 3: HITL Approval (Human-In-The-Loop)
- All remediation actions require human approval via Jira comment
- Workflow interrupts at `prepare_hitl` node
- Approval: Post comment "approve" or "lgtm" on Jira ticket
- Rejection: Post comment "reject" or "deny"

### Security Hardening TODO

⚠️ **The following hardening is recommended but NOT yet implemented:**

1. **Secure /approve endpoint**
   - Add HMAC signature verification
   - Verify approval came from authorized Jira user
   - See TODO in `langgraph-agent/src/server.py:318`

2. **Enable Kubernetes audit logging**
   - Track all actions by `jira-agent-sa` ServiceAccount
   - Alert on unauthorized namespace access attempts

3. **Restrict namespace allowlist**
   - Override default "*" with explicit namespace list
   - Add to deployment: `SECURITY_ALLOWED_REMEDIATION_NAMESPACES="production,staging"`

4. **Implement mutual TLS**
   - Between jira-agent and langgraph-agent pods
   - Prevents network-level attacks

## Architecture Deployed

```
┌─────────────────────┐
│   Vault (External)  │
│  - jira-url         │
│  - jira-email       │
│  - jira-api-token   │
│  - huggingface-token│
└──────────┬──────────┘
           │ External Secrets Operator
           ▼
┌──────────────────────────────────────────────┐
│        Namespace: jira-k8s-agent             │
│                                              │
│  ┌────────────────────────────────────┐     │
│  │ Secret: jira-k8s-agent-secret       │     │
│  │ (auto-synced from Vault every 1h)  │     │
│  └─────────┬──────────────────────────┘     │
│            │                                 │
│   ┌────────┼─────────┐                      │
│   ▼        ▼         ▼                      │
│  ┌───┐  ┌───┐    ┌──────┐                  │
│  │GO │  │AI │    │vLLM  │                  │
│  │   │→ │   │ ←→ │Model │                  │
│  └───┘  └───┘    └──────┘                  │
│  jira   lang      Qwen3                     │
│  agent  graph     32B                       │
│                                              │
└──────────────────────────────────────────────┘
```

## Deployment Checklist

- [ ] Secrets added to Vault at path `internal/jira-k8s-agent`
- [ ] Docker images built
- [ ] Images pushed to registry (if needed)
- [ ] Namespace created
- [ ] All manifests applied
- [ ] ExternalSecret shows SecretSynced
- [ ] jira-k8s-agent-secret exists with 7 keys
- [ ] All 3 pods are Running
- [ ] All 3 services exist
- [ ] jira-agent health check passes
- [ ] vLLM health check passes
- [ ] langgraph-agent health check passes
- [ ] langgraph-agent logs show vLLM endpoint
- [ ] No errors in pod logs

## Next Steps

1. ✅ Deploy system (this guide)
2. ⏳ Monitor first investigation
3. ⏳ Check LangSmith traces
4. ⏳ Verify Jira comments posted
5. ⏳ Add GPU for production performance
