"""Pod analysis utilities for Kubernetes investigation.

This module provides functions for analyzing pod status, extracting pod information,
and formatting deployment status messages.
"""

import logging
from typing import Any

from .dependency_discovery import get_deployment_from_pod_name

logger = logging.getLogger(__name__)


def is_pod_running_and_ready(status: str, ready: str) -> bool:
    """Check if pod is running and ready.

    Args:
        status: Pod status string (e.g., "Running", "Pending")
        ready: Pod ready string (e.g., "1/1", "0/1")

    Returns:
        True if pod is running and all containers are ready
    """
    if status != "Running":
        return False
    parts = ready.split('/')
    if len(parts) != 2:
        return False
    try:
        running, total = int(parts[0]), int(parts[1])
        return total > 0 and running == total
    except ValueError:
        logger.debug("Unexpected ready format: %r", ready)
        return False


def is_pod_in_error_state(status: str) -> bool:
    """Check if pod is in error state.

    Args:
        status: Pod status string

    Returns:
        True if pod is in error state
    """
    error_states = [
        "Error", "CrashLoopBackOff", "ImagePullBackOff",
        "Pending", "CreateContainerConfigError"
    ]
    return status in error_states


def _is_mcp_structured_format(pods_output: str) -> bool:
    """Detect if output uses Go MCP structured format vs kubectl tabular."""
    if not pods_output:
        return False
    lines = pods_output.splitlines()
    return (
        any(line.strip().startswith("NAME:") for line in lines)
        and any(line.strip().startswith("STATUS:") for line in lines)
    )


def _parse_mcp_structured(pods_output: str) -> list[dict[str, str]]:
    """Parse Go MCP server's structured pod output.

    Expected format:
        Found N pod(s):

        NAME: pod-name-hash
          NAMESPACE: production
          STATUS: Running
          LABELS:
            app: order-service

    Returns:
        List of dicts with 'name' and 'status' keys.
    """
    pods = []
    current_pod: dict[str, str] | None = None

    for line in pods_output.split('\n'):
        stripped = line.strip()
        if stripped.startswith("NAME:"):
            if current_pod and current_pod.get("name"):
                pods.append(current_pod)
            current_pod = {"name": stripped.split(":", 1)[1].strip(), "status": ""}
        elif stripped.startswith("STATUS:") and current_pod is not None:
            current_pod["status"] = stripped.split(":", 1)[1].strip()

    if current_pod and current_pod.get("name"):
        pods.append(current_pod)

    return pods


def _parse_tabular(pods_output: str) -> list[dict[str, str]]:
    """Parse kubectl tabular pod output.

    Expected format:
        NAME                            READY   STATUS    RESTARTS   AGE
        order-service-688ffdf8b-lw2rc   1/1     Running   0          2m

    Returns:
        List of dicts with 'name', 'status', and 'ready' keys.
    """
    pods = []
    for line in pods_output.split('\n'):
        if not line or 'NAME' in line or 'No resources' in line:
            continue
        parts = line.split()
        if len(parts) < 3:
            if line.strip():  # Only log non-blank lines
                logger.debug("Skipping malformed pod line: %r", line)
            continue
        pods.append({
            "name": parts[0],
            "ready": parts[1] if len(parts) > 1 else "0/0",
            "status": parts[2] if len(parts) > 2 else "Unknown",
        })
    return pods


def analyze_deployment_pods(
    pods_output: str,
    deployment_name: str
) -> dict[str, Any]:
    """Analyze pods belonging to a deployment.

    Handles both Go MCP structured format and kubectl tabular format.

    Args:
        pods_output: Pod output from MCP server or kubectl
        deployment_name: Deployment to analyze

    Returns:
        Dictionary with:
        - matching_pods: List of pod names
        - running: Count of running pods
        - errors: List of error descriptions
        - total: Total pod count
    """
    if not pods_output:
        return {"matching_pods": [], "running": 0, "errors": [], "total": 0}

    matching_pods = []
    running_pods = 0
    error_pods = []

    if _is_mcp_structured_format(pods_output):
        parsed_pods = _parse_mcp_structured(pods_output)
    else:
        parsed_pods = _parse_tabular(pods_output)

    for pod in parsed_pods:
        pod_name = pod["name"]
        pod_deployment = get_deployment_from_pod_name(pod_name)

        if pod_deployment.lower() == deployment_name.lower():
            matching_pods.append(pod_name)
            status = pod["status"]
            ready = pod.get("ready", "")

            # MCP format has no READY column; use STATUS=Running as proxy
            if ready and is_pod_running_and_ready(status, ready):
                running_pods += 1
            elif not ready and status == "Running":
                running_pods += 1
            elif is_pod_in_error_state(status):
                error_pods.append(f"{pod_name}: {status}")

    return {
        "matching_pods": matching_pods,
        "running": running_pods,
        "errors": error_pods,
        "total": len(matching_pods)
    }


def format_pod_list(pod_names: list[str]) -> str:
    """Format pod name list for display.

    Args:
        pod_names: List of pod names

    Returns:
        Formatted pod list string (shows first 5 with "..." if more)
    """
    preview = ', '.join(pod_names[:5])
    suffix = " ..." if len(pod_names) > 5 else ""
    return f"  Pods: {preview}{suffix}"


def format_no_pods_status(deployment_name: str) -> str:
    """Format status message for deployment with no pods.

    Args:
        deployment_name: Name of deployment

    Returns:
        Formatted status string with kubectl-compatible header
    """
    return (
        f"{deployment_name}   0/0   0   0   0s\n"
        f"DEPLOYMENT STATUS: {deployment_name}\n"
        f"  Replicas: 0/0 (deployment appears to have 0 replicas - NO PODS FOUND)\n"
        f"  Status: SCALED TO ZERO or PODS NOT CREATED\n"
        f"  Action Required: Scale deployment to at least 1 replica to restore service\n"
        f"  Command: kubectl scale deployment/{deployment_name} --replicas=1"
    )


def format_deployment_status(
    deployment_name: str,
    pod_info: dict[str, Any]
) -> str:
    """Format deployment status based on pod information.

    Args:
        deployment_name: Name of deployment
        pod_info: Pod analysis results from analyze_deployment_pods()

    Returns:
        Formatted status string with kubectl-compatible header
    """
    running = pod_info['running']
    total = pod_info['total']

    # kubectl-compatible format: NAME READY UP-TO-DATE AVAILABLE AGE
    status_lines = [
        f"{deployment_name}   {running}/{total}   {total}   {running}   0s",
        f"DEPLOYMENT STATUS: {deployment_name}",
        f"  Replicas: {running}/{total} ready",
        format_pod_list(pod_info["matching_pods"])
    ]

    if pod_info["errors"]:
        status_lines.append(f"  Errors: {'; '.join(pod_info['errors'][:3])}")
        status_lines.append(
            f"  Status: DEGRADED - {len(pod_info['errors'])} pod(s) in error state"
        )
    elif running == total:
        status_lines.append("  Status: HEALTHY - all pods running")
    else:
        not_ready = total - running
        status_lines.append(f"  Status: TRANSITIONING - {not_ready} pod(s) not ready")

    return '\n'.join(status_lines)


def infer_deployment_status_from_pods(
    pods_output: str,
    deployment_name: str
) -> str:
    """Infer deployment status from pod data.

    Analyzes pods output to determine:
    1. How many pods exist for the deployment (ready/total)
    2. What state the pods are in (Running, Error, etc.)
    3. If no pods exist, infers the deployment has 0 replicas

    Args:
        pods_output: Raw output from kubectl get pods
        deployment_name: Name of the deployment to check

    Returns:
        Human-readable deployment status string
    """
    if not pods_output or "No resources found" in pods_output:
        return format_no_pods_status(deployment_name)

    pod_info = analyze_deployment_pods(pods_output, deployment_name)

    if pod_info["total"] == 0:
        return format_no_pods_status(deployment_name)

    return format_deployment_status(deployment_name, pod_info)
