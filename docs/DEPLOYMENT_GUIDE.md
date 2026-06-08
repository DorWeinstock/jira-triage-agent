# Deploy Multi-Cluster Solution from Scratch on saas-cluster

Complete step-by-step guide to deploy the jira-k8s-agent with multi-cluster support.

---

## Prerequisites Checklist

### Required Access
- ✅ Access to saas Kubernetes cluster (kubectl configured)
- ✅ Access to Vault (vault CLI configured and authenticated)
- ✅ Kubeconfig files for hldc02 and hldc03 clusters
- ✅ Jira API credentials (URL, email, API token)
- ✅ Google AI API key (for Gemini 2.5 Flash)
- ✅ LangSmith API key (optional, for observability)
- ✅ Docker registry access (artifactory-kfs.habana-labs.com)

### Verify Tools
```bash
kubectl version --client
vault version
docker version
make --version
```

---

## Step 1: Set Cluster Context

```bash
# Set your saas cluster context
kubectl config get-contexts
kubectl config use-context <your-saas-context>

# Verify you're on the right cluster
kubectl cluster-info

# Set namespace
export NAMESPACE="jira-k8s-agent"
```

---

## Step 2: Store Secrets in Vault

### 2.1 Login to Vault
```bash
vault login
vault token lookup  # Verify authentication
```

### 2.2 Store Application Secrets
```bash
vault kv put internal/jira-k8s-agent \
  jira-url="https://jira.devtools.intel.com" \
  jira-email="YOUR_EMAIL@intel.com" \
  jira-api-token="YOUR_JIRA_API_TOKEN" \
  google-api-key="YOUR_GOOGLE_AI_API_KEY" \
  langchain-api-key="YOUR_LANGSMITH_KEY"
```

### 2.3 Store Kubeconfigs
```bash
# Verify you have kubeconfigs
ls ~/.kube/config-hldc02
ls ~/.kube/config-hldc03

# Test cluster access
kubectl --kubeconfig=~/.kube/config-hldc02 get nodes
kubectl --kubeconfig=~/.kube/config-hldc03 get nodes

# Store in Vault (base64 encoded)
vault kv patch internal/jira-k8s-agent \
  kubeconfig-hldc02="$(cat ~/.kube/config-hldc02 | base64 -w 0)" \
  kubeconfig-hldc03="$(cat ~/.kube/config-hldc03 | base64 -w 0)"
```

### 2.4 Verify All Secrets
```bash
vault kv get internal/jira-k8s-agent

# Should show 7 keys:
# - jira-url, jira-email, jira-api-token
# - google-api-key, langchain-api-key
# - kubeconfig-hldc02, kubeconfig-hldc03
```

---

## Step 3: Build and Push Images

```bash
cd /home/anoah/habana/habana-internal/jira-jenkins-agent

# Build both images
make build-all

# Push to registry
docker login artifactory-kfs.habana-labs.com
make push-jira-agent
make push-langgraph
```

---

## Step 4: Deploy to saas-cluster

### 4.1 Create Namespace
```bash
kubectl create namespace jira-k8s-agent
kubectl config set-context --current --namespace=jira-k8s-agent
```

### 4.2 Deploy All Components
```bash
# Deploy everything (ExternalSecrets, deployments, services, RBAC)
kubectl apply -k deploy/base/

# Watch deployment
kubectl get pods -w
```

Expected output:
```
NAME                              READY   STATUS    RESTARTS   AGE
jira-agent-xxxxx                  1/1     Running   0          30s
langgraph-agent-xxxxx             1/1     Running   0          30s
```

---

## Step 5: Verify Deployment

### 5.1 Check ExternalSecrets Synced
```bash
kubectl get externalsecret

# Should show SecretSynced for:
# - jira-k8s-agent-external-secret
# - hldc02-kubeconfig-external-secret
# - hldc03-kubeconfig-external-secret
```

### 5.2 Verify Secrets Created
```bash
kubectl get secrets

# Should include:
# - jira-k8s-agent-secret
# - hldc02-kubeconfig
# - hldc03-kubeconfig
```

### 5.3 Check Cluster Manager Initialized
```bash
kubectl logs -l app=jira-agent | grep "cluster manager initialized"

# Expected: clusters: [saas, hldc02, hldc03]
```

### 5.4 Verify MCP Endpoints
```bash
kubectl logs -l app=jira-agent | grep "registered K8s MCP endpoint"

# Expected 3 endpoints:
# /mcp/k8s/saas
# /mcp/k8s/hldc02
# /mcp/k8s/hldc03
```

---

## Step 6: Test Multi-Cluster Functionality

### 6.1 Test Health Endpoints
```bash
kubectl port-forward svc/jira-agent 8080:8080 &
curl http://localhost:8080/health
curl http://localhost:8080/ready
pkill -f "port-forward.*jira-agent"
```

### 6.2 Create Test Ticket
```bash
# Create ticket with g2 keyword (detects hldc02)
curl -X POST https://jira.devtools.intel.com/rest/api/2/issue \
  -u YOUR_EMAIL:YOUR_JIRA_TOKEN \
  -H "Content-Type: application/json" \
  -d '{
    "fields": {
      "project": {"key": "GAUDISW"},
      "summary": "Test: g2 cluster investigation",
      "description": "Testing multi-cluster detection",
      "issuetype": {"name": "Bug"},
      "components": [{"name": "DevOps_K8S"}],
      "labels": ["ai-investigate", "g2"]
    }
  }'
```

### 6.3 Monitor Investigation
```bash
# Watch logs for cluster detection
kubectl logs -l app=langgraph-agent -f | grep -i cluster

# Expected:
# "Detected cluster from keywords: hldc02"
# "Investigating cluster hldc02..."
```

---

## Troubleshooting

### ExternalSecret Not Syncing
```bash
# Check ExternalSecret status
kubectl describe externalsecret hldc02-kubeconfig-external-secret

# Verify Vault has secrets
vault kv get internal/jira-k8s-agent

# Check External Secrets Operator logs
kubectl logs -n external-secrets -l app.kubernetes.io/name=external-secrets
```

### Cluster Manager Shows Only "saas"
```bash
# Check secrets exist
kubectl get secrets | grep kubeconfig

# Check jira-agent logs
kubectl logs -l app=jira-agent | grep -i error

# Verify kubeconfig format
kubectl get secret hldc02-kubeconfig -o jsonpath='{.data.config}' | base64 -d | head -5
```

### Pods Crash Looping
```bash
kubectl describe pod <pod-name>
kubectl logs <pod-name> --previous

# Common issues:
# - Missing secrets (check ExternalSecrets)
# - Image pull errors (check registry access)
# - Invalid configuration (check env vars)
```

---

## Quick Reference

```bash
# View all resources
kubectl get all -n jira-k8s-agent

# Check secrets
kubectl get secrets

# View ExternalSecrets
kubectl get externalsecret

# Stream logs
kubectl logs -l app=jira-agent -f
kubectl logs -l app=langgraph-agent -f

# Restart deployments
kubectl rollout restart deployment/jira-agent
kubectl rollout restart deployment/langgraph-agent

# Show cluster config
kubectl get deployment jira-agent \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="CLUSTER_CONFIGS")].value}'
```

---

## Post-Deployment Checklist

- [ ] All ExternalSecrets show "SecretSynced"
- [ ] All secrets created (4 total)
- [ ] Both pods running (jira-agent, langgraph-agent)
- [ ] Cluster manager initialized with 3 clusters
- [ ] All 3 MCP endpoints registered
- [ ] Health endpoints respond
- [ ] Test ticket processed successfully
- [ ] Cluster detection works
- [ ] LangSmith shows correct cluster

---

**Deployment Complete! 🎉**

Next steps:
- Monitor LangSmith: https://smith.langchain.com
- Review logs for any errors
- Test with real tickets containing cluster keywords (g2, g3, hldc02, hldc03)
