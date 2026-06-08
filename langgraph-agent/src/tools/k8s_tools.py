"""Wrapper for Kubernetes MCP server tools.

This module provides a client for interacting with the Kubernetes MCP server,
enabling cluster operations like getting resources, logs, and events.
"""

import logging
import re
import yaml
from typing import Any

from ..config import get_settings
from ..exceptions import ValidationError
from .base_mcp_client import BaseMCPClient

logger = logging.getLogger(__name__)

# RFC 1123 subdomain regex for K8s names
K8S_NAME_PATTERN = re.compile(r'^[a-z0-9]([a-z0-9-]{0,251}[a-z0-9])?$')
K8S_NAMESPACE_PATTERN = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')

# Mapping of K8s resource types to their Kind names (for field selectors)
RESOURCE_KIND_MAP: dict[str, str] = {
    "pods": "Pod", "pod": "Pod",
    "deployments": "Deployment", "deployment": "Deployment", "deploy": "Deployment",
    "services": "Service", "service": "Service", "svc": "Service",
    "configmaps": "ConfigMap", "configmap": "ConfigMap", "cm": "ConfigMap",
     "secrets": "Secret", "secret": "Secret",  # pragma: allowlist secret
    "nodes": "Node", "node": "Node",
    "namespaces": "Namespace", "namespace": "Namespace", "ns": "Namespace",
    "statefulsets": "StatefulSet", "statefulset": "StatefulSet", "sts": "StatefulSet",
    "daemonsets": "DaemonSet", "daemonset": "DaemonSet", "ds": "DaemonSet",
    "replicasets": "ReplicaSet", "replicaset": "ReplicaSet", "rs": "ReplicaSet",
    "jobs": "Job", "job": "Job",
    "cronjobs": "CronJob", "cronjob": "CronJob", "cj": "CronJob",
    "ingresses": "Ingress", "ingress": "Ingress", "ing": "Ingress",
    "endpoints": "Endpoints", "ep": "Endpoints",
    "persistentvolumes": "PersistentVolume", "persistentvolume": "PersistentVolume", "pvs": "PersistentVolume", "pv": "PersistentVolume",
    "persistentvolumeclaims": "PersistentVolumeClaim", "persistentvolumeclaim": "PersistentVolumeClaim", "pvcs": "PersistentVolumeClaim", "pvc": "PersistentVolumeClaim",
    "serviceaccounts": "ServiceAccount", "serviceaccount": "ServiceAccount", "sa": "ServiceAccount",
    "networkpolicies": "NetworkPolicy", "networkpolicy": "NetworkPolicy", "netpol": "NetworkPolicy",
    "storageclasses": "StorageClass", "storageclass": "StorageClass", "sc": "StorageClass",
    "horizontalpodautoscalers": "HorizontalPodAutoscaler", "hpa": "HorizontalPodAutoscaler",
}

# Valid K8s resource types (matches Go GVR map in format.go)
VALID_RESOURCE_TYPES = frozenset({
    # Core (namespaced)
    "pods", "pod",
    "services", "service", "svc",
    "configmaps", "configmap", "cm",
    "secrets", "secret",
    "endpoints", "ep",
    "serviceaccounts", "serviceaccount", "sa",
    "persistentvolumeclaims", "persistentvolumeclaim", "pvcs", "pvc",
    # Core (cluster-scoped)
    "nodes", "node",
    "namespaces", "namespace", "ns",
    "persistentvolumes", "persistentvolume", "pvs", "pv",
    # Apps (namespaced)
    "deployments", "deployment", "deploy",
    "statefulsets", "statefulset", "sts",
    "daemonsets", "daemonset", "ds",
    "replicasets", "replicaset", "rs",
    # Batch (namespaced)
    "jobs", "job",
    "cronjobs", "cronjob", "cj",
    # Networking (namespaced)
    "ingresses", "ingress", "ing",
    "networkpolicies", "networkpolicy", "netpol",
    # Storage (cluster-scoped)
    "storageclasses", "storageclass", "sc",
    # Autoscaling (namespaced)
    "horizontalpodautoscalers", "hpa",
})

# Tool risk classification for HITL (Human-in-the-Loop) approval
# Write tools modify cluster state and require human approval
WRITE_TOOLS = frozenset({
    "kubectl_scale",
    "kubectl_rollout_restart",
    "kubectl_apply",
    "kubectl_delete",
})

# Dangerous K8s resource kinds that cannot be applied via kubectl_apply
# ClusterRoleBinding and ClusterRole could elevate privileges; use kubectl directly with explicit approval
DANGEROUS_KINDS = frozenset({
    "ClusterRoleBinding",
    "ClusterRole",
})

# Read tools only observe cluster state and are safe to execute
READ_TOOLS = frozenset({
    # Generic tools (Phase 1)
    "kubectl_get",
    "kubectl_describe",
    # Legacy tools (kept for backward compat)
    "kubectl_get_pods",
    "kubectl_get_deployments",
    "kubectl_get_services",
    "kubectl_get_configmaps",
    "kubectl_get_secrets",
    "kubectl_logs",
    "kubectl_events",
    "kubectl_describe_pod",
})


def classify_tool_risk(tool_name: str) -> str:
    """Classify the risk level of a K8s MCP tool.

    Used by HITL (Human-in-the-Loop) to determine whether a tool call
    requires human approval before execution.

    Args:
        tool_name: Name of the MCP tool to classify.

    Returns:
        "high" for write operations that modify cluster state,
        "low" for read-only operations,
        "high" for unknown tools (safety default).
    """
    if tool_name in WRITE_TOOLS:
        return "high"
    if tool_name in READ_TOOLS:
        return "low"
    # Unknown tools default to high risk for safety
    return "high"


def is_write_tool(tool_name: str) -> bool:
    """Check if a tool performs write operations on the cluster.

    Args:
        tool_name: Name of the MCP tool to check.

    Returns:
        True if the tool modifies cluster state, False otherwise.
    """
    return tool_name in WRITE_TOOLS




class K8sTools(BaseMCPClient):
    """Client for interacting with the Kubernetes MCP server.

    Provides high-level methods for common Kubernetes operations while handling
    MCP protocol details internally. All methods include input validation to
    catch errors early and provide clear error messages.

    Supports both read and write operations. Use readonly=True for investigation
    agents that should not modify cluster state.
    """

    def __init__(self, mcp_endpoint: str = None, readonly: bool = False):
        """Initialize K8s tools client.

        Args:
            mcp_endpoint: URL of the K8s MCP server (default: from config)
            readonly: If True, blocks write operations (for investigation agents)
        """
        settings = get_settings()
        self.readonly = readonly
        super().__init__(endpoint=mcp_endpoint or settings.k8s_mcp_endpoint)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any], max_retries: int = 2
    ) -> Any:
        """Call MCP tool with readonly enforcement.

        Args:
            tool_name: Tool to call
            arguments: Tool arguments
            max_retries: Maximum number of retry attempts for connection errors

        Returns:
            Tool result

        Raises:
            PermissionError: If write operation blocked in readonly mode
        """
        if self.readonly and tool_name in WRITE_TOOLS:
            logger.error(
                f"[{self.client_name}] SECURITY: Blocked attempt to call "
                f"write operation '{tool_name}' in readonly mode"
            )
            raise PermissionError(
                f"Write operation '{tool_name}' is blocked in readonly mode. "
                f"Investigation agents can only use read-only operations."
            )

        return await super().call_tool(tool_name, arguments, max_retries=max_retries)

    # =========================================================================
    # Generic Get/Describe (Phase 1 - new tools)
    # =========================================================================

    async def get(
        self,
        resource_type: str,
        namespace: str | None = None,
        name: str | None = None,
        label_selector: str | None = None,
        field_selector: str | None = None,
        limit: int | None = None,
    ) -> str:
        """Get any K8s resource via generic kubectl_get tool.

        Returns clean YAML. Omit namespace for all-NS or cluster-scoped.

        Args:
            resource_type: Type of resource (pods, deployments, nodes, etc.)
            namespace: K8s namespace. Omit for all-NS or cluster-scoped.
            name: Specific resource name. Omit for listing.
            label_selector: Label selector filter.
            field_selector: Field selector filter.
            limit: Max resources to return.

        Returns:
            Clean YAML output from MCP server.

        Raises:
            ValidationError: If input parameters are invalid.
        """
        self._validate_resource_type(resource_type)
        if namespace:
            self._validate_namespace(namespace)
        if name:
            self._validate_resource_name(name)

        args: dict[str, Any] = {"resource_type": resource_type}
        if namespace:
            args["namespace"] = namespace
        if name:
            args["name"] = name
        if label_selector:
            args["label_selector"] = label_selector
        if field_selector:
            args["field_selector"] = field_selector
        if limit is not None:
            args["limit"] = limit

        result = await self.call_tool("kubectl_get", args)
        logger.info(
            "retrieved resources (generic)",
            extra={
                "component": "k8s_tools",
                "resource_type": resource_type,
                "namespace": namespace or "all",
            },
        )
        return result

    async def describe(
        self,
        resource_type: str,
        name: str,
        namespace: str | None = None,
    ) -> str:
        """Describe any K8s resource via generic kubectl_describe tool.

        Returns full resource YAML with noisy fields stripped.

        Args:
            resource_type: Type of resource.
            name: Resource name.
            namespace: K8s namespace. Omit for cluster-scoped.

        Returns:
            Clean YAML output from MCP server.

        Raises:
            ValidationError: If input parameters are invalid.
        """
        self._validate_resource_type(resource_type)
        self._validate_resource_name(name)
        if namespace:
            self._validate_namespace(namespace)

        args: dict[str, Any] = {"resource_type": resource_type, "name": name}
        if namespace:
            args["namespace"] = namespace

        result = await self.call_tool("kubectl_describe", args)
        logger.info(
            "described resource (generic)",
            extra={
                "component": "k8s_tools",
                "resource_type": resource_type,
                "name": name,
                "namespace": namespace or "cluster-scoped",
            },
        )
        return result

    # =========================================================================
    # Legacy Get/Describe (Phase 1 - kept for backward compat)
    # =========================================================================

    async def kubectl_get(
        self,
        resource_type: str,
        namespace: str = "default",
        name: str | None = None,
        label_selector: str | None = None
    ) -> Any:
        """Get Kubernetes resources.

        Supports: pods, deployments, services, configmaps, secrets

        Args:
            resource_type: Type of resource (pods, deployments, services)
            namespace: Kubernetes namespace
            name: Optional specific resource name (not used for listing)
            label_selector: Optional label selector to filter resources

        Returns:
            Resource data from MCP server (typically string or dict)

        Raises:
            ValidationError: If input parameters are invalid
            ToolError: If MCP tool call fails
        """
        self._validate_resource_type(resource_type)
        self._validate_namespace(namespace)
        if name:
            self._validate_resource_name(name)

        # Map resource type to MCP tool
        resource_lower = resource_type.lower()
        tool_map = {
            "pods": "kubectl_get_pods",
            "pod": "kubectl_get_pods",
            "deployments": "kubectl_get_deployments",
            "deployment": "kubectl_get_deployments",
            "deploy": "kubectl_get_deployments",
            "services": "kubectl_get_services",
            "service": "kubectl_get_services",
            "svc": "kubectl_get_services",
            "configmaps": "kubectl_get_configmaps",
            "configmap": "kubectl_get_configmaps",
            "cm": "kubectl_get_configmaps",
            "secrets": "kubectl_get_secrets",  # pragma: allowlist secret
            "secret": "kubectl_get_secrets",  # pragma: allowlist secret
        }

        tool_name = tool_map.get(resource_lower)
        if not tool_name:
            raise ValidationError(
                f"Resource type '{resource_type}' is not supported by legacy kubectl_get. "
                "Use get() instead.",
                field="resource_type", value=resource_type, agent_name=self.client_name
            )
        
        args: dict[str, Any] = {"namespace": namespace}
        if label_selector:
            args["label_selector"] = label_selector
        result = await self.call_tool(tool_name, args)

        logger.info(
            "retrieved resources",
            extra={
                "component": "k8s_tools",
                "resource_type": resource_type,
                "namespace": namespace,
            }
        )
        return result

    async def kubectl_get_all_namespaces(
        self,
        resource_type: str,
        label_selector: str | None = None
    ) -> Any:
        """Get Kubernetes resources across all namespaces.

        Supports: pods, deployments, services

        Args:
            resource_type: Type of resource (pods, deployments, services)
            label_selector: Optional label selector to filter resources

        Returns:
            Resource data from MCP server across all namespaces

        Raises:
            ValidationError: If input parameters are invalid
            ToolError: If MCP tool call fails
        """
        self._validate_resource_type(resource_type)

        # Map resource type to all-namespaces MCP tool
        resource_lower = resource_type.lower()
        tool_map = {
            "pods": "kubectl_get_pods_all_namespaces",
            "pod": "kubectl_get_pods_all_namespaces",
            "deployments": "kubectl_get_deployments_all_namespaces",
            "deployment": "kubectl_get_deployments_all_namespaces",
            "deploy": "kubectl_get_deployments_all_namespaces",
            "services": "kubectl_get_services_all_namespaces",
            "service": "kubectl_get_services_all_namespaces",
            "svc": "kubectl_get_services_all_namespaces",
        }

        tool_name = tool_map.get(resource_lower)
        if not tool_name:
            raise ValidationError(
                f"Resource type '{resource_type}' is not supported by legacy kubectl_get_all_namespaces. "
                "Use get() instead.",
                field="resource_type", value=resource_type, agent_name=self.client_name
            )

        args: dict[str, Any] = {}
        if label_selector:
            args["label_selector"] = label_selector

        result = await self.call_tool(tool_name, args)
        logger.info(
            "retrieved resources from all namespaces",
            extra={
                "component": "k8s_tools",
                "resource_type": resource_type,
            }
        )
        return result

    async def kubectl_describe(
        self,
        resource_type: str,
        name: str,
        namespace: str = "default"
    ) -> str:
        """Get detailed information about a specific resource.

        Note: Currently only supports 'pods' resource type via MCP server.

        Args:
            resource_type: Type of resource
            name: Resource name
            namespace: Kubernetes namespace

        Returns:
            Detailed resource information

        Raises:
            ValidationError: If input parameters are invalid
            ToolError: If MCP tool call fails
        """
        self._validate_resource_type(resource_type)
        self._validate_resource_name(name)
        self._validate_namespace(namespace)

        # Currently MCP server only supports kubectl_describe_pod
        if resource_type.lower() in ("pod", "pods"):
            args = {
                "namespace": namespace,
                "pod": name
            }
            result = await self.call_tool("kubectl_describe_pod", args)
        else:
            raise ValidationError(
                f"Resource type '{resource_type}' is not supported by legacy kubectl_describe. "
                "Use describe() instead.",
                field="resource_type", value=resource_type, agent_name=self.client_name
            )

        logger.info(
            "described resource",
            extra={
                "component": "k8s_tools",
                "resource_type": resource_type,
                "name": name,
                "namespace": namespace,
            }
        )
        return result

    async def kubectl_logs(
        self,
        pod_name: str,
        namespace: str = "default",
        container: str | None = None,
        tail: int | None = None
    ) -> str:
        """Get pod logs.

        Args:
            pod_name: Pod name
            namespace: Kubernetes namespace
            container: Specific container name
            tail: Number of lines from the end

        Returns:
            Log output

        Raises:
            ValidationError: If input parameters are invalid
            ToolError: If MCP tool call fails
        """
        self._validate_resource_name(pod_name, field="pod_name")
        self._validate_namespace(namespace)
        if container:
            self._validate_resource_name(container, field="container")
        self._validate_tail_lines(tail)

        # MCP server uses: namespace, pod, container, tail_lines
        args: dict[str, Any] = {
            "namespace": namespace,
            "pod": pod_name
        }
        if container:
            args["container"] = container
        if tail is not None:
            args["tail_lines"] = tail

        result = await self.call_tool("kubectl_logs", args)
        logger.info(
            "retrieved pod logs",
            extra={
                "component": "k8s_tools",
                "pod_name": pod_name,
                "namespace": namespace,
            }
        )
        return result

    async def kubectl_events(
        self,
        namespace: str = "default",
        resource_type: str | None = None,
        name: str | None = None
    ) -> Any:
        """Get cluster events.

        Args:
            namespace: Kubernetes namespace
            resource_type: Optional filter by resource type (used to build field_selector)
            name: Optional filter by resource name (used to build field_selector)

        Returns:
            List of events from MCP server

        Raises:
            ValidationError: If input parameters are invalid
            ToolError: If MCP tool call fails
        """
        self._validate_namespace(namespace)
        if resource_type:
            self._validate_resource_type(resource_type)
        if name:
            self._validate_resource_name(name)

        # MCP server uses: namespace, field_selector
        args: dict[str, Any] = {"namespace": namespace}

        # Build field_selector if name provided
        if name:
            selectors = [f"involvedObject.name={name}"]
            if resource_type:
                kind = RESOURCE_KIND_MAP.get(resource_type.lower())
                if kind:
                    selectors.append(f"involvedObject.kind={kind}")
            args["field_selector"] = ",".join(selectors)

        result = await self.call_tool("kubectl_events", args)
        logger.info(
            "retrieved events",
            extra={
                "component": "k8s_tools",
                "namespace": namespace,
            }
        )
        return result

    # =========================================================================
    # Validation Methods
    # =========================================================================

    def _validate_namespace(self, namespace: str) -> None:
        """Validate Kubernetes namespace name (RFC 1123 label)."""
        if not namespace:
            raise ValidationError(
                "Namespace cannot be empty",
                field="namespace",
                value=namespace,
                agent_name=self.client_name
            )
        if not K8S_NAMESPACE_PATTERN.match(namespace):
            raise ValidationError(
                f"Invalid namespace format: '{namespace}'. Must be lowercase alphanumeric "
                "with hyphens, start/end with alphanumeric, max 63 characters",
                field="namespace",
                value=namespace,
                agent_name=self.client_name
            )

    def _validate_resource_name(self, name: str, field: str = "name") -> None:
        """Validate Kubernetes resource name (RFC 1123 subdomain)."""
        if not name:
            raise ValidationError(
                f"{field.replace('_', ' ').title()} cannot be empty",
                field=field,
                value=name,
                agent_name=self.client_name
            )
        if not K8S_NAME_PATTERN.match(name):
            raise ValidationError(
                f"Invalid {field} format: '{name}'. Must be lowercase alphanumeric "
                "with hyphens, start/end with alphanumeric, max 253 characters",
                field=field,
                value=name,
                agent_name=self.client_name
            )

    def _validate_resource_type(self, resource_type: str) -> None:
        """Validate Kubernetes resource type."""
        if not resource_type:
            raise ValidationError(
                "Resource type cannot be empty",
                field="resource_type",
                value=resource_type,
                agent_name=self.client_name
            )
        if resource_type.lower() not in VALID_RESOURCE_TYPES:
            raise ValidationError(
                f"Invalid resource type: '{resource_type}'. Valid types: "
                f"{', '.join(sorted(VALID_RESOURCE_TYPES))}",
                field="resource_type",
                value=resource_type,
                agent_name=self.client_name
            )

    def _validate_tail_lines(self, tail: int | None) -> None:
        """Validate tail lines parameter for log retrieval."""
        if tail is not None and tail < 0:
            raise ValidationError(
                f"Tail lines must be non-negative, got: {tail}",
                field="tail_lines",
                value=str(tail),
                agent_name=self.client_name
            )

    # =========================================================================
    # Write Operations
    # =========================================================================

    async def kubectl_scale(
        self,
        deployment: str,
        replicas: int,
        namespace: str = "default"
    ) -> str:
        """Scale a deployment to specified replica count.

        Args:
            deployment: Deployment name
            replicas: Target replica count (>= 0)
            namespace: Kubernetes namespace

        Returns:
            Scale operation result

        Raises:
            ValidationError: If input parameters are invalid
            ToolError: If MCP tool call fails
        """
        self._validate_resource_name(deployment, field="deployment")
        self._validate_namespace(namespace)
        if replicas < 0:
            raise ValidationError(
                "Replicas must be >= 0",
                field="replicas",
                value=str(replicas),
                agent_name=self.client_name
            )

        args = {
            "namespace": namespace,
            "deployment": deployment,
            "replicas": replicas
        }
        result = await self.call_tool("kubectl_scale", args)
        logger.info(
            "scaled deployment",
            extra={
                "component": "k8s_tools",
                "deployment": deployment,
                "replicas": replicas,
                "namespace": namespace,
            }
        )
        return result

    async def kubectl_rollout_restart(
        self,
        deployment: str,
        namespace: str = "default"
    ) -> str:
        """Trigger a rolling restart of a deployment.

        Args:
            deployment: Deployment name
            namespace: Kubernetes namespace

        Returns:
            Rollout restart result

        Raises:
            ValidationError: If input parameters are invalid
            ToolError: If MCP tool call fails
        """
        self._validate_resource_name(deployment, field="deployment")
        self._validate_namespace(namespace)

        args = {
            "namespace": namespace,
            "deployment": deployment
        }
        result = await self.call_tool("kubectl_rollout_restart", args)
        logger.info(
            "triggered rollout restart",
            extra={
                "component": "k8s_tools",
                "deployment": deployment,
                "namespace": namespace,
            }
        )
        return result

    async def kubectl_apply(
        self,
        manifest: str,
        namespace: str = "default"
    ) -> str:
        """Apply a YAML manifest to the cluster.

        Supports: ConfigMap, Secret, Pod, Service

        Args:
            manifest: YAML manifest content
            namespace: Target namespace (used if not specified in manifest)

        Returns:
            Apply operation result

        Raises:
            ValidationError: If input parameters are invalid
            ToolError: If MCP tool call fails
        """
        self._validate_namespace(namespace)
        if not manifest or not manifest.strip():
            raise ValidationError(
                "Manifest cannot be empty",
                field="manifest",
                value="(empty)",
                agent_name=self.client_name
            )

        # Parse YAML to validate structure and check for dangerous kinds
        try:
            # Use safe_load_all to handle both single and multi-doc YAML
            docs = list(yaml.safe_load_all(manifest))
        except yaml.YAMLError as e:
            raise ValidationError(
                f"Invalid YAML manifest: {e}",
                field="manifest",
                value="(malformed)",
                agent_name=self.client_name
            )

        # Validate that manifest parsed successfully (not empty)
        if not docs or all(doc is None for doc in docs):
            raise ValidationError(
                "Manifest is empty after parsing",
                field="manifest",
                value="(empty)",
                agent_name=self.client_name
            )

        # Check for dangerous kinds in any document
        for doc in docs:
            if isinstance(doc, dict):
                kind = doc.get("kind", "")
                if kind in DANGEROUS_KINDS:
                    raise ValidationError(
                        f"Cannot apply resource kind '{kind}' via kubectl_apply. "
                        f"Use kubectl directly with explicit approval for security-sensitive resources.",
                        field="manifest",
                        value=kind,
                        agent_name=self.client_name
                    )

        args = {
            "namespace": namespace,
            "manifest": manifest
        }
        result = await self.call_tool("kubectl_apply", args)
        logger.info(
            "applied manifest",
            extra={
                "component": "k8s_tools",
                "namespace": namespace,
            }
        )
        return result

    async def kubectl_delete(
        self,
        resource_type: str,
        name: str,
        namespace: str = "default"
    ) -> str:
        """Delete a Kubernetes resource.

        Supports: pod, configmap, secret, deployment

        Args:
            resource_type: Type of resource (pod, configmap, secret, deployment)
            name: Resource name
            namespace: Kubernetes namespace

        Returns:
            Delete operation result

        Raises:
            ValidationError: If input parameters are invalid
            ToolError: If MCP tool call fails
        """
        valid_delete_types = {"pod", "pods", "configmap", "configmaps", "cm",
                              "secret", "secrets", "deployment", "deployments", "deploy"}
        if resource_type.lower() not in valid_delete_types:
            raise ValidationError(
                f"Invalid resource type: '{resource_type}'. "
                f"Supported: pod, configmap, secret, deployment",
                field="resource_type",
                value=resource_type,
                agent_name=self.client_name
            )

        self._validate_resource_name(name, field="name")
        self._validate_namespace(namespace)

        args = {
            "namespace": namespace,
            "resource_type": resource_type,
            "name": name
        }
        result = await self.call_tool("kubectl_delete", args)
        logger.info(
            "deleted resource",
            extra={
                "component": "k8s_tools",
                "resource_type": resource_type,
                "name": name,
                "namespace": namespace,
            }
        )
        return result
