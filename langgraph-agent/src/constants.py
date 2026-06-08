"""Centralized constants and magic strings.

This module centralizes all constants to:
- Eliminate scattered magic strings
- Provide single source of truth
- Enable easy updates across codebase
- Improve code readability with named constants
"""

from typing import Final

__all__ = [
    "MISSING_VALUE",
    "SUCCESS_INDICATORS",
    "ERROR_INDICATORS",
    "TRANSITIONAL_INDICATORS",
    "RESOURCE_PRIORITY",
    "AGENT_JIRA",
    "AGENT_K8S_INVESTIGATOR",
    "AGENT_DIAGNOSTICIAN",
    "AGENT_K8S_REMEDIATION_EXECUTOR",
    "AGENT_REMEDIATION",
    "CLUSTER_KEYWORD_PATTERNS",
]

# =============================================================================
# Status Indicators
# =============================================================================

MISSING_VALUE: Final[str] = "N/A"
"""Displayed when a value is missing or unavailable."""

SUCCESS_INDICATORS: Final[frozenset[str]] = frozenset({
    "success",
    "running",
    "ready",
    "healthy",
    "active",
    "completed",
    "succeeded",
    "available",
})
"""Keywords indicating successful/healthy status."""

ERROR_INDICATORS: Final[frozenset[str]] = frozenset({
    "error",
    "failed",
    "failure",
    "crashloopbackoff",
    "imagepullbackoff",
    "errimagepull",
    "oomkilled",
    "terminated",
    "evicted",
    "unknown",
})
"""Keywords indicating error/unhealthy status."""

TRANSITIONAL_INDICATORS: Final[frozenset[str]] = frozenset({
    "pending",
    "containercreating",
})
"""Transitional states — pods in progress, not yet healthy or failed.

These are NOT errors. Use when distinguishing between 'broken' and 'starting'."""

# =============================================================================
# K8s Resource Types
# =============================================================================

RESOURCE_PRIORITY: Final[tuple[str, ...]] = (
    "pods",
    "deployments",
    "services",
    "configmaps",
    "secrets",
    "replicasets",
    "statefulsets",
    "daemonsets",
    "persistentvolumeclaims",
    "events",
)
"""Resource types in order of investigation priority."""

# =============================================================================
# Agent Names
# =============================================================================

AGENT_JIRA: Final[str] = "JiraAgent"
AGENT_K8S_INVESTIGATOR: Final[str] = "K8sInvestigator"
AGENT_DIAGNOSTICIAN: Final[str] = "Diagnostician"
AGENT_K8S_REMEDIATION_EXECUTOR: Final[str] = "K8sRemediationExecutor"

# TODO: remove after all callers migrate to AGENT_K8S_REMEDIATION_EXECUTOR
AGENT_REMEDIATION: Final[str] = AGENT_K8S_REMEDIATION_EXECUTOR

# =============================================================================
# Cluster Configuration
# =============================================================================

CLUSTER_KEYWORD_PATTERNS: Final[dict[str, list[str]]] = {
    "hldc02": [r'\bhls2\b', r'\bg2\b', r'\bhldc02\b'],
    "hldc03": [r'\bg3\b', r'\bhldc03\b'],
}
"""Cluster detection patterns: maps cluster name to list of regex patterns.
Used by _detect_cluster_from_keywords to identify which cluster a ticket relates to."""

# =============================================================================
# Utility Functions
# =============================================================================
# (No utility functions currently needed - all logic moved to specialized modules)
