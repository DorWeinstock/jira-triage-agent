"""MCP tool wrappers for the multi-agent system.

This module provides MCP clients for external services:
- JiraTools: Jira issue management
- K8sTools: Kubernetes access (supports readonly=True for investigation agents)

SECURITY: Use K8sTools(readonly=True) for investigation agents.
Only K8sRemediationExecutor should use K8sTools(readonly=False) with write capabilities.
"""

from .jira_tools import JiraTools
from .k8s_tools import K8sTools

__all__ = ["JiraTools", "K8sTools"]
