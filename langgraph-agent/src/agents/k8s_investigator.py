"""Kubernetes cluster investigation agent."""

import asyncio
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

from ..config import (
    MAX_CONTEXT_CHARS,
    MAX_PODS_TO_LOG,
    LOG_MIN_TAIL_LINES,
    LOG_MAX_TAIL_LINES,
    LOG_CHARS_PER_LINE_ESTIMATE,
    LOG_DEDUP_ENABLED,
    LOG_DEDUP_THRESHOLD,
    create_diagnosis_llm,
)
from ..exceptions import ToolError
from ..state import AgentState
from ..tools.k8s_tools import K8sTools
from ..utils.log_processor import process_pod_logs
from ..utils.pod_name_parser import is_valid_k8s_pod_name
from ..utils.llm_utils import invoke_llm_with_retry

# Extracted modules for better organization
from .pod_analysis import (
    is_pod_running_and_ready,
    is_pod_in_error_state,
    analyze_deployment_pods,
    format_pod_list,
    format_no_pods_status,
    format_deployment_status,
    infer_deployment_status_from_pods,
)

logger = logging.getLogger(__name__)

# Agent identifier for error context
AGENT_NAME = "K8sInvestigator"


class K8sInvestigator:
    """Investigates Kubernetes cluster to diagnose issues.

    This agent systematically investigates Kubernetes resources to identify
    issues affecting deployments, services, and pods. It fetches resources in
    parallel for efficiency and classifies issues to avoid false dependency
    assumptions.

    SECURITY: This agent uses K8sTools(readonly=True) which enforces read-only access.
    Write operations are blocked at the tool level, providing defense-in-depth.
    Only K8sRemediationExecutor should have access to write operations.
    """

    def __init__(self, k8s_tools: K8sTools) -> None:
        """Initialize the K8s investigator.

        Args:
            k8s_tools: Read-only Kubernetes tools client for cluster operations.
                       Must be K8sTools(readonly=True) to ensure no write operations.
        """
        self.llm = create_diagnosis_llm()
        self.tools = k8s_tools

    async def run(self, state: AgentState) -> AgentState:
        """Investigate the Kubernetes cluster based on affected resources from ticket.

        Uses the specific deployment/service/namespace extracted by JiraAgent.
        If namespace is not specified, attempts to discover it automatically.

        Args:
            state: Current agent state with ticket and resource information

        Returns:
            Updated state with cluster findings
        """
        logger.info("Starting Kubernetes cluster investigation")

        try:
            await self._ensure_session_health()

            # Discover namespace if not explicitly set
            discovered_ns = await self._discover_namespace(state)
            if discovered_ns:
                state["namespace"] = discovered_ns
                logger.info(f"Using discovered namespace: {discovered_ns}")

            investigation_context = self._extract_investigation_context(state)
            findings = await self._perform_investigation(state, investigation_context)
            self._update_state_with_findings(state, findings)
            self._log_investigation_summary(state)

        except ToolError as e:
            self._handle_tool_error(state, e)
        except Exception as e:
            self._handle_unexpected_error(state, e)

        return state

    async def _ensure_session_health(self) -> None:
        """Proactively ensure healthy MCP session before investigation."""
        if not await self.tools.ensure_healthy_session():
            logger.warning(f"[{AGENT_NAME}] Could not establish healthy K8s MCP session")

    async def _discover_namespace(self, state: dict[str, Any]) -> str | None:
        """Discover namespace when not specified in ticket.

        Strategy:
        1. If namespace already set in state, use it
        2. If affected_namespaces from ticket extraction, use first one
        3. Search all namespaces for deployment/service by name
        4. Return None if ambiguous (multiple namespaces have same resource)

        Args:
            state: Current agent state dictionary

        Returns:
            Discovered namespace string, or None if ambiguous/not found
        """
        # Already have a namespace explicitly set
        if state.get("namespace"):
            return state["namespace"]

        # Check if LLM extracted namespaces from ticket
        affected_resources = state.get("affected_resources", {})
        affected_namespaces = affected_resources.get("namespaces", [])
        if affected_namespaces:
            logger.info(f"Using namespace from ticket: {affected_namespaces[0]}")
            return affected_namespaces[0]

        # No namespace - search cluster for the deployment
        deployment_names = affected_resources.get("deployments", [])

        if deployment_names:
            target = deployment_names[0]
            logger.info(f"Searching all namespaces for deployment: {target}")

            try:
                all_deployments = await self.tools.kubectl_get_all_namespaces("deployments")
                namespaces = self._extract_namespaces_for_resource(all_deployments, target)

                if len(namespaces) == 1:
                    logger.info(f"Discovered namespace: {namespaces[0]}")
                    return namespaces[0]
                elif len(namespaces) > 1:
                    logger.warning(f"Ambiguous: '{target}' in namespaces: {namespaces}")
                    state["discovered_namespace_candidates"] = [
                        {"namespace": ns, "resource": target, "type": "deployment"}
                        for ns in namespaces
                    ]
                    return None
            except Exception as e:
                logger.warning(f"Deployment search failed: {e}")

        logger.warning("Could not discover namespace - no resources to search")
        return None

    def _extract_namespaces_for_resource(self, output: str, resource_name: str) -> list[str]:
        """Extract namespaces where a specific resource exists.

        Parses kubectl output with NAMESPACE column to find all namespaces
        containing the exact resource name.

        Args:
            output: Raw output from kubectl_get_all_namespaces
            resource_name: Exact name of the resource to find

        Returns:
            Sorted list of namespace names containing the resource
        """
        namespaces = set()
        lines = output.strip().split('\n')
        for line in lines[1:]:  # Skip header
            parts = line.split('\t')
            if len(parts) >= 2:
                ns, name = parts[0], parts[1]
                if name == resource_name:
                    namespaces.add(ns)
        return sorted(namespaces)

    def _extract_investigation_context(self, state: AgentState) -> dict[str, Any]:
        """Extract investigation context from state.

        Args:
            state: Current agent state

        Returns:
            Dictionary with namespace, deployments, and services
        """
        affected_resources = state.get("affected_resources", {})
        affected_deployments = affected_resources.get("deployments", [])
        affected_services = affected_resources.get("services", [])

        context = {
            "namespace": state.get("namespace"),
            "affected_deployments": affected_deployments,
            "affected_services": affected_services,
            "affected_deployment": affected_deployments[0] if affected_deployments else None,
            "affected_service": affected_services[0] if affected_services else None
        }

        logger.info(
            f"Investigating: deployment={context['affected_deployment']}, "
            f"service={context['affected_service']}, namespace={context['namespace']}"
        )

        return context

    def _discover_referenced_resources(
        self,
        deployment_data: str
    ) -> dict[str, list[str]]:
        """Parse REFERENCED_RESOURCES section from deployment data.

        Extracts ConfigMaps, Secrets, ServiceAccounts, and PVCs that a
        deployment references in its spec (envFrom, env valueFrom, volumes).
        Handles multiple deployments by aggregating and deduplicating.

        Args:
            deployment_data: Raw formatted deployment output from MCP tools

        Returns:
            Dictionary with keys: configmaps, secrets, service_accounts, pvcs
            Each value is a sorted, deduplicated list of resource names.
        """
        result: dict[str, set[str]] = {
            "configmaps": set(),
            "secrets": set(),
            "service_accounts": set(),
            "pvcs": set(),
        }

        if not deployment_data:
            return {k: [] for k in result}

        key_mapping = {
            "CONFIGMAPS": "configmaps",
            "SECRETS": "secrets",  # pragma: allowlist secret
            "SERVICE_ACCOUNT": "service_accounts",
            "PVCS": "pvcs",
        }

        for line in deployment_data.split("\n"):
            stripped = line.strip()
            for prefix, key in key_mapping.items():
                if stripped.startswith(f"{prefix}:"):
                    value_part = stripped[len(prefix) + 1:].strip()
                    if value_part:
                        names = [n.strip() for n in value_part.split(",") if n.strip()]
                        result[key].update(names)

        return {k: sorted(v) for k, v in result.items()}

    def _merge_discovered_resources(
        self,
        state: AgentState,
        discovered: dict[str, list[str]]
    ) -> None:
        """Merge discovered resources into affected_resources in state.

        Adds newly discovered configmaps and secrets to the affected_resources
        dict so they get picked up by the parallel fetch step.
        Deduplicates against already-known affected resources.

        SECURITY: For secrets, only the names are tracked for existence
        checking. Secret data is NEVER fetched or exposed.

        Args:
            state: Current agent state to update in-place
            discovered: Output from _discover_referenced_resources
        """
        affected = state.get("affected_resources") or {}

        for resource_key in ("configmaps", "secrets"):
            existing = set(affected.get(resource_key, []))
            new_names = set(discovered.get(resource_key, []))
            merged = sorted(existing | new_names)
            affected[resource_key] = merged

        state["affected_resources"] = affected

    async def _discover_and_merge_resources(
        self,
        state: AgentState,
        context: dict[str, Any]
    ) -> None:
        """Fetch deployment data and merge discovered resource references into state.

        Calls kubectl_get for deployments to get formatted output including
        REFERENCED_RESOURCES section, then parses and merges into affected_resources
        so the parallel fetch step picks them up.

        Args:
            state: Current agent state to update
            context: Investigation context with namespace and deployment info
        """
        if not context.get("affected_deployment"):
            return

        try:
            deployment_data = await self.tools.kubectl_get(
                "deployments",
                namespace=context["namespace"],
                label_selector=f"app={context['affected_deployment']}"
            )
            if not deployment_data or "No deployments found" in str(deployment_data):
                return

            discovered = self._discover_referenced_resources(str(deployment_data))
            self._merge_discovered_resources(state, discovered)

            # Log what was discovered
            total = sum(len(v) for v in discovered.values())
            if total > 0:
                logger.info(
                    f"Auto-discovered {total} referenced resources from deployment spec: "
                    f"configmaps={discovered['configmaps']}, "
                    f"secrets={discovered['secrets']}"
                )
        except Exception as e:
            logger.warning(f"Resource auto-discovery failed (non-fatal): {e}")

    async def _perform_investigation(
        self,
        state: AgentState,
        context: dict[str, Any]
    ) -> dict[str, Any]:
        """Perform complete cluster investigation.

        Args:
            state: Current agent state
            context: Investigation context

        Returns:
            Dictionary with all investigation findings
        """
        findings = self._initialize_findings(context)

        # Fetch core resources (pods, deployment status, service, endpoints)
        await self._fetch_core_resources(findings, context)

        # Auto-discover referenced resources from deployment spec
        await self._discover_and_merge_resources(state, context)

        parallel_resources = await self._fetch_additional_resources(state, context["namespace"])
        findings["resources"].update(parallel_resources)

        events = await self.tools.kubectl_events(namespace=context["namespace"])
        findings["events"] = events

        await self._fetch_pod_logs_if_needed(findings, context)

        analysis = await self._analyze_cluster_state(findings, state)
        findings["preliminary_findings"] = analysis

        return findings

    def _initialize_findings(self, context: dict[str, Any]) -> dict[str, Any]:
        """Initialize findings dictionary with context.

        Args:
            context: Investigation context

        Returns:
            Initialized findings dictionary
        """
        return {
            "affected_deployment": context["affected_deployment"],
            "affected_service": context["affected_service"],
            "affected_deployments": context["affected_deployments"],
            "affected_services": context["affected_services"],
            "namespace": context["namespace"],
            "resources": {},
            "logs": {},
            "events": [],
            "preliminary_findings": ""
        }

    async def _fetch_core_resources(
        self,
        findings: dict[str, Any],
        context: dict[str, Any]
    ) -> None:
        """Fetch core resources: pods, deployment status, service, endpoints.

        This helper consolidates the common resource-fetching pattern used in both
        `_perform_investigation` and `run_verification_only`. It:
        1. Fetches pods with label-selector fallback
        2. Infers deployment status from pods
        3. Fetches service and endpoints

        Args:
            findings: Findings dictionary to update with fetched resources
            context: Investigation context with deployment, service, namespace
        """
        affected_deployment = context["affected_deployment"]
        affected_service = context["affected_service"]
        namespace = context["namespace"]

        # Step 1: Fetch pods
        if affected_deployment:
            label_selector = f"app={affected_deployment}"
            pods_status = await self.tools.kubectl_get(
                "pods", namespace=namespace, label_selector=label_selector
            )
            # Fallback if no pods found with label
            if "No pods found" in str(pods_status) or not pods_status:
                pods_status = await self.tools.kubectl_get("pods", namespace=namespace)
        else:
            pods_status = await self.tools.kubectl_get("pods", namespace=namespace)
        findings["resources"]["pods"] = pods_status
        logger.info(f"Pods status: {str(pods_status)[:500]}")

        # Step 2: Infer deployment status from pods
        if affected_deployment:
            inferred_status = infer_deployment_status_from_pods(pods_status, affected_deployment)
            findings["resources"]["deployment"] = inferred_status
            logger.info(f"Inferred deployment status: {inferred_status}")

        # Step 3: Fetch service and endpoints
        if affected_service:
            service_status = await self.tools.kubectl_get(
                "services", namespace=namespace, name=affected_service
            )
            findings["resources"]["service"] = service_status

            endpoints = await self.tools.kubectl_get(
                "endpoints", namespace=namespace, name=affected_service
            )
            findings["resources"]["endpoints"] = endpoints

    async def _fetch_additional_resources(
        self,
        state: AgentState,
        namespace: str
    ) -> dict[str, Any]:
        """Fetch additional resources in parallel.

        Args:
            state: Current agent state
            namespace: Kubernetes namespace

        Returns:
            Dictionary of additional resources
        """
        affected_resources = state.get("affected_resources", {})
        return await self._fetch_resources_parallel(
            namespace=namespace,
            affected_statefulsets=affected_resources.get("statefulsets", []),
            affected_daemonsets=affected_resources.get("daemonsets", []),
            affected_configmaps=affected_resources.get("configmaps", []),
            affected_secrets=affected_resources.get("secrets", [])
        )

    async def _fetch_pod_logs_if_needed(
        self,
        findings: dict[str, Any],
        context: dict[str, Any]
    ) -> None:
        """Fetch pod logs if needed based on pod status.

        Args:
            findings: Findings dictionary to update
            context: Investigation context
        """
        pods_str = str(findings["resources"].get("pods", ""))
        if not self._should_fetch_pod_logs(pods_str):
            return

        valid_pods = self._select_pods_for_logging(pods_str, context["affected_deployments"])
        if not valid_pods:
            return

        tail_lines = self._calculate_dynamic_tail(len(valid_pods))
        raw_pod_logs = await self._fetch_pod_logs_parallel(
            valid_pods,
            context["namespace"],
            tail_lines=tail_lines
        )
        if LOG_DEDUP_ENABLED:
            processed_logs = process_pod_logs(raw_pod_logs, threshold=LOG_DEDUP_THRESHOLD)
        else:
            processed_logs = raw_pod_logs
        findings["logs"].update(processed_logs)


    def _update_state_with_findings(
        self,
        state: AgentState,
        findings: dict[str, Any]
    ) -> None:
        """Update state with investigation findings.

        Args:
            state: Current agent state
            findings: Investigation findings
        """
        # Removed: discovered_dependencies, unrelated_issues, dependency_evidence
        # These were part of dependency analysis which has been simplified
        state["cluster_findings"] = findings

    def _log_investigation_summary(self, state: AgentState) -> None:
        """Log investigation summary.

        Args:
            state: Current agent state with findings
        """
        logger.info("Kubernetes investigation complete")

    def _handle_tool_error(self, state: AgentState, error: ToolError) -> None:
        """Handle tool errors during investigation.

        Args:
            state: Current agent state
            error: Tool error exception
        """
        logger.error(f"[{AGENT_NAME}] Tool error during investigation: {error}", exc_info=True)
        state["cluster_findings"] = {
            "error": str(error),
            "error_type": "tool_error",
            "recoverable": True
        }

    def _handle_unexpected_error(self, state: AgentState, error: Exception) -> None:
        """Handle unexpected errors during investigation.

        Args:
            state: Current agent state
            error: Exception
        """
        logger.error(f"[{AGENT_NAME}] Unexpected error during investigation: {error}")
        state["cluster_findings"] = {
            "error": f"Investigation failed: {str(error)}",
            "error_type": "unexpected_error",
            "recoverable": False
        }

    def _should_fetch_pod_logs(self, pods_output: str) -> bool:
        """Check if pod logs should be fetched based on pod status.

        Args:
            pods_output: Raw output from kubectl get pods

        Returns:
            True if logs should be fetched (pods exist and may have issues)
        """
        status_indicators = ("Running", "Error", "CrashLoop")
        return any(indicator in pods_output for indicator in status_indicators)

    def _extract_pod_names(self, pods_output: str) -> list[str]:
        """Extract actual pod names from kubectl get pods output.

        Args:
            pods_output: Raw output from kubectl get pods

        Returns:
            List of valid pod names
        """
        pod_names = []
        lines = pods_output.split('\n')
        for line in lines:
            if not line or 'NAME' in line or 'No resources' in line:
                continue
            parts = line.split()
            if len(parts) >= 1:
                pod_name = parts[0]
                if is_valid_k8s_pod_name(pod_name):
                    pod_names.append(pod_name)
        return pod_names

    def _select_pods_for_logging(
        self,
        pods_output: str,
        affected_deployments: list[str]
    ) -> list[str]:
        """Select only ticket-related pods for log fetching.

        IMPORTANT: Only fetches logs for pods belonging to deployments mentioned
        in the ticket. Does NOT include unrelated pods even if they are in error
        state - this prevents investigation from being distracted by other issues.

        Args:
            pods_output: Raw output from kubectl get pods
            affected_deployments: List of deployment names from the ticket

        Returns:
            List of pod names to fetch logs for, limited to MAX_PODS_TO_LOG
        """
        ticket_pods = []

        lines = pods_output.split('\n')
        for line in lines:
            if not line or 'NAME' in line or 'No resources' in line:
                continue

            parts = line.split()
            if len(parts) < 3:
                continue

            pod_name = parts[0]
            if not is_valid_k8s_pod_name(pod_name):
                continue

            # Only include pods from ticket-mentioned deployments
            is_ticket_pod = any(
                dep.lower() in pod_name.lower()
                for dep in affected_deployments
            ) if affected_deployments else False

            if is_ticket_pod:
                ticket_pods.append(pod_name)
            # Skip ALL other pods - including unrelated error pods
            # The investigation should focus ONLY on the ticket's target

        if ticket_pods:
            logger.info(f"Selected {len(ticket_pods)} ticket-related pods for logging")
        else:
            logger.info("No ticket-related pods found for logging")

        return ticket_pods[:MAX_PODS_TO_LOG]

    def _calculate_dynamic_tail(self, num_pods: int) -> int:
        """Calculate dynamic tail line count based on available context space.

        Distributes MAX_CONTEXT_CHARS["logs"] evenly across pods,
        then converts to line count using LOG_CHARS_PER_LINE_ESTIMATE.

        Args:
            num_pods: Number of pods that will have logs fetched

        Returns:
            Number of tail lines to fetch per pod
        """
        if num_pods <= 0:
            return LOG_MAX_TAIL_LINES

        max_chars_per_pod = MAX_CONTEXT_CHARS["logs"] // num_pods
        tail_lines = max_chars_per_pod // LOG_CHARS_PER_LINE_ESTIMATE

        # Clamp to configured min/max
        return max(LOG_MIN_TAIL_LINES, min(LOG_MAX_TAIL_LINES, tail_lines))

    async def _fetch_resources_parallel(
        self,
        namespace: str,
        affected_statefulsets: list[str],
        affected_daemonsets: list[str],
        affected_configmaps: list[str],
        affected_secrets: list[str]
    ) -> dict[str, Any]:
        """Fetch multiple K8s resource types in parallel.

        This significantly reduces investigation time when multiple resource
        types need to be fetched (typically 5-6 parallel calls instead of
        sequential).

        Args:
            namespace: Kubernetes namespace
            affected_statefulsets: List of StatefulSet names to check
            affected_daemonsets: List of DaemonSet names to check
            affected_configmaps: List of ConfigMap names to check
            affected_secrets: List of Secret names to check

        Returns:
            Dictionary mapping resource type to fetched data
        """
        results: dict[str, Any] = {}

        # Build list of (resource_type, key_name, should_fetch) tuples
        fetch_tasks: list[tuple[str, str, bool]] = [
            ("services", "service", True),  # Always fetch
            ("endpoints", "endpoints", True),  # Always fetch
            ("statefulsets", "statefulset", bool(affected_statefulsets)),
            ("daemonsets", "daemonset", bool(affected_daemonsets)),
            ("configmaps", "configmap", bool(affected_configmaps)),
            ("secrets", "secret", bool(affected_secrets)),
        ]

        # Filter to only tasks that should be fetched
        active_tasks = [
            (resource_type, key_name)
            for resource_type, key_name, should_fetch in fetch_tasks
            if should_fetch
        ]

        if not active_tasks:
            return results

        # Log what we're fetching
        resource_types = [rt for rt, _ in active_tasks]
        logger.info(f"Fetching {len(active_tasks)} resource types in parallel: {resource_types}")

        # Create coroutines for parallel execution
        async def fetch_resource(resource_type: str, key_name: str) -> tuple[str, Any]:
            """Fetch a single resource type and return (key_name, result)."""
            try:
                data = await self.tools.kubectl_get(resource_type, namespace=namespace)
                return (key_name, data)
            except Exception as e:
                logger.warning(f"Failed to fetch {resource_type}: {e}")
                return (key_name, f"Error fetching {resource_type}: {e}")

        # Execute all fetches in parallel
        fetch_results = await asyncio.gather(
            *[fetch_resource(rt, kn) for rt, kn in active_tasks],
            return_exceptions=True
        )

        # Process results
        for result in fetch_results:
            if isinstance(result, Exception):
                logger.error(f"Parallel fetch exception: {result}")
                continue
            key_name, data = result
            results[key_name] = data

        return results

    async def _fetch_pod_logs_parallel(
        self,
        pod_names: list[str],
        namespace: str,
        tail_lines: int = LOG_MAX_TAIL_LINES
    ) -> dict[str, str]:
        """Fetch logs for multiple pods in parallel.

        This significantly reduces log collection time when investigating
        multiple pods (e.g., 3 pods fetched in ~1 network round-trip instead
        of 3 sequential calls).

        Args:
            pod_names: List of pod names to fetch logs for
            namespace: Kubernetes namespace
            tail_lines: Number of log lines to fetch per pod (dynamic based on context budget)

        Returns:
            Dictionary mapping pod name to log content
        """
        if not pod_names:
            return {}

        logger.info(f"Fetching logs for {len(pod_names)} pods in parallel (tail={tail_lines}): {pod_names}")

        async def fetch_single_pod_logs(pod_name: str) -> tuple[str, str]:
            """Fetch logs for a single pod and return (pod_name, logs)."""
            try:
                logs = await self.tools.kubectl_logs(
                    pod_name=pod_name,
                    namespace=namespace,
                    tail=tail_lines
                )
                return (pod_name, logs)
            except Exception as e:
                logger.warning(f"Failed to fetch logs for pod {pod_name}: {e}")
                return (pod_name, f"Error fetching logs: {e}")

        # Execute all log fetches in parallel
        results = await asyncio.gather(
            *[fetch_single_pod_logs(pod) for pod in pod_names],
            return_exceptions=True
        )

        # Process results
        pod_logs: dict[str, str] = {}
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Log fetch exception: {result}")
                continue
            pod_name, logs = result
            pod_logs[pod_name] = logs

        return pod_logs

    async def _analyze_cluster_state(
        self,
        findings: dict[str, Any],
        state: AgentState
    ) -> str:
        """Let LLM analyze gathered K8s data and identify root issue.

        Includes dependency classification to prevent false assumptions.
        Uses historical ticket data as hints to guide investigation.

        Args:
            findings: Investigation findings
            state: Current agent state

        Returns:
            LLM analysis as string
        """
        context = self._build_analysis_context(findings, state)
        prompt = self._build_analysis_prompt(findings, context)

        messages = [HumanMessage(content=prompt)]
        response = await self.llm.ainvoke(messages)
        return response.content

    def _build_analysis_context(
        self,
        findings: dict[str, Any],
        state: AgentState
    ) -> dict[str, str]:
        """Build context sections for LLM analysis.

        Args:
            findings: Investigation findings
            state: Current agent state

        Returns:
            Dictionary with formatted context sections
        """
        return {
            "historical_hints": self._format_historical_hints(state)
        }

    def _format_historical_hints(self, state: AgentState) -> str:
        """Format historical context from similar tickets.

        Args:
            state: Current agent state

        Returns:
            Formatted historical hints string
        """
        past_resolutions = state.get("past_resolutions", [])
        similar_tickets = state.get("similar_tickets", [])

        if not past_resolutions and not similar_tickets:
            return ""

        hints = "\n### HISTORICAL CONTEXT (use as investigation hints)\n"
        hints += "Similar issues in the past were resolved by:\n"

        if past_resolutions:
            for r in past_resolutions[:3]:
                if isinstance(r, str):
                    hints += f"  - {r}\n"
                elif isinstance(r, dict):
                    hints += f"  - {r.get('resolution', str(r))}\n"

        if similar_tickets:
            for t in similar_tickets[:2]:
                if isinstance(t, dict):
                    key = t.get('key', 'Unknown')
                    summary = t.get('summary', '')
                    resolution = t.get('resolution', '')
                    if resolution:
                        hints += f"  - {key}: {summary[:50]}... → Fixed: {resolution}\n"

        hints += "\nUse these patterns to GUIDE your investigation - check these areas first.\n"
        hints += "But VERIFY with current evidence - don't assume the same issue without proof.\n"

        return hints

    def _build_analysis_prompt(
        self,
        findings: dict[str, Any],
        context: dict[str, str]
    ) -> str:
        """Build comprehensive analysis prompt for LLM.

        Args:
            findings: Investigation findings
            context: Formatted context sections

        Returns:
            Complete prompt string
        """
        affected_deployment = findings.get("affected_deployment", "Unknown")
        affected_service = findings.get("affected_service", "Unknown")
        namespace = findings.get("namespace")

        # Optimized prompt: ~30% fewer tokens while maintaining accuracy
        return f"""Diagnose K8s issue for deployment "{affected_deployment}" in {namespace}.

TARGET: {affected_deployment} (svc: {affected_service})
{context["historical_hints"]}

RESOURCES:
{self._format_resources(findings.get("resources", {}))}

EVENTS: {str(findings.get("events", []))[:MAX_CONTEXT_CHARS["logs"]]}

LOGS: {str(findings.get("logs", {}))[:MAX_CONTEXT_CHARS["logs"]]}

Analyze {affected_deployment} ONLY:
1. State: replicas, pods, readiness
2. Root cause: why is it failing?
3. Evidence: specific data points
4. Fix: concrete action to resolve

RULES:
- Use ONLY verified dependencies (listed above)
- IGNORE unrelated failures in namespace
- 0/0 replicas → scale up (unrelated to other issues)
- No pods → explain why (0 replicas? missing config? scheduling?)
- Use ONLY data above; don't invent resources
"""

    def _format_resources(self, resources: dict[str, Any]) -> str:
        """Format resource data for LLM analysis"""
        formatted = ""
        for resource_type, data in resources.items():
            formatted += f"\n=== {resource_type.upper()} ===\n{data}\n"
        return formatted if formatted else "No resources found"

    # Dependency discovery methods removed - POC simplification

    async def run_verification_only(self, state: AgentState) -> AgentState:
        """
        Lightweight cluster check for verification - NO LLM calls.

        This method is optimized for the verification polling loop where we only
        need to check if resources are healthy, not perform full analysis.

        Unlike run(), this method:
        - Only fetches pods, deployments (inferred), services, endpoints, events
        - Does NOT fetch logs
        - Does NOT discover dependencies
        - Does NOT call LLM for analysis
        - Completes in 2-5 seconds instead of 30-60 seconds

        Args:
            state: Current agent state with affected resources

        Returns:
            Updated state with minimal cluster_findings for verification
        """
        logger.info("Running lightweight verification check (no LLM)")

        try:
            # Ensure healthy MCP session
            if not await self.tools.ensure_healthy_session():
                logger.warning(f"[{AGENT_NAME}] Could not establish healthy K8s MCP session for verification")

            namespace = state.get("namespace")
            affected_resources = state.get("affected_resources", {})
            affected_deployments = affected_resources.get("deployments", [])
            affected_services = affected_resources.get("services", [])
            affected_deployment = affected_deployments[0] if affected_deployments else None
            affected_service = affected_services[0] if affected_services else None

            findings = {
                "affected_deployment": affected_deployment,
                "affected_service": affected_service,
                "affected_deployments": affected_deployments,
                "affected_services": affected_services,
                "namespace": namespace,
                "resources": {},
                "logs": {},  # Empty - not fetched for verification
                "events": [],  # Populated below with recent events
                "preliminary_findings": "Verification check - see resource status"
            }

            # Fetch core resources using consolidated helper
            context = {
                "namespace": namespace,
                "affected_deployment": affected_deployment,
                "affected_service": affected_service,
                "affected_deployments": [],
                "affected_services": []
            }
            await self._fetch_core_resources(findings, context)

            # Step 4: Fetch recent events (cheap, valuable post-remediation context)
            try:
                events = await self.tools.kubectl_events(namespace=namespace)
                findings["events"] = events
            except Exception as e:
                logger.warning(f"[{AGENT_NAME}] Events fetch failed (non-fatal): {e}")

            # Update state with minimal findings
            state["cluster_findings"] = findings

            logger.info(f"Lightweight verification complete for {affected_deployment}")

        except Exception as e:
            logger.error(f"[{AGENT_NAME}] Error during verification check: {e}")
            state["cluster_findings"] = {
                "error": str(e),
                "error_type": "verification_error",
                "recoverable": True
            }

        return state
