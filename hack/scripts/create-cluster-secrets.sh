#!/bin/bash
set -e

# Script to create Kubernetes secrets for external cluster kubeconfigs.
#
# Usage:
#   ./create-cluster-secrets.sh              # Use ~/.kube/config-* (local testing)
#   ./create-cluster-secrets.sh --vault      # Use Vault (production - via ExternalSecrets)
#   ./create-cluster-secrets.sh --help       # Show help

NAMESPACE="jira-k8s-agent"

show_help() {
    cat <<EOF
Create Kubernetes secrets for multi-cluster kubeconfigs

Usage:
  $(basename "$0") [OPTIONS]

Options:
  --vault          Use Vault-based approach (ExternalSecrets)
  --local          Use local kubeconfig files from ~/.kube/ (default)
  --help           Show this help message

Local Testing Mode (default):
  Creates K8s secrets directly from ~/.kube/config-hldc02 and ~/.kube/config-hldc03
  Use this for local development and testing with kind clusters

Vault Production Mode (--vault):
  Verifies that ExternalSecrets are configured and syncing from Vault
  Kubeconfigs must be stored in Vault at: internal/jira-k8s-agent/kubeconfigs
  See docs/vault-setup.md for instructions on storing kubeconfigs in Vault

Examples:
  # Local testing (creates secrets from ~/.kube/config-*)
  ./create-cluster-secrets.sh

  # Verify Vault-based ExternalSecrets are working
  ./create-cluster-secrets.sh --vault

EOF
    exit 0
}

create_local_secrets() {
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  Creating unified kubeconfig secret from ~/.kube/ (Local Test)  ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""

    KUBE_DIR="$HOME/.kube"
    local files_found=0

    # Check if files exist
    [ -f "${KUBE_DIR}/config-hldc02" ] && files_found=$((files_found + 1))
    [ -f "${KUBE_DIR}/config-hldc03" ] && files_found=$((files_found + 1))

    if [ $files_found -eq 0 ]; then
        echo "❌ No kubeconfig files found. Place kubeconfigs at:"
        echo "   - ${KUBE_DIR}/config-hldc02"
        echo "   - ${KUBE_DIR}/config-hldc03"
        exit 1
    fi

    # Build kubectl create command with available files
    echo "→ Creating unified secret: cluster-kubeconfigs"
    cmd="kubectl create secret generic cluster-kubeconfigs -n $NAMESPACE"

    if [ -f "${KUBE_DIR}/config-hldc02" ]; then
        cmd="$cmd --from-file=hldc02=${KUBE_DIR}/config-hldc02"
        echo "  ✓ Added hldc02 from ${KUBE_DIR}/config-hldc02"
    else
        echo "  ⚠ Warning: ${KUBE_DIR}/config-hldc02 not found, skipping"
    fi

    if [ -f "${KUBE_DIR}/config-hldc03" ]; then
        cmd="$cmd --from-file=hldc03=${KUBE_DIR}/config-hldc03"
        echo "  ✓ Added hldc03 from ${KUBE_DIR}/config-hldc03"
    else
        echo "  ⚠ Warning: ${KUBE_DIR}/config-hldc03 not found, skipping"
    fi

    # Execute the command
    cmd="$cmd --dry-run=client -o yaml"
    eval "$cmd" | kubectl apply -f -

    echo ""
    echo "✓ Unified secret 'cluster-kubeconfigs' created with $files_found kubeconfig(s)"
    echo ""
    echo "Verify with:"
    echo "  kubectl get secret cluster-kubeconfigs -n $NAMESPACE"
    echo "  kubectl get secret cluster-kubeconfigs -n $NAMESPACE -o jsonpath='{.data}' | jq 'keys'"
}

verify_vault_secrets() {
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  Verifying Vault-based ExternalSecrets (Production Mode)        ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""

    echo "→ Checking ExternalSecret status..."
    if ! kubectl get externalsecret kubeconfigs-external-secret -n "$NAMESPACE" &>/dev/null; then
        echo "❌ ExternalSecret 'kubeconfigs-external-secret' not found in namespace $NAMESPACE"
        echo "   Deploy with: kubectl apply -k deploy/base/"
        exit 1
    fi

    echo ""
    kubectl get externalsecret kubeconfigs-external-secret -n "$NAMESPACE"

    echo ""
    echo "→ Checking if unified secret was created by ExternalSecret..."

    if kubectl get secret cluster-kubeconfigs -n "$NAMESPACE" &>/dev/null; then
        # Check if it was created by ExternalSecret
        owner=$(kubectl get secret cluster-kubeconfigs -n "$NAMESPACE" -o jsonpath='{.metadata.ownerReferences[0].kind}' 2>/dev/null || echo "")
        if [ "$owner" == "ExternalSecret" ]; then
            echo "  ✓ cluster-kubeconfigs exists (managed by ExternalSecret)"

            # Check which keys are present
            echo ""
            echo "  Keys in secret:"
            keys=$(kubectl get secret cluster-kubeconfigs -n "$NAMESPACE" -o jsonpath='{.data}' | jq -r 'keys[]' 2>/dev/null || echo "")
            if [ -n "$keys" ]; then
                echo "$keys" | while read -r key; do
                    echo "    - $key"
                done
            fi
        else
            echo "  ⚠ cluster-kubeconfigs exists but NOT managed by ExternalSecret"
        fi
    else
        echo "  ❌ cluster-kubeconfigs secret not found"
        echo ""
        echo "To fix:"
        echo "  1. Store kubeconfigs in Vault (see docs/vault-setup.md):"
        echo "     vault kv patch internal/jira-k8s-agent \\"
        echo "       kubeconfig-hldc02=@~/.kube/config-hldc02 \\"
        echo "       kubeconfig-hldc03=@~/.kube/config-hldc03"
        echo ""
        echo "  2. Check ExternalSecret status:"
        echo "     kubectl describe externalsecret kubeconfigs-external-secret -n $NAMESPACE"
        exit 1
    fi

    echo ""
    echo "✓ Unified kubeconfig secret successfully synced from Vault"
}

# Parse arguments
MODE="local"
while [[ $# -gt 0 ]]; do
    case $1 in
        --vault)
            MODE="vault"
            shift
            ;;
        --local)
            MODE="local"
            shift
            ;;
        --help|-h)
            show_help
            ;;
        *)
            echo "Unknown option: $1"
            echo "Run with --help for usage information"
            exit 1
            ;;
    esac
done

# Execute based on mode
case $MODE in
    local)
        create_local_secrets
        ;;
    vault)
        verify_vault_secrets
        ;;
esac
