"""Dependency discovery for Kubernetes deployments.

This module provides functions for discovering dependencies between deployments
by analyzing pod descriptions, environment variables, and service references.
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def get_deployment_from_pod_name(pod_name: str) -> str:
    """Extract deployment name from pod name.

    Pod names follow: deployment-name-replicaset-hash-pod-hash
    This is a heuristic - strips the last two hyphen-separated segments.

    Examples:
        "order-service-5f8d6c7b-abc12" -> "order-service"
        "api-server-prod-7c9d8e6f-xyz99" -> "api-server-prod"
        "simple-abc12" -> "simple"
        "single" -> "single"
    """
    parts = pod_name.rsplit('-', 2)
    if len(parts) == 3:
        return parts[0]
    if len(parts) == 2:
        logger.debug(
            "Pod name '%s' has only one suffix segment; deployment name may be inaccurate.",
            pod_name,
        )
        return parts[0]
    logger.debug(
        "Pod name '%s' has no hyphen segments; returning as-is.",
        pod_name,
    )
    return pod_name


def extract_service_references(
    deployment_yaml: str,
    namespace: str
) -> list[dict[str, str]]:
    """Extract service references from deployment YAML.

    Looks at:
    - Environment variables with service DNS patterns
    - ConfigMap/Secret references that point to other services

    Args:
        deployment_yaml: Pod description or deployment YAML content
        namespace: Kubernetes namespace

    Returns:
        List of dicts with 'service' and 'evidence' keys
    
    Raises:
        ValueError: If namespace is empty or None
    """
    if not namespace or not namespace.strip():
        raise ValueError("namespace must be a non-empty string")
    if not deployment_yaml:
        return []
    
    refs = []

    # Look for K8s service DNS patterns
    dns_pattern = (
        r'\b([a-z0-9][a-z0-9-]{2,}[a-z0-9])'  # min 4 chars: a-z0-9, middle can have hyphens, ends with alphanumeric
        r'(?:\.' + re.escape(namespace) + r')?'
        r'\.svc(?:\.cluster\.local)?\b'
    )
    dns_matches = re.findall(dns_pattern, deployment_yaml, re.IGNORECASE)
    for match in dns_matches:
        if match and len(match) > 2:
            refs.append({
                "service": match,
                "evidence": f"Service DNS reference found: {match}.svc.cluster.local"
            })

    # Look for common env var patterns that reference services
    env_patterns = [
        (r'"([A-Z_]+_HOST)":\s*"([^"]+)"', "HOST env var"),
        (r'"([A-Z_]+_URL)":\s*"([^"]+)"', "URL env var"),
        (r'"([A-Z_]+_ENDPOINT)":\s*"([^"]+)"', "ENDPOINT env var"),
        (r'"([A-Z_]+_SERVICE)":\s*"([^"]+)"', "SERVICE env var"),
    ]

    for pattern, evidence_type in env_patterns:
        matches = re.findall(pattern, deployment_yaml)
        for var_name, var_value in matches:
            service_match = re.search(r'\b([a-z][a-z0-9-]{2,}[a-z0-9])\b', var_value.lower())
            if service_match:
                service_name = service_match.group(1)
                refs.append({
                    "service": service_name,
                    "evidence": f"{evidence_type}: {var_name}={var_value}"
                })

    # Deduplicate by service name
    seen = set()
    unique_refs = []
    for ref in refs:
        if ref["service"] not in seen:
            seen.add(ref["service"])
            unique_refs.append(ref)

    return unique_refs


def extract_deployment_names(kubectl_output: str) -> list[str]:
    """Extract deployment names from kubectl get deployments output.

    Args:
        kubectl_output: Raw kubectl output

    Returns:
        List of deployment names
    """
    names = []
    lines = kubectl_output.split('\n')
    for line in lines:
        if not line or line.startswith('NAME ') or line.startswith('No resources'):
            continue
        parts = line.split()
        if len(parts) >= 1:
            name = parts[0]
            if name and not name.startswith('=') and len(name) > 1:
                names.append(name)
    return names


def classify_namespace_issues(
    findings: dict[str, Any],
    target_deployment: str,
    dependencies: dict[str, Any]
) -> dict[str, list[dict[str, str]]]:
    """Classify issues found in namespace by relationship to target.

    Categories:
    - target_issues: Issues directly affecting the target deployment
    - dependency_issues: Issues affecting verified dependencies
    - unrelated_issues: Issues with NO evidence of affecting target

    Args:
        findings: Investigation findings with resources
        target_deployment: Name of target deployment
        dependencies: Discovered dependencies dict

    Returns:
        Classification dict with categorized issues
    """
    classification = {
        "target_issues": [],
        "dependency_issues": [],
        "unrelated_issues": []
    }

    verified_dep_names = {d["name"] for d in dependencies.get("verified_dependencies", [])}
    pods_output = str(findings.get("resources", {}).get("pods", ""))

    problem_indicators = [
        "CrashLoopBackOff", "Error", "ImagePullBackOff",
        "Pending", "CreateContainerConfigError"
    ]

    for line in pods_output.split('\n'):
        if not line or line.startswith('NAME '):
            continue

        has_problem = any(indicator in line for indicator in problem_indicators)
        if not has_problem:
            continue

        parts = line.split()
        if len(parts) < 1:
            continue

        pod_name = parts[0]
        pod_deployment = get_deployment_from_pod_name(pod_name)

        if pod_deployment == target_deployment:
            classification["target_issues"].append({
                "pod": pod_name,
                "status_line": line,
                "relationship": "direct - this is the target deployment"
            })
        elif pod_deployment in verified_dep_names:
            evidence = dependencies.get('evidence', {}).get(pod_deployment, 'service reference')
            classification["dependency_issues"].append({
                "pod": pod_name,
                "status_line": line,
                "dependency": pod_deployment,
                "relationship": f"verified dependency - evidence: {evidence}"
            })
        else:
            classification["unrelated_issues"].append({
                "pod": pod_name,
                "status_line": line,
                "relationship": "NO EVIDENCE of relationship to target - do NOT assume causation"
            })

    return classification
