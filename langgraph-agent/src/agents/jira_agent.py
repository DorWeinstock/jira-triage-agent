"""Jira specialist agent for reading tickets and posting comments.

Also handles historical ticket search (merged from HistoryAgent).
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from ..state import AgentState
from ..tools.jira_tools import JiraTools
from ..config import (
    create_extraction_llm,
    get_settings,
)
from ..exceptions import ToolError, ValidationError
from ..models import TicketExtraction


# Type alias for raw ticket fields
RawTicketFields = dict[str, str | list[str]]

logger = logging.getLogger(__name__)

# Agent identifier for error context
AGENT_NAME = "JiraAgent"

# Label added after successful investigation comment
LABEL_AI_INVESTIGATED = "ai-agent-investigated"

# Import history search functionality (kept in separate module for code organization)
from .history_agent import HistoryAgent


class JiraAgent:
    """Handles Jira ticket reading, commenting, and historical search.

    Includes history search functionality (merged from HistoryAgent) to provide
    all Jira-related operations in a single agent.
    """

    def __init__(self, jira_tools: JiraTools):
        self.llm = create_extraction_llm()
        self.tools = jira_tools
        # History search is now part of JiraAgent
        self._history_agent = HistoryAgent(jira_tools)

    async def read_ticket(self, state: AgentState) -> AgentState:
        """Read Jira ticket and extract key information.

        This is the entry point of the workflow. It fetches the ticket details
        and populates the state with initial context.
        """
        logger.info(f"Reading Jira ticket: {state.get('ticket_id')}")

        try:
            # 1. Fetch ticket from Jira
            ticket_data = await self._fetch_ticket(state["ticket_id"])
            if ticket_data is None:
                state["ticket_summary"] = "Error fetching ticket: no data returned"
                return await self._search_history(state)

            if "error" in ticket_data:
                logger.error(f"Failed to fetch ticket: {ticket_data['error']}")
                state["ticket_summary"] = (
                    f"Error fetching ticket: {ticket_data['error']}"
                )
                return await self._search_history(state)

            # 2. Parse response into raw fields
            raw_fields = self._parse_ticket_response(ticket_data)

            # 3. Update state with raw fields
            state["ticket_description"] = raw_fields["description"]
            state["ticket_labels"] = raw_fields["labels"]
            state["ticket_priority"] = raw_fields["priority"]
            state["ticket_status"] = raw_fields["status"]
            state["ticket_components"] = raw_fields.get("components", [])

            # Log ticket content for debugging
            logger.info(f"Ticket content - Summary: {raw_fields['summary']}")
            desc_preview = raw_fields["description"][:200] if raw_fields["description"] else "None"
            logger.info(f"Ticket content - Description: {desc_preview}...")

            # 3.1. Extract Jenkins URLs from description and full content
            from ..utils.jenkins_url_extractor import extract_jenkins_urls
            combined_text = raw_fields.get("description", "")
            # Include full content text for URL extraction (captures comments too)
            if "content" in ticket_data and isinstance(ticket_data["content"], str):
                combined_text += "\n" + ticket_data["content"]
            state["jenkins_urls"] = extract_jenkins_urls(combined_text)
            if state["jenkins_urls"]:
                logger.info(
                    f"Extracted {len(state['jenkins_urls'])} Jenkins URL(s) "
                    f"from ticket: {state['jenkins_urls']}"
                )

            # 3.5. Detect target cluster (two-phase: keywords first, then resource discovery)
            target_cluster = self._detect_cluster_from_keywords(raw_fields)

            if target_cluster is None:
                logger.info("No cluster keywords found, will attempt resource discovery after extraction")
            else:
                state["target_cluster"] = target_cluster
                logger.info(f"Detected cluster from keywords: {target_cluster}")

            # 4. Extract resources with LLM
            state = await self._extract_resources_with_llm(state, raw_fields)

            # 4.5. Fallback to resource discovery if keyword detection failed
            if state.get("target_cluster") is None:
                logger.info("Attempting resource discovery for cluster detection")
                affected = state.get("affected_resources", {})
                target_cluster = await self._discover_cluster_from_resources(
                    affected.get("deployments", []),
                    affected.get("services", [])
                )

                if target_cluster is None:
                    # Fallback to local cluster (kind/dev environment)
                    target_cluster = "local"
                    logger.warning(
                        f"No specific cluster detected for {state['ticket_id']}, "
                        "using local cluster (default K8S_MCP_ENDPOINT)"
                    )

                state["target_cluster"] = target_cluster
                logger.info(f"Detected cluster from resource discovery: {target_cluster}")

        except ValidationError as e:
            logger.error(f"[{AGENT_NAME}] Validation error: {e}")
            state["ticket_summary"] = f"Validation error: {e}"
        except ToolError as e:
            logger.error(f"[{AGENT_NAME}] Tool error: {e}")
            state["ticket_summary"] = f"Tool error: {e}"
        except Exception as e:
            logger.error(f"[{AGENT_NAME}] Unexpected error: {e}")
            state["ticket_summary"] = f"Error processing ticket: {str(e)}"

        # 5. Search for similar historical tickets
        return await self._search_history(state)

    async def _fetch_ticket(self, ticket_id: str) -> Optional[dict]:
        """Fetch ticket data from Jira via MCP tools.

        Args:
            ticket_id: The Jira ticket ID to fetch.

        Returns:
            Ticket data dictionary or None if fetch fails.
        """
        return await self.tools.get_ticket(ticket_id)

    def _parse_ticket_response(self, ticket_data: dict) -> RawTicketFields:
        """Parse ticket response into normalized raw fields.

        Handles both MCP formatted text responses and direct Jira API responses.

        Args:
            ticket_data: Raw response from Jira tools.

        Returns:
            Dictionary with normalized fields: summary, description, labels,
            priority, status.
        """
        # Check if it's an MCP formatted text response
        if "content" in ticket_data and isinstance(ticket_data["content"], str):
            content_text = ticket_data["content"]

            # Check if it's a markdown formatted response from MCP
            if "**Ticket Information**" in content_text or "Summary:" in content_text:
                return self._parse_mcp_formatted_response(content_text)

            # Try to parse as JSON
            try:
                ticket_data = json.loads(content_text)
            except json.JSONDecodeError:
                # Use content as description
                return {
                    "summary": "Unknown issue",
                    "description": content_text,
                    "labels": [],
                    "priority": "Unknown",
                    "status": "Unknown",
                    "components": [],
                }

        # Handle Jira API structure where fields are nested
        if "fields" in ticket_data:
            return self._parse_jira_api_response(ticket_data["fields"])

        # Direct structure (flat)
        if "summary" in ticket_data:
            return {
                "summary": ticket_data.get("summary", "Unknown issue"),
                "description": ticket_data.get("description", ""),
                "labels": ticket_data.get("labels", []),
                "priority": ticket_data.get("priority", "Unknown"),
                "status": ticket_data.get("status", "Unknown"),
                "components": ticket_data.get("components", []),
            }

        # Fallback for unknown structure
        return {
            "summary": "Unknown issue",
            "description": str(ticket_data),
            "labels": [],
            "priority": "Unknown",
            "status": "Unknown",
            "components": [],
        }

    def _parse_mcp_formatted_response(self, content_text: str) -> RawTicketFields:
        """Parse MCP markdown formatted text into raw fields.

        Args:
            content_text: Markdown formatted ticket information from MCP.

        Returns:
            Dictionary with extracted fields.
        """
        raw_summary = "Unknown issue"
        raw_description = ""
        raw_priority = "Unknown"
        raw_status = "Unknown"

        # Extract Summary: line
        summary_match = re.search(r"Summary:\s*(.+?)(?:\n|$)", content_text)
        if summary_match:
            raw_summary = summary_match.group(1).strip()

        # Extract Description section - try bold markdown first
        # Handles both inline (**Description:** text) and newline (**Description:**\ntext)
        desc_match = re.search(
            r"\*\*Description:\*\*[ \t]*(.*?)(?:\n\n\*\*|\n\*\*|$)",
            content_text,
            re.DOTALL,
        )
        if desc_match and desc_match.group(1).strip():
            raw_description = desc_match.group(1).strip()
        else:
            # Simpler pattern -- handles Go inline format (Description: text)
            # Uses [ \t]* to consume only spaces/tabs, preserving \n for boundaries
            desc_match = re.search(
                r"Description:[ \t]*(.*?)(?:\nResolution:|\nComponents:|\n\n|\n\*\*|$)",
                content_text,
                re.DOTALL,
            )
            if desc_match:
                raw_description = desc_match.group(1).strip()

        # Extract Status
        status_match = re.search(r"Status:\s*(.+?)(?:\n|$)", content_text)
        if status_match:
            raw_status = status_match.group(1).strip()

        # Extract Priority
        priority_match = re.search(r"Priority:\s*(.+?)(?:\n|$)", content_text)
        if priority_match:
            raw_priority = priority_match.group(1).strip()

        # Extract Components
        components_match = re.search(r"Components:\s*(.+?)(?:\n|$)", content_text)
        raw_components = []
        if components_match:
            comp_str = components_match.group(1).strip()
            raw_components = [c.strip() for c in comp_str.split(",") if c.strip()]

        logger.info(f"Parsed MCP formatted response - Summary: {raw_summary}")

        return {
            "summary": raw_summary,
            "description": raw_description,
            "labels": [],
            "priority": raw_priority,
            "status": raw_status,
            "components": raw_components,
        }

    def _parse_jira_api_response(self, fields: dict) -> RawTicketFields:
        """Parse nested Jira API response structure.

        Args:
            fields: The 'fields' object from Jira API response.

        Returns:
            Dictionary with normalized fields.
        """
        priority = fields.get("priority", {})
        raw_priority = (
            priority.get("name", "Unknown")
            if isinstance(priority, dict)
            else str(priority)
        )

        status = fields.get("status", {})
        raw_status = (
            status.get("name", "Unknown")
            if isinstance(status, dict)
            else str(status)
        )

        # Extract component names from Jira API nested structure
        raw_components = []
        components_field = fields.get("components", [])
        if isinstance(components_field, list):
            for comp in components_field:
                if isinstance(comp, dict):
                    raw_components.append(comp.get("name", ""))
                elif isinstance(comp, str):
                    raw_components.append(comp)
            raw_components = [c for c in raw_components if c]

        return {
            "summary": fields.get("summary", "Unknown issue"),
            "description": fields.get("description", ""),
            "labels": fields.get("labels", []),
            "priority": raw_priority,
            "status": raw_status,
            "components": raw_components,
        }

    def _detect_cluster_from_keywords(self, raw_fields: RawTicketFields) -> Optional[str]:
        """Phase 1: Detect target cluster from keywords in ticket content.

        Searches for cluster-specific patterns in labels, summary, and description:
        - hldc02: g2*, hls2*, hldc02
        - hldc03: g3*, hldc03

        Args:
            raw_fields: Parsed ticket fields containing labels, summary, description.

        Returns:
            Cluster name ("hldc02", "hldc03") or None if no keywords found.
        """
        from ..constants import CLUSTER_KEYWORD_PATTERNS
        
        # Combine all text for keyword search
        text_parts = []
        text_parts.extend(raw_fields.get("labels", []))
        text_parts.append(raw_fields.get("summary", ""))
        text_parts.append(raw_fields.get("description", ""))
        combined_text = " ".join(text_parts).lower()

        # Check patterns from constants (centralized cluster definitions)
        for cluster, patterns in CLUSTER_KEYWORD_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, combined_text, re.IGNORECASE):
                    logger.info(f"Matched {cluster} pattern: {pattern}")
                    return cluster

        return None

    async def _discover_cluster_from_resources(
        self,
        affected_deployments: list[str],
        affected_services: list[str]
    ) -> Optional[str]:
        """Phase 2: Discover cluster by searching for resources sequentially.

        Searches hldc02 first, then hldc03 for matching deployments.
        First match wins.

        Args:
            affected_deployments: Deployment names extracted from ticket.
            affected_services: Service names extracted from ticket (currently unused).

        Returns:
            Cluster name where resources were found, or None if not found in any cluster.
        """
        if not affected_deployments:
            logger.info("No deployments to search for, skipping resource discovery")
            return None

        # Import K8s tools here to avoid circular dependency
        from ..tools.k8s_tools import K8sTools

        settings = get_settings()
        for cluster in settings.k8s_clusters:
            endpoint = f"{settings.k8s_cluster_mcp_base_url}/{cluster}"
            k8s_tools = K8sTools(mcp_endpoint=endpoint, readonly=True)

            logger.info(f"Searching for resources in cluster {cluster}")

            try:
                for deployment in affected_deployments:
                    # Search all namespaces for deployment
                    result = await k8s_tools.kubectl_get_all_namespaces(
                        "deployments",
                        label_selector=f"app={deployment}"
                    )

                    # Check if deployment name appears in result
                    if deployment.lower() in result.lower():
                        logger.info(f"Found deployment '{deployment}' in cluster {cluster}")
                        return cluster

            except Exception as e:
                logger.warning(f"Error searching cluster {cluster}: {e}")
                continue

        logger.warning("No deployments found in any cluster")
        return None

    async def _extract_resources_with_llm(
        self, state: AgentState, raw_fields: RawTicketFields
    ) -> AgentState:
        """Extract Kubernetes resources from ticket using LLM structured output.

        Uses Pydantic model for reliable extraction of deployment names, services,
        namespaces, and error messages.

        Args:
            state: Current agent state to update.
            raw_fields: Parsed ticket fields containing summary and description.

        Returns:
            Updated state with extracted resources.
        """
        raw_summary = raw_fields["summary"]
        raw_description = raw_fields["description"]

        try:
            # Pre-truncate description to save tokens
            settings = get_settings()
            truncated_description = (
                raw_description[:settings.max_description_for_extraction]
                if raw_description
                else ""
            )

            extraction_prompt = self._build_extraction_prompt(
                raw_summary, truncated_description
            )

            # Call LLM and parse JSON manually (vLLM CPU lacks guided decoding)
            try:
                response = await self.llm.ainvoke(extraction_prompt)
                parsed = self._parse_json_response(response.content, TicketExtraction)
                logger.info(f"Pydantic extraction result: {parsed}")

                # Store extracted fields in state
                state = self._apply_extraction_to_state(state, parsed)

                # Build readable summary
                state["ticket_summary"] = self._build_summary_from_extraction(
                    parsed, raw_summary
                )

                logger.info(
                    f"Extracted: deployments={parsed.affected_deployments}, "
                    f"services={parsed.affected_services}, "
                    f"namespace={state.get('namespace')}"
                )

            except Exception as parse_error:
                logger.warning(
                    "[%s] LLM parse failed",
                    AGENT_NAME,
                    extra={
                        "error_type": type(parse_error).__name__,
                        "error_event": "llm_parse_failure",
                        "ticket_id": state.get("ticket_id"),
                    },
                    exc_info=True,
                )
                state["ticket_summary"] = f"{raw_summary}\n\n{raw_description}"
                fallback = self._fallback_extract_resources(raw_summary, raw_description)
                state["affected_resources"] = fallback
                state["namespace"] = fallback["namespaces"][0] if fallback["namespaces"] else None

        except Exception as llm_error:
            # LLM failed - use raw ticket data with regex fallback
            logger.warning(
                "[%s] LLM invocation failed",
                AGENT_NAME,
                extra={
                    "error_type": type(llm_error).__name__,
                    "error_event": "llm_invocation_failure",
                    "ticket_id": state.get("ticket_id"),
                },
                exc_info=True,
            )
            state["ticket_summary"] = f"{raw_summary}\n\n{raw_description}"
            fallback = self._fallback_extract_resources(raw_summary, raw_description)
            state["affected_resources"] = fallback
            state["namespace"] = fallback["namespaces"][0] if fallback["namespaces"] else None
            logger.info("Using raw ticket data (no LLM parsing)")

        return state

    @staticmethod
    def _parse_json_response(content: str, model_class):
        """Extract JSON from LLM response and parse into Pydantic model."""
        # Try to find JSON block in response (may be wrapped in markdown)
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if json_match:
            content = json_match.group(1)
        # Try to find raw JSON object
        brace_match = re.search(r'\{[\s\S]*\}', content)
        if brace_match:
            content = brace_match.group(0)
        data = json.loads(content)
        return model_class(**data)

    @staticmethod
    def _fallback_extract_resources(
        raw_summary: str, raw_description: str
    ) -> dict[str, list[str]]:
        """Regex-based fallback to extract K8s resources when LLM/JSON fails.

        Scans summary and description for deployment/service name patterns
        and namespace keywords. Returns a dict compatible with affected_resources.

        Args:
            raw_summary: Ticket summary text.
            raw_description: Ticket description text.

        Returns:
            Dict with deployments, services, pods, configmaps, secrets,
            statefulsets, daemonsets, and namespaces keys.
        """
        combined = f"{raw_summary} {raw_description}"

        # Extract hyphenated service/deployment names (e.g. order-service, payment-api)
        # Matches word-word patterns that look like K8s resource names
        name_pattern = re.compile(r'\b([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b')
        # Filter out common non-resource patterns
        exclude = {
            "crash-loop", "crash-loopbackoff", "image-pull", "oom-killed",
            "not-responding", "non-fatal", "pre-existing", "re-dispatch",
            "in-progress", "in-review",
        }
        candidates = [
            m for m in name_pattern.findall(combined.lower())
            if m not in exclude and len(m) > 3
        ]
        # Deduplicate while preserving order
        seen = set()
        unique_names = []
        for name in candidates:
            if name not in seen:
                seen.add(name)
                unique_names.append(name)

        # Extract namespace from "in <namespace> namespace" or "<namespace> namespace"
        ns_pattern = re.compile(
            r'(?:in\s+|namespace[:\s]+)([a-z][a-z0-9-]*)',
            re.IGNORECASE,
        )
        namespaces = list(dict.fromkeys(ns_pattern.findall(combined.lower())))
        # Also check for bare namespace names
        known_ns = {"production", "staging", "default", "kube-system", "monitoring"}
        for ns in known_ns:
            if ns in combined.lower() and ns not in namespaces:
                namespaces.append(ns)
        if not namespaces:
            namespaces = ["production"]

        return {
            "deployments": unique_names,
            "services": [],
            "pods": [],
            "configmaps": [],
            "secrets": [],
            "statefulsets": [],
            "daemonsets": [],
            "namespaces": namespaces,
        }

    def _build_extraction_prompt(self, summary: str, description: str) -> str:
        """Build the prompt for LLM resource extraction.

        Args:
            summary: Ticket summary text.
            description: Ticket description (pre-truncated).

        Returns:
            Formatted prompt string.
        """
        return f"""Extract the affected Kubernetes resources from this Jira ticket.

TICKET SUMMARY: {summary}

TICKET DESCRIPTION: {description}

INSTRUCTIONS:
1. Look for deployment names (e.g., "order-service", "api-server", "payment-api")
2. Look for service names (often same as deployment names)
3. Look for namespace names (e.g., "production", "staging", "default")
4. Extract any error messages mentioned
5. Summarize the symptoms/problem

IMPORTANT:
- Extract the EXACT resource names as they appear in the ticket.
- If the ticket mentions "order-service is down", then affected_deployments should include "order-service".
- The affected_namespaces field is CRITICAL. If the ticket says "production namespace", use ["production"]. Never default to ["default"] unless the ticket explicitly says "default namespace".
- Respond with ONLY a JSON object, no explanation. Use this exact schema:
{{"affected_deployments": ["deploy-name"], "affected_services": ["svc-name"], "affected_pods": [], "affected_configmaps": [], "affected_secrets": [], "affected_statefulsets": [], "affected_daemonsets": [], "affected_namespaces": ["namespace-from-ticket"], "symptoms": "brief description of the problem", "error_messages": []}}"""

    def _apply_extraction_to_state(
        self, state: AgentState, parsed: TicketExtraction
    ) -> AgentState:
        """Apply parsed extraction results to agent state.

        Args:
            state: Current agent state.
            parsed: Pydantic model with extracted resources.

        Returns:
            Updated state with all extracted fields.
        """
        namespaces = parsed.affected_namespaces if parsed.affected_namespaces else None

        # Promote pods to deployments when no deployments extracted.
        # Users commonly say "X pods failing" when X is actually a deployment name.
        deployments = parsed.affected_deployments
        if not deployments and parsed.affected_pods:
            deployments = list(parsed.affected_pods)
            logger.info(
                f"No deployments extracted, promoting pods to deployments: {deployments}"
            )

        # Consolidate all affected resources into single dictionary
        state["affected_resources"] = {
            "deployments": deployments,
            "services": parsed.affected_services,
            "pods": parsed.affected_pods,
            "configmaps": parsed.affected_configmaps,
            "secrets": parsed.affected_secrets,
            "statefulsets": parsed.affected_statefulsets,
            "daemonsets": parsed.affected_daemonsets,
            "namespaces": namespaces,
        }
        state["symptoms"] = parsed.symptoms
        state["error_messages"] = parsed.error_messages
        state["namespace"] = namespaces[0] if namespaces else None

        return state

    def _build_summary_from_extraction(
        self, parsed: TicketExtraction, fallback_summary: str
    ) -> str:
        """Build readable summary from extracted fields.

        Only includes non-empty fields. Adds fallback context if no resources
        were extracted.

        Args:
            parsed: Pydantic model with extracted resources.
            fallback_summary: Original ticket summary for fallback.

        Returns:
            Formatted summary string.
        """
        summary_lines = []

        if parsed.affected_deployments:
            summary_lines.append(
                f"AFFECTED DEPLOYMENTS: {', '.join(parsed.affected_deployments)}"
            )
        if parsed.affected_services:
            summary_lines.append(
                f"AFFECTED SERVICES: {', '.join(parsed.affected_services)}"
            )
        if parsed.affected_pods:
            summary_lines.append(
                f"AFFECTED PODS: {', '.join(parsed.affected_pods)}"
            )
        if parsed.affected_configmaps:
            summary_lines.append(
                f"AFFECTED CONFIGMAPS: {', '.join(parsed.affected_configmaps)}"
            )
        if parsed.affected_secrets:
            summary_lines.append(
                f"AFFECTED SECRETS: {', '.join(parsed.affected_secrets)}"
            )
        if parsed.affected_statefulsets:
            summary_lines.append(
                f"AFFECTED STATEFULSETS: {', '.join(parsed.affected_statefulsets)}"
            )
        if parsed.affected_daemonsets:
            summary_lines.append(
                f"AFFECTED DAEMONSETS: {', '.join(parsed.affected_daemonsets)}"
            )

        namespaces = parsed.affected_namespaces or None
        summary_lines.append(f"NAMESPACE: {', '.join(namespaces) if namespaces else 'Not specified'}")

        if parsed.symptoms:
            summary_lines.append(f"SYMPTOMS: {parsed.symptoms}")
        if parsed.error_messages:
            summary_lines.append(
                f"ERROR MESSAGES: {'; '.join(parsed.error_messages)}"
            )

        # Add fallback if no resources were extracted
        if (
            not parsed.affected_deployments
            and not parsed.affected_services
            and not parsed.affected_pods
        ):
            summary_lines.append(f"\nFALLBACK CONTEXT: {fallback_summary[:200]}")

        return "\n".join(summary_lines)

    async def _search_history(self, state: AgentState) -> AgentState:
        """Search for similar historical tickets.

        Args:
            state: Current agent state.

        Returns:
            Updated state with similar_tickets and past_resolutions.
        """
        try:
            logger.info("Searching for similar historical tickets...")
            state = await self._history_agent.run(state)
            similar_count = len(state.get("similar_tickets", []))
            logger.info(f"Found {similar_count} similar historical tickets")
        except Exception as e:
            logger.warning(f"[{AGENT_NAME}] History search failed (non-fatal): {e}")
            state.setdefault("similar_tickets", [])
            state.setdefault("past_resolutions", [])

        return state

    async def post_comment(self, state: AgentState) -> AgentState:
        """
        Post investigation results back to Jira ticket with idempotency protection.

        This is called at the end of the workflow to report findings.
        Includes dual idempotency checks to prevent duplicate comments:
        1. State-based: Checks if comment was already posted in this workflow run
        2. Label-based: Checks if ticket already has investigation label (cross-workflow)
        """
        ticket_id = state.get("ticket_id")
        logger.info(f"Posting comment to ticket: {ticket_id}")

        # ═══════════════════════════════════════════════════════════════
        # IDEMPOTENCY CHECK 1: State-based (same workflow thread/resume)
        # ═══════════════════════════════════════════════════════════════
        if state.get("_comment_posted"):
            logger.info(
                f"[{AGENT_NAME}] Comment already posted in this workflow run - skipping"
            )
            return state

        # ═══════════════════════════════════════════════════════════════
        # IDEMPOTENCY CHECK 2: Label-based (cross-workflow protection)
        # Prevents duplicates if Go poller re-dispatches after partial completion
        # ═══════════════════════════════════════════════════════════════
        try:
            ticket_data = await self._fetch_ticket(ticket_id)
            if ticket_data and "fields" in ticket_data:
                labels = ticket_data.get("fields", {}).get("labels", [])
                if LABEL_AI_INVESTIGATED in labels:
                    logger.warning(
                        f"[{AGENT_NAME}] Ticket {ticket_id} already has {LABEL_AI_INVESTIGATED} label - "
                        "skipping comment to prevent duplicate (likely re-dispatch after partial completion)"
                    )
                    state["_comment_posted"] = True  # Mark so we don't check again
                    return state
        except Exception as e:
            logger.warning(
                f"[{AGENT_NAME}] Failed to check labels for idempotency: {e} - proceeding with comment"
            )

        logger.debug(
            f"post_comment state - remediation_count: {state.get('remediation_count')}"
        )
        logger.debug(
            f"post_comment state - remediation_history: {state.get('remediation_history')}"
        )
        logger.debug(
            f"post_comment state - issue_resolved: {state.get('issue_resolved')}"
        )
        similar = state.get("similar_tickets", [])
        logger.debug(f"post_comment state - similar_tickets count: {len(similar)}")

        # Format the investigation results
        comment = self._build_comment(state)

        try:
            result = await self.tools.add_comment(
                ticket_id=ticket_id, comment=comment
            )

            if "error" not in result:
                logger.info(f"[{AGENT_NAME}] Successfully posted comment to Jira")
                # Mark as posted BEFORE adding label - ensures checkpoint captures this
                # even if we crash between comment and label
                state["_comment_posted"] = True
                # Add investigation label
                try:
                    await self.tools.add_label(ticket_id, LABEL_AI_INVESTIGATED)
                    logger.info(f"[{AGENT_NAME}] Added {LABEL_AI_INVESTIGATED} label")
                except Exception as label_err:
                    logger.warning(f"[{AGENT_NAME}] Failed to add label: {label_err}")
            else:
                logger.error(
                    f"[{AGENT_NAME}] Failed to post comment: {result['error']}"
                )

        except ValidationError as e:
            logger.error(f"[{AGENT_NAME}] Validation error posting comment: {e}")
        except ToolError as e:
            logger.error(f"[{AGENT_NAME}] Tool error posting comment: {e}")
        except Exception as e:
            logger.error(f"[{AGENT_NAME}] Unexpected error posting comment: {e}")

        return state

    def _build_comment(self, state: AgentState) -> str:
        """
        Unified comment formatter for all scenarios.

        Adapts sections based on state:
        - HITL rejection: Shows rejection reason and proposed fix
        - HITL approval: Shows approval details and applied fix
        - Standard investigation: Shows diagnosis and remediation attempts
        """
        # Determine comment type
        is_rejection = state.get("hitl_diagnosis_approved") is False
        is_approval = state.get("hitl_remediation_approved") is True and state.get("issue_resolved", False)
        issue_resolved = state.get("issue_resolved", False)

        # Build header with appropriate status
        header = self._build_header(is_rejection, is_approval, issue_resolved)

        # Build sections conditionally
        sections = []

        # HITL-specific sections
        if is_rejection:
            sections.append(self._build_rejection_details(state))
            sections.append(self._build_proposed_fix(state))
        elif is_approval:
            sections.append(self._build_approval_details(state))
            sections.append(self._build_applied_fix(state))

        # Diagnosis section (all scenarios)
        sections.append(self._build_diagnosis(state, is_rejection, is_approval))

        # Remediation section (standard and approval)
        if not is_rejection and state.get("remediation_attempted"):
            sections.append(self._build_remediation(state))

        # Verification (approval only)
        if is_approval:
            sections.append(self._build_verification(state))

        # Cluster state (standard only)
        if not is_rejection and not is_approval and state.get("cluster_findings"):
            sections.append(self._build_cluster_state(state))

        # Similar tickets (all scenarios)
        sections.append(self._build_similar_tickets(state))

        # Metadata footer (all scenarios)
        sections.append(self._build_metadata(state))

        return header + "\n\n----\n\n".join(sections)

    def _build_header(self, is_rejection: bool, is_approval: bool, issue_resolved: bool) -> str:
        """Build status header based on scenario."""
        if is_rejection:
            status_emoji = "⛔"
            status_text = "REMEDIATION REJECTED"
            description = "The AI agent proposed a fix, but remediation was declined by the operator."
        elif is_approval:
            status_emoji = "✅"
            status_text = "RESOLVED - Human Approved Fix Applied"
            description = "The AI agent proposed a fix, received approval, and successfully applied the remediation."
        elif issue_resolved:
            status_emoji = "✅"
            status_text = "RESOLVED - Issue Fixed Automatically"
            description = "The AI agent successfully diagnosed and remediated this issue."
        else:
            status_emoji = "⚠️"
            status_text = "NEEDS ATTENTION - Manual Investigation Required"
            description = "The AI agent investigated but could not fully resolve this issue."

        return f"""h2. {status_emoji} {status_text}

{description}"""

    def _build_rejection_details(self, state: AgentState) -> str:
        """Build rejection details section."""
        rejection_reason = state.get("hitl_rejection_reason", "No reason provided")
        rejected_at = state.get("hitl_requested_at", self._get_timestamp())

        return f"""h3. Rejection Details

*Rejected at:* {rejected_at}

*Reason:*
{rejection_reason}"""

    def _build_proposed_fix(self, state: AgentState) -> str:
        """Build proposed fix section (rejection scenario)."""
        recommended_action = state.get("recommended_action", "No specific action was proposed")
        confidence = state.get("confidence_level", "Unknown")
        risk_level = state.get("action_risk_level", "Unknown")
        namespace = state.get("namespace", "default")

        # Extract target resource
        target = "Unknown"
        deployments = state.get("affected_resources", {}).get("deployments", [])
        services = state.get("affected_resources", {}).get("services", [])
        if deployments:
            target = f"deployment/{deployments[0]}"
        elif services:
            target = f"service/{services[0]}"

        # Format command if available
        command_section = ""
        if "kubectl" in recommended_action.lower():
            command_section = f"\n\n*Command that would have been executed:*\n{{code}}\n{recommended_action}\n{{code}}"

        return f"""h3. Proposed Fix (NOT APPLIED)

*Action:* {self._summarize_action(recommended_action)}
*Target:* {target}
*Namespace:* {namespace}
*Confidence:* {confidence}
*Risk Level:* {risk_level}{command_section}"""

    def _build_approval_details(self, state: AgentState) -> str:
        """Build approval details section."""
        approved_at = state.get("hitl_requested_at", self._get_timestamp())

        return f"""h3. Approval Details

*Approved at:* {approved_at}
*Outcome:* Remediation applied and verified successful"""

    def _build_applied_fix(self, state: AgentState) -> str:
        """Build applied fix section (approval scenario)."""
        recommended_action = state.get("recommended_action", "Remediation action applied")
        confidence = state.get("confidence_level", "Unknown")
        namespace = state.get("namespace", "default")
        remediation_count = state.get("remediation_count", 1)

        # Extract target resource
        target = "Unknown"
        deployments = state.get("affected_resources", {}).get("deployments", [])
        services = state.get("affected_resources", {}).get("services", [])
        if deployments:
            target = f"deployment/{deployments[0]}"
        elif services:
            target = f"service/{services[0]}"

        # Build actions table
        history = state.get("remediation_history", [])
        actions_table = ""
        if history:
            actions_table = "\n\n*Actions Performed:*\n||#||Action||Result||\n"
            for i, entry in enumerate(history, 1):
                success = entry.get("success", False)
                action = entry.get("action", "Unknown action")
                result_icon = "✅" if success else "❌"
                actions_table += f"|{i}|{action}|{result_icon}|\n"

        return f"""h3. Applied Fix

*Action:* {self._summarize_action(recommended_action)}
*Target:* {target}
*Namespace:* {namespace}
*Confidence:* {confidence}
*Attempts:* {remediation_count}{actions_table}"""

    def _build_diagnosis(self, state: AgentState, is_rejection: bool, is_approval: bool) -> str:
        """Build diagnosis section (all scenarios)."""
        root_cause = state.get("root_cause") or "Unable to determine root cause"
        confidence = state.get("confidence_level") or "Unknown"
        confidence_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(
            confidence.lower(), "⚪"
        )

        # Truncate root cause for readability
        if len(root_cause) > 500:
            first_para = root_cause.split("\n\n")[0]
            root_cause = first_para[:500] + "..." if len(first_para) > 500 else first_para

        diagnosis = f"""h3. Diagnosis

*Root Cause ({confidence_icon} {confidence} confidence):*
{root_cause}"""

        # Add recommended action for standard non-resolved cases
        if not is_rejection and not is_approval and not state.get("issue_resolved", False):
            diagnosis += f"\n\n*Recommended Action:*\n{state.get('recommended_action', 'Manual investigation recommended')}"

        # Add lock warning if applicable
        if state.get("remediation_skipped_due_to_lock", False):
            locked_by = state.get("locked_by_ticket") or "another ticket"
            diagnosis += f"\n\n*⚠️ Note:* Remediation was skipped because the resource is locked by {locked_by}"

        # Add preventive measures if available
        if state.get("preventive_measures"):
            diagnosis += "\n\n*Preventive Measures:*"
            for measure in state["preventive_measures"]:
                diagnosis += f"\n* {measure}"

        return diagnosis

    def _build_remediation(self, state: AgentState) -> str:
        """Build remediation section (standard and approval)."""
        issue_resolved = state.get("issue_resolved", False)
        remediation_count = state.get("remediation_count", 0)
        history = state.get("remediation_history", [])
        verification_evidence = state.get("verification_evidence", [])

        status_text = "✅ Successfully resolved" if issue_resolved else "❌ Not resolved - manual intervention needed"

        section = f"""h3. Remediation Actions

*Outcome:* {status_text}
*Total Attempts:* {remediation_count}

||#||Action||Result||"""

        for i, entry in enumerate(history, 1):
            success = entry.get("success", False)
            action = entry.get("action", "Unknown action")
            result_icon = "✅" if success else "❌"
            section += f"\n|{i}|{action}|{result_icon}|"

        # Add verification evidence
        if verification_evidence:
            section += "\n\n*Verification Evidence:*"
            for evidence in verification_evidence:
                icon = "✅" if issue_resolved else "⚠️"
                section += f"\n* {icon} {evidence}"

        # Add error if present
        result = state.get("remediation_result", {})
        if result.get("error"):
            section += f"\n\n*Last Error:* {result.get('error')}"

        return section

    def _build_verification(self, state: AgentState) -> str:
        """Build verification section (approval only)."""
        verification_evidence = state.get("verification_evidence", [])

        if not verification_evidence:
            return """h3. Verification

*Status:* ✅ Issue resolved - service restored

_Verification completed successfully._"""

        evidence_list = "\n".join([f"* ✅ {evidence}" for evidence in verification_evidence])

        return f"""h3. Verification

*Status:* ✅ Issue resolved - service restored

*Evidence:*
{evidence_list}"""

    def _build_cluster_state(self, state: AgentState) -> str:
        """Build cluster state section (standard only)."""
        findings = state.get("cluster_findings", {})
        deployments = state.get("affected_resources", {}).get("deployments", [])
        services = state.get("affected_resources", {}).get("services", [])

        section = f"""h3. Cluster State

*Target Resources:*
* Deployments: {', '.join(deployments) if deployments else 'N/A'}
* Services: {', '.join(services) if services else 'N/A'}
* Namespace: {state.get('namespace', 'N/A')}"""

        # Add deployment status if available
        resources = findings.get("resources", {})
        if resources.get("deployment"):
            deployment_info = str(resources["deployment"])[:get_settings().truncation_deployment_status]
            section += f"\n\n*Deployment Status:*\n{{code}}\n{deployment_info}\n{{code}}"

        # Add pod status if available
        if resources.get("pods"):
            pods_info = str(resources["pods"])[:get_settings().truncation_deployment_status]
            section += f"\n\n*Pod Status:*\n{{code}}\n{pods_info}\n{{code}}"

        # Add events if available
        events = findings.get("events", [])
        if events:
            events_str = str(events)[:get_settings().truncation_description]
            section += f"\n\n*Recent Events:*\n{{noformat}}\n{events_str}\n{{noformat}}"

        return section

    def _build_similar_tickets(self, state: AgentState) -> str:
        """Build similar tickets section (all scenarios)."""
        similar = state.get("similar_tickets", [])

        if not similar:
            return """h3. Similar Past Issues

_No similar resolved issues found._"""

        settings = get_settings()
        issues_list = []
        for ticket in similar[:settings.max_similar_tickets]:
            if ticket is None:
                continue
            if isinstance(ticket, dict):
                key = ticket.get('key', 'Unknown')
                summary = ticket.get('summary', 'No summary')
                is_resolved = ticket.get('is_resolved', False)
                status_icon = "✅" if is_resolved else "🔴"
                issues_list.append(f"* {status_icon} [{key}] - {summary}")
            else:
                issues_list.append(f"* {ticket}")

        tickets_text = "\n".join(issues_list)

        return f"""h3. Similar Past Issues

The following issues may be relevant:

{tickets_text}"""

    def _build_metadata(self, state: AgentState) -> str:
        """Build metadata footer (all scenarios)."""
        issue_resolved = state.get("issue_resolved", False)
        confidence = state.get("confidence_level", "Unknown")
        deployments = state.get("affected_resources", {}).get("deployments", [])
        services = state.get("affected_resources", {}).get("services", [])

        return f"""h4. Investigation Metadata

||Field||Value||
|Status|{("✅ Resolved" if issue_resolved else "⚠️ Unresolved")}|
|Confidence|{confidence}|
|Namespace|{state.get('namespace', 'N/A')}|
|Deployments|{', '.join(deployments) if deployments else 'N/A'}|
|Services|{', '.join(services) if services else 'N/A'}|
|Remediation Attempts|{state.get('remediation_count', 0)}|"""

    def _summarize_action(self, recommended_action: str) -> str:
        """Create a short summary of the recommended action."""
        if not recommended_action:
            return "No specific action proposed"

        # Take first line or first 100 chars
        first_line = recommended_action.split("\n")[0]
        if len(first_line) > 100:
            return first_line[:97] + "..."
        return first_line

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

