#!/bin/bash
# Quick Start Script for Jira-K8s Agent
# This script sets up a minimal working environment for testing

set -e

echo "Jira-K8s Agent Quick Start"
echo "=============================="

# Check prerequisites
echo "Checking prerequisites..."
command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found"; exit 1; }
command -v kind >/dev/null 2>&1 || { echo "kind not found"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "docker not found"; exit 1; }

# Step 1: Check if kind cluster exists, create if needed
CLUSTER_NAME="jira-agent-test"
echo "Checking for existing kind cluster '$CLUSTER_NAME'..."

if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "Cluster '$CLUSTER_NAME' already exists"

    # Switch to this cluster context
    kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || true

    read -p "Do you want to delete and recreate it? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Deleting existing cluster..."
        kind delete cluster --name "$CLUSTER_NAME"
        echo "Creating new kind cluster '$CLUSTER_NAME'..."
        kind create cluster --name "$CLUSTER_NAME"
    fi
else
    echo "Creating kind cluster '$CLUSTER_NAME'..."
    kind create cluster --name "$CLUSTER_NAME"
fi

# Verify cluster is accessible
echo "Verifying cluster connection..."
kubectl cluster-info --context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 || {
    echo "Failed to connect to cluster"
    exit 1
}

# Step 2: Configure proxy (if behind corporate proxy)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/configure-kind-proxy.sh" ]; then
    echo "Configuring proxy settings..."
    bash "$SCRIPT_DIR/configure-kind-proxy.sh" "$CLUSTER_NAME"
fi

# Step 3: Create namespace
echo "Creating namespace..."
kubectl create namespace jira-webhook-server --dry-run=client -o yaml | kubectl apply -f -

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo ""
echo "1. Build and load images:"
echo "   make docker-build"
echo "   kind load docker-image jira-agent:latest --name $CLUSTER_NAME"
echo "   kind load docker-image langgraph-agent:latest --name $CLUSTER_NAME"
echo ""
echo "2. Create secrets:"
echo "   kubectl create secret generic jira-k8s-agent-secret \\"
echo "     --from-literal=jira-url=https://your-jira.example.com \\"
echo "     --from-literal=jira-email=your-email@example.com \\"
echo "     --from-literal=jira-api-token=your-token \\"
echo "     --from-literal=jenkins-username=your-jenkins-user \\"
echo "     --from-literal=jenkins-api-token=your-jenkins-token \\"
echo "     -n jira-webhook-server"
echo ""
echo "3. Deploy the agent:"
echo "   kubectl apply -k deploy/base/"
echo ""
echo "4. Check status:"
echo "   kubectl get pods -n jira-webhook-server"
echo "   kubectl logs -n jira-webhook-server -l app=jira-agent -f"
echo ""
