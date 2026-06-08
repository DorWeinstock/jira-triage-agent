#!/bin/bash
# Prerequisites Check Script

echo "🔍 Checking prerequisites for Jira-K8s Agent..."
echo ""

# Track if all requirements are met
all_good=true

# Check kubectl
echo -n "Checking kubectl... "
if command -v kubectl &> /dev/null; then
    version=$(kubectl version --client --short 2>/dev/null | head -n1)
    echo "✅ Found ($version)"
else
    echo "❌ NOT FOUND"
    echo "   Install: brew install kubectl (macOS) or see https://kubernetes.io/docs/tasks/tools/"
    all_good=false
fi

# Check helm
echo -n "Checking helm... "
if command -v helm &> /dev/null; then
    version=$(helm version --short 2>/dev/null)
    echo "✅ Found ($version)"
else
    echo "❌ NOT FOUND"
    echo "   Install: brew install helm (macOS) or curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash"
    all_good=false
fi

# Check for a cluster tool (kind or docker desktop)
echo -n "Checking cluster tool... "
cluster_tool=""
if command -v kind &> /dev/null; then
    echo "✅ Found kind"
    cluster_tool="kind"
elif kubectl config get-contexts 2>/dev/null | grep -q "docker-desktop\|rancher-desktop"; then
    echo "✅ Found Docker Desktop / Rancher Desktop"
    cluster_tool="docker-desktop"
else
    echo "❌ NOT FOUND"
    echo "   Install kind:"
    echo "   - macOS: brew install kind"
    echo "   - Linux: curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64 && chmod +x ./kind && sudo mv ./kind /usr/local/bin/"
    echo "   - Or use Docker Desktop with K8s enabled"
    all_good=false
fi

# Check if Docker is running (needed for kind)
if [ "$cluster_tool" = "kind" ]; then
    echo -n "Checking Docker (required for kind)... "
    if docker ps &> /dev/null; then
        echo "✅ Docker is running"
    else
        echo "❌ Docker not running or not installed"
        echo "   kind requires Docker. Start Docker or use Docker Desktop."
        all_good=false
    fi
fi

# Check for existing cluster
echo -n "Checking for running K8s cluster... "
if kubectl cluster-info &> /dev/null; then
    context=$(kubectl config current-context)
    echo "✅ Cluster running (context: $context)"
else
    echo "⚠️  No cluster running"
    if [ "$cluster_tool" = "kind" ]; then
        echo "   Start with: kind create cluster --name jira-agent-test"
    fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$all_good" = true ]; then
    echo "✅ All prerequisites met! You're ready to run quick-start.sh"
else
    echo "❌ Some prerequisites missing. Please install the tools above."
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"