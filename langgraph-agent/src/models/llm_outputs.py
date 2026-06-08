"""Pydantic models for all LLM structured outputs.

This module centralizes all Pydantic models used for LLM structured output
across the multi-agent system. Using structured output instead of string
parsing provides:
- Type safety and validation
- IDE autocomplete
- Clear documentation of expected formats
- Reduced parsing errors
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# =============================================================================
# Constants
# =============================================================================

_YAML_MAX_BYTES = 65_536  # 64 KiB — generous limit for K8s manifests


# =============================================================================
# Enums
# =============================================================================


class ConfidenceLevel(str, Enum):
    """Confidence level for diagnoses and recommendations.

    Used by Diagnostician to indicate how certain the diagnosis is,
    and by supervisor to decide whether to attempt remediation.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ActionType(str, Enum):
    """Types of remediation actions that can be executed.

    Used by K8sRemediationExecutor to determine which K8s operation to perform.
    """

    CREATE_CONFIGMAP = "create_configmap"
    CREATE_SECRET = "create_secret"
    APPLY_MANIFEST = "apply_manifest"
    RESTART = "restart"
    SCALE = "scale"
    DELETE = "delete"
    PATCH = "patch"
    MANUAL_INTERVENTION = "manual_intervention"


class IssueCategory(str, Enum):
    """Categories of Kubernetes issues.

    Used by JiraAgent (via HistoryAgent helper) and Diagnostician to classify
    problems and find similar past issues.
    """

    RESOURCE_EXHAUSTION = "resource_exhaustion"
    CONFIGURATION_ERROR = "configuration_error"
    NETWORK_ISSUE = "network_issue"
    APPLICATION_BUG = "application_bug"
    DEPLOYMENT_FAILURE = "deployment_failure"
    IMAGE_PULL_ERROR = "image_pull_error"
    PERMISSION_ERROR = "permission_error"
    UNKNOWN = "unknown"


# =============================================================================
# JiraAgent Models
# =============================================================================


class TicketExtraction(BaseModel):
    """Structured extraction of K8s resources from Jira ticket.

    Used by JiraAgent to parse ticket content and identify affected resources.
    """

    affected_deployments: list[str] = Field(
        default_factory=list,
        description="List of deployment names mentioned in the ticket",
    )
    affected_services: list[str] = Field(
        default_factory=list,
        description="List of service names mentioned in the ticket",
    )
    affected_pods: list[str] = Field(
        default_factory=list,
        description="List of specific pod names if mentioned",
    )
    affected_configmaps: list[str] = Field(
        default_factory=list,
        description="List of ConfigMap names mentioned",
    )
    affected_secrets: list[str] = Field(
        default_factory=list,
        description="List of Secret names mentioned",
    )
    affected_statefulsets: list[str] = Field(
        default_factory=list,
        description="List of StatefulSet names mentioned",
    )
    affected_daemonsets: list[str] = Field(
        default_factory=list,
        description="List of DaemonSet names mentioned",
    )
    affected_namespaces: list[str] = Field(
        default_factory=lambda: ["default"],
        description="List of namespaces mentioned",
    )
    symptoms: str = Field(
        default="",
        description="Brief description of what's broken",
    )
    error_messages: list[str] = Field(
        default_factory=list,
        description="Error messages mentioned in the ticket",
    )

    @field_validator("affected_namespaces", mode="before")
    @classmethod
    def ensure_namespace_list(cls, v: Any) -> list[str]:
        """Ensure namespace is always a non-empty list."""
        if not v:
            return ["default"]
        if isinstance(v, str):
            return [v]
        return v


# =============================================================================
# Diagnostician Models
# =============================================================================


class RemediationStep(BaseModel):
    """A single remediation action within a multi-step plan.

    Each step targets one K8s resource with one action. Steps are executed
    sequentially by K8sRemediationExecutor with stop-on-first-failure semantics.
    """

    action: ActionType = Field(
        default=ActionType.MANUAL_INTERVENTION,
        description="The type of K8s action to perform",
    )
    resource_type: str = Field(
        default="deployment",
        description="K8s resource type (deployment, pod, configmap, secret, etc.)",
    )
    name: str = Field(
        default="",
        description="Name of the target resource",
    )
    namespace: str = Field(
        default="default",
        description="Namespace of the target resource",
    )
    data: Optional[dict[str, Any]] = Field(
        default=None,
        description="Data for create_configmap, create_secret, or patch operations",
    )
    yaml_content: Optional[str] = Field(
        default=None,
        max_length=_YAML_MAX_BYTES,
        description=(
            "YAML content for apply_manifest action. "
            "SECURITY: callers must validate provenance before execution. "
            "Max 64 KiB."
        ),
    )
    replicas: Optional[int] = Field(
        default=None,
        ge=0,
        description="Target replica count for scale action",
    )
    reason: str = Field(
        default="",
        description="Why this specific action is needed",
    )

    @field_validator("namespace", mode="before")
    @classmethod
    def ensure_namespace(cls, v: Any) -> str:
        """Ensure namespace is never empty."""
        if not v or v in ("N/A", "unknown", ""):
            return "default"
        return str(v)


class RemediationPlan(BaseModel):
    """Structured remediation plan with ordered steps.

    Supports multi-step remediation: the executor iterates steps sequentially
    with stop-on-first-failure semantics and upfront resource locking.

    Backward compatible: if top-level action/name/etc. are provided without
    steps, a single step is auto-created from those fields.

    If remediation_possible is False, manual_instructions describes what to do.
    """

    remediation_possible: bool = Field(
        ...,
        description="Whether automated remediation is possible. False means manual intervention required.",
    )
    steps: list[RemediationStep] = Field(
        default_factory=list,
        description="Ordered list of remediation steps to execute sequentially",
    )

    # --- Top-level fields kept for backward compatibility ---
    # When steps is empty but these are set, a single step is auto-created.
    action: ActionType = Field(
        default=ActionType.MANUAL_INTERVENTION,
        description="(Legacy) The type of K8s action to perform",
    )
    resource_type: str = Field(
        default="deployment",
        description="(Legacy) K8s resource type",
    )
    name: str = Field(
        default="",
        description="(Legacy) Name of the target resource",
    )
    namespace: str = Field(
        default="default",
        description="(Legacy) Namespace of the target resource",
    )
    data: Optional[dict[str, Any]] = Field(
        default=None,
        description="(Legacy) Data for create/patch operations",
    )
    yaml_content: Optional[str] = Field(
        default=None,
        max_length=_YAML_MAX_BYTES,
        description=(
            "(Legacy) YAML content for apply_manifest action. "
            "SECURITY: callers must validate provenance before execution. "
            "Max 64 KiB."
        ),
    )
    replicas: Optional[int] = Field(
        default=None,
        ge=0,
        description="(Legacy) Target replica count for scale action",
    )
    reason: str = Field(
        default="",
        description="(Legacy) Why this specific remediation was chosen",
    )
    manual_instructions: Optional[str] = Field(
        default=None,
        description="Detailed instructions if manual intervention is required",
    )

    @field_validator("namespace", mode="before")
    @classmethod
    def ensure_namespace(cls, v: Any) -> str:
        """Ensure namespace is never empty."""
        if not v or v in ("N/A", "unknown", ""):
            return "default"
        return str(v)

    @model_validator(mode="after")
    def auto_create_step_from_top_level(self) -> "RemediationPlan":
        """Create a single step from top-level fields if steps is empty.

        This preserves backward compatibility: existing code that sets
        action/name/namespace on the plan directly will still work.
        """
        if (
            not self.steps
            and self.remediation_possible
            and self.action != ActionType.MANUAL_INTERVENTION
        ):
            self.steps = [
                RemediationStep(
                    action=self.action,
                    resource_type=self.resource_type,
                    name=self.name,
                    namespace=self.namespace,
                    data=self.data,
                    yaml_content=self.yaml_content,
                    replicas=self.replicas,
                    reason=self.reason,
                )
            ]
        return self


def get_llm_schema_for_remediation_plan() -> dict[str, Any]:
    """Get RemediationPlan schema with legacy fields hidden from the LLM.
    
    This is used by the Diagnostician when constructing the prompt for the LLM.
    Legacy fields (action, name, namespace, etc.) are kept for backward
    compatibility in actual RemediationPlan instances, but should NOT appear in
    the JSON schema sent to the LLM, which would pollute the prompt and increase
    hallucination risk.
    """
    schema = RemediationPlan.model_json_schema()
    legacy_fields = {
        "action", "resource_type", "name", "namespace",
        "data", "yaml_content", "replicas", "reason"
    }
    # Remove from properties
    props = schema.get("properties", {})
    for field in legacy_fields:
        props.pop(field, None)
    # Remove from required if present
    required = schema.get("required", [])
    schema["required"] = [f for f in required if f not in legacy_fields]
    return schema


class Diagnosis(BaseModel):
    """Structured diagnosis output from Diagnostician.

    This replaces the fragile string parsing of LLM output with
    validated structured data.

    IMPORTANT: The remediation_plan field directly specifies the K8s action
    to take, eliminating the need for a separate LLM call to parse text
    instructions into structured actions.
    """

    root_cause: str = Field(
        ...,
        min_length=10,
        description="The diagnosed root cause of the issue",
    )
    confidence_level: ConfidenceLevel = Field(
        ...,
        description="How confident we are in this diagnosis",
    )
    remediation_plan: RemediationPlan = Field(
        ...,
        description="The structured remediation plan to execute",
    )
    preventive_measures: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Measures to prevent this issue in the future",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting this diagnosis",
    )
    category: IssueCategory = Field(
        default=IssueCategory.UNKNOWN,
        description="Category of the issue",
    )


class JiraTicketResponse(BaseModel):
    """Structured ticket data from the Go Jira MCP server.

    Used in _fetch_ticket_details and _parse_tickets to replace
    brittle regex parsing with validated structured data.
    """

    key: str = Field(
        default="",
        description="Jira ticket key, e.g. SP-123"
    )
    summary: str = Field(
        default="",
        description="Ticket title/summary"
    )
    description: str = Field(
        default="",
        description="Full ticket description"
    )
    status: str = Field(
        default="Unknown",
        description="Jira workflow status"
    )
    resolution: str = Field(
        default="",
        description="Resolution text if resolved"
    )
    last_comment: str = Field(
        default="",
        description="Most recent comment body"
    )
    updated: str = Field(
        default="",
        description="ISO-8601 last-updated timestamp"
    )
    is_resolved: bool = Field(
        default=False,
        description="True if status is Done/Resolved/Closed"
    )
    components: list[str] = Field(
        default_factory=list,
        description="Jira component names"
    )

    @model_validator(mode="after")
    def infer_is_resolved(self) -> "JiraTicketResponse":
        """Infer is_resolved from status unless explicitly provided as True.
        
        Uses model_validator(mode="after") to ensure status field is fully
        populated before checking it (unlike field_validator which runs before
        all fields are validated).
        """
        if not self.is_resolved:
            self.is_resolved = self.status.lower() in ("resolved", "done", "closed")
        return self



