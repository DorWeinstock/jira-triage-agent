"""Format Jira approval comments for HITL using wiki markup panels.

Uses Jira panel syntax for a clean, readable format that matches the
outcome comment style. Full content is displayed without truncation.
"""

import json
import logging
from typing import Any

from ..models.llm_outputs import ActionType, RemediationPlan

logger = logging.getLogger(__name__)

# Confidence indicators
CONFIDENCE_INDICATORS = {
    "high": "🟢 HIGH CONFIDENCE",
    "medium": "🟡 MEDIUM CONFIDENCE",
    "low": "🔴 LOW CONFIDENCE",
}

# Panel colors by confidence level (matching outcome comments)
CONFIDENCE_COLORS = {
    "high": {"bg": "#d4edda", "border": "#28a745"},      # Green
    "medium": {"bg": "#fff3cd", "border": "#ffc107"},    # Yellow
    "low": {"bg": "#f8d7da", "border": "#f5c6cb"},       # Red
}

# Panel colors for sections
PANEL_COLORS = {
    "problem": {"bg": "#f8f9fa", "border": "#6c757d"},   # Gray
    "fix": {"bg": "#e7f3ff", "border": "#0066cc"},       # Blue
    "evidence": {"bg": "#f5f5f5", "border": "#9e9e9e"},  # Light gray
    "action": {"bg": "#fff8e1", "border": "#ffc107"},    # Amber
}


def format_approval_comment(state: dict[str, Any]) -> str:
    """Format approval comment for Jira using wiki markup panels.

    Creates a structured comment with full content displayed in panels,
    matching the visual style of outcome comments. No truncation.

    Args:
        state: Current workflow state with diagnosis.

    Returns:
        Jira wiki markup formatted comment.

    Raises:
        TypeError: If state is None or not a dict.
    """
    if state is None:
        raise TypeError("state must be a dict, got None")
    if not isinstance(state, dict):
        raise TypeError(f"state must be a dict, got {type(state).__name__}")

    confidence = state.get("confidence_level", "medium")
    colors = CONFIDENCE_COLORS.get(confidence, CONFIDENCE_COLORS["medium"])
    indicator = CONFIDENCE_INDICATORS.get(confidence, CONFIDENCE_INDICATORS["medium"])

    problem = _extract_problem(state)
    fix = state.get("recommended_action", "No action recommended")
    evidence = _format_evidence(state)
    cluster = state.get("target_cluster")
    cluster_line = f"\n*Target Cluster:* {cluster}" if cluster else ""

    commands = _extract_recommended_commands(state)
    commands_section = _format_commands_section(commands)

    return f"""{{panel:bgColor={colors["bg"]}|borderColor={colors["border"]}}}
h2. ⏳ APPROVAL REQUIRED [{indicator}]{cluster_line}

The AI agent has diagnosed this issue and is ready to apply a fix. Please review and respond.
{{panel}}

{{panel:title=Problem|bgColor={PANEL_COLORS["problem"]["bg"]}|borderColor={PANEL_COLORS["problem"]["border"]}}}
{problem}
{{panel}}

{{panel:title=Recommended Fix|bgColor={PANEL_COLORS["fix"]["bg"]}|borderColor={PANEL_COLORS["fix"]["border"]}}}
{fix}
{{panel}}
{commands_section}
{{panel:title=Evidence|bgColor={PANEL_COLORS["evidence"]["bg"]}|borderColor={PANEL_COLORS["evidence"]["border"]}}}
{evidence}
{{panel}}

{{panel:bgColor={PANEL_COLORS["action"]["bg"]}|borderColor={PANEL_COLORS["action"]["border"]}}}
*To respond:* Comment with {{{{approve}}}} or {{{{reject: your reason}}}}
{{panel}}"""


def _extract_problem(state: dict[str, Any]) -> str:
    """Extract concise problem statement from root cause.

    Handles root_cause as string or dict. If dict, prefers 'summary' key,
    then 'detail' key. Non-string/None values return "Unknown issue".
    """
    root_cause = state.get("root_cause", "Unknown issue")
    if isinstance(root_cause, dict):
        # Handle structured root_cause: {"summary": "...", "detail": "..."}
        return (
            root_cause.get("summary") or root_cause.get("detail") or str(root_cause)
        )
    if root_cause is None or (isinstance(root_cause, str) and not root_cause.strip()):
        return "Unknown issue"
    return str(root_cause)


def _format_evidence(state: dict[str, Any]) -> str:
    """Format evidence as a bulleted list for Jira wiki markup.

    Extracts key evidence points from cluster findings and formats them
    as a readable bulleted list. No truncation - shows full evidence.

    Args:
        state: Current workflow state with cluster findings.

    Returns:
        Jira wiki markup bulleted list of evidence points.
    """
    findings = state.get("cluster_findings", {})
    if not findings or not isinstance(findings, dict):
        return "_No findings available_"

    evidence_lines = []

    # Affected deployment and namespace
    if findings.get("affected_deployment"):
        evidence_lines.append(f"* *Affected Deployment:* {findings['affected_deployment']}")
    if findings.get("namespace"):
        evidence_lines.append(f"* *Namespace:* {findings['namespace']}")

    # Resources (dict with pods, service, etc.)
    resources = findings.get("resources")
    if isinstance(resources, dict) and resources:
        non_empty = {k: v for k, v in resources.items() if v}
        if non_empty:
            evidence_lines.append(f"* *Resources Checked:* {len(non_empty)} resource types ({', '.join(non_empty.keys())})")

    # Events (can be list or string)
    events = findings.get("events")
    if events:
        if isinstance(events, list) and events:
            evidence_lines.append(f"* *Events:* {len(events)} events detected")
        elif isinstance(events, str) and events.strip():
            evidence_lines.append("* *Events:* Events found in cluster")

    # Logs if available
    if findings.get("logs"):
        evidence_lines.append("* *Logs:* Log data collected")

    # Fallback: show other non-empty findings
    if not evidence_lines:
        FALLBACK_LIMIT = 5
        items = [(k, v) for k, v in findings.items() if v and k not in ("preliminary_findings", "logs")]
        for key, value in items[:FALLBACK_LIMIT]:
            if isinstance(value, list):
                evidence_lines.append(f"* *{key.replace('_', ' ').title()}:* {len(value)} items")
            elif isinstance(value, dict):
                evidence_lines.append(f"* *{key.replace('_', ' ').title()}:* {len(value)} entries")
            else:
                evidence_lines.append(f"* *{key.replace('_', ' ').title()}:* {value}")
        # Show truncation notice if data was omitted
        remaining = len(items) - FALLBACK_LIMIT
        if remaining > 0:
            evidence_lines.append(f"_...and {remaining} more findings_")

    return "\n".join(evidence_lines) if evidence_lines else "_See diagnosis for details_"


def _extract_recommended_commands(state: dict[str, Any]) -> list[str]:
    """Extract ordered kubectl commands from the remediation plan.

    Converts each actionable RemediationStep into the equivalent kubectl
    command string. manual_intervention steps are skipped.

    Security: Secret values are NEVER included in commands. Only key
    names are shown with placeholder values.

    Args:
        state: Current workflow state with optional remediation_plan dict.

    Returns:
        Ordered list of kubectl command strings. Empty if no actionable
        steps exist.
    """
    plan_dict = state.get("remediation_plan")
    if not plan_dict or not isinstance(plan_dict, dict):
        return []

    try:
        plan = RemediationPlan(**plan_dict)
    except Exception as e:
        logger.warning("Failed to parse remediation_plan for commands: %s", e)
        return []

    if not plan.remediation_possible:
        return []

    commands: list[str] = []
    for step in plan.steps:
        cmd = _step_to_command(step)
        if cmd:
            commands.append(cmd)

    return commands


def _step_to_command(step) -> str | None:
    """Convert a single RemediationStep to a kubectl command string.

    Returns None for manual_intervention or unknown action types.
    """
    action = step.action
    name = step.name
    ns = step.namespace
    resource_type = step.resource_type

    if action == ActionType.SCALE:
        replicas = step.replicas if step.replicas is not None else 1
        return (
            f"kubectl scale {resource_type} {name} "
            f"--replicas={replicas} -n {ns}"
        )

    if action == ActionType.RESTART:
        return (
            f"kubectl rollout restart {resource_type} {name} -n {ns}"
        )

    if action == ActionType.DELETE:
        return f"kubectl delete {resource_type} {name} -n {ns}"

    if action == ActionType.CREATE_CONFIGMAP:
        literals = _format_from_literal_flags(step.data or {})
        return (
            f"kubectl create configmap {name} {literals}-n {ns}"
        )

    if action == ActionType.CREATE_SECRET:
        # SECURITY: Never expose secret values -- use <REDACTED> placeholders
        masked = _format_secret_literal_flags(step.data or {})
        return (
            f"kubectl create secret generic {name} {masked}-n {ns}"
        )

    if action == ActionType.PATCH:
        patch_json = json.dumps(step.data or {})
        # SECURITY: Escape single quotes for safe shell interpolation (POSIX standard)
        safe_json = patch_json.replace("'", "'\\''")
        return (
            f"kubectl patch {resource_type} {name} -n {ns} "
            f"--type=merge -p '{safe_json}'"
        )

    if action == ActionType.APPLY_MANIFEST:
        yaml_content = step.yaml_content or ""
        # SECURITY: Escape EOF sentinel to prevent heredoc breakout
        safe_yaml = yaml_content.replace("EOF", "E_O_F")
        return f"kubectl apply -f - <<EOF\n{safe_yaml}\nEOF"

    # manual_intervention or unknown
    return None


def _format_from_literal_flags(data: dict[str, Any]) -> str:
    """Format --from-literal flags for configmap creation."""
    if not data:
        return ""
    parts = [f"--from-literal={k}={v}" for k, v in sorted(data.items())]
    return " ".join(parts) + " "


def _format_secret_literal_flags(data: dict[str, Any]) -> str:
    """Format --from-literal flags for secret creation with redacted values.

    Key names are shown but values are replaced with <REDACTED>
    to prevent secret exposure in Jira comments.
    """
    if not data:
        return ""
    parts = [f"--from-literal={k}=<REDACTED>" for k in sorted(data.keys())]
    return " ".join(parts) + " "


def _format_commands_section(commands: list[str]) -> str:
    """Format commands as a Jira wiki markup section with code block.

    Returns empty string if no commands, so the section is omitted entirely.
    """
    if not commands:
        return ""

    numbered = "\n".join(
        f"# Step {i}: {cmd}" if not cmd.startswith("kubectl apply")
        else f"# Step {i}:\n{cmd}"
        for i, cmd in enumerate(commands, start=1)
    )

    return f"""
{{panel:title=Recommended Commands|bgColor={PANEL_COLORS["fix"]["bg"]}|borderColor={PANEL_COLORS["fix"]["border"]}}}
{{code}}
{numbered}
{{code}}
{{panel}}
"""
