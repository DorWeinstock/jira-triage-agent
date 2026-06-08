"""
Utility functions for parsing and cleaning pod names from LLM responses

This module addresses the issue where LLMs return explanatory text
alongside pod names (e.g., "The pod is: api-server-abc123-xyz45")
instead of returning just the pod name.
"""

import re

_MAX_INPUT_LEN = 10_000  # characters; LLM pod-name responses never exceed this


def clean_pod_name(llm_response: str) -> str:
    """
    Clean LLM response to extract pod name

    Removes:
    - Explanatory text ("The pod is:", "I found:", etc.)
    - Markdown formatting (backticks, quotes, bold)
    - Surrounding whitespace
    - Case normalization to lowercase

    Args:
        llm_response: Raw text from LLM that may contain pod name

    Returns:
        Cleaned pod name or empty string if none found

    Examples:
        >>> clean_pod_name("The pod is: api-server-5f8d6c7b-abc12")
        'api-server-5f8d6c7b-abc12'

        >>> clean_pod_name("`nginx-deployment-6b4f8d7c-klm45`")
        'nginx-deployment-6b4f8d7c-klm45'

        >>> clean_pod_name("I couldn't find any pod name")
        ''
    """
    if not llm_response:
        return ""

    llm_response = llm_response[:_MAX_INPUT_LEN]
    text = llm_response.strip()

    # Remove common markdown formatting
    # First, handle code blocks (triple backticks) specially
    text = re.sub(r'^```(?:[a-z]+)?(?:\n|\s)', '', text)  # Code block start with language
    text = re.sub(r'^```', '', text)  # Code block start without language
    text = re.sub(r'```$', '', text)  # Code block end

    # Now remove single backticks, quotes, and other formatting
    text = re.sub(r'[`"\']', '', text)  # Backticks and quotes
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # Bold
    text = re.sub(r'\*([^*]+)\*', r'\1', text)  # Italic

    # Remove common LLM explanation patterns
    explanation_patterns = [
        r'(?i)^.*?(?:pod|name)\s+(?:is|name):\s*',  # "The pod is:", "Pod name:"
        r'(?i)^.*?(?:found|identified)\s+(?:the\s+)?pod:\s*',  # "I found the pod:"
        r'(?i)^.*?problematic\s+pod\s+(?:is|name):\s*',  # "The problematic pod is:"
        r'(?i)^based on.*?,\s*(?:the\s+)?pod\s+(?:is|name)\s+',  # "Based on..., the pod is"
        r"(?i)^.*?couldn't find.*",  # "I couldn't find any pod..."
        r'(?i)^.*?unable to.*',  # "Unable to determine pod name..."
        r'(?i)^.*?no pod.*',  # "No pod mentioned..."
    ]

    for pattern in explanation_patterns:
        text = re.sub(pattern, '', text)

    # Extract pod name pattern from remaining text
    # Kubernetes pod naming: lowercase alphanumeric + hyphens
    # Common patterns:
    # 1. deployment-name-replicaset_hash-pod_hash (e.g., api-server-5f8d6c7b-abc12)
    # 2. statefulset-name-ordinal (e.g., cassandra-0)
    # 3. simple pod name (e.g., nginx)

    # Try standard deployment pattern first (name-hash-hash)
    # This pattern is most specific, so try it first
    match = re.search(r'\b([a-z0-9]+(?:-[a-z0-9]+)*-[a-z0-9]{8,10}-[a-z0-9]{5})\b', text.lower())
    if match:
        return match.group(1)

    # Try StatefulSet pattern (name-ordinal)
    match = re.search(r'\b([a-z0-9]+(?:-[a-z0-9]+)*-\d+)\b', text.lower())
    if match:
        candidate = match.group(1)
        # Avoid matching things like "mentioned-1" or other non-pod words
        # Validate it looks like a real pod name (has meaningful parts)
        if len(candidate) >= 5:  # Reasonable min length for pod name
            return candidate

    # Try simple pod name (alphanumeric with hyphens, at least 6 chars)
    # Higher threshold than 3 to reduce false positives from common phrases.
    match = re.search(r'\b([a-z0-9][a-z0-9-]{5,}[a-z0-9])\b', text.lower())
    if match:
        pod_name = match.group(1)
        # Use the canonical validator as a secondary check, plus a minimal
        # filter for common false-positive patterns that appear in LLM text.
        if is_valid_k8s_pod_name(pod_name):
            # Avoid obvious explanation words (whole-word match only)
            # Pod names like "alertmanager" should not be rejected just because they
            # contain the substring "describe".
            filter_words = {'couldnt', 'describe', 'determine', 'unable', 'problem', 'ticket'}
            # Split by hyphens and check if any part is a filter word
            parts = pod_name.split('-')
            if not any(part in filter_words for part in parts):
                return pod_name

    return ""


def is_valid_k8s_pod_name(name: str) -> bool:
    """
    Validate pod name follows Kubernetes naming conventions

    Rules (RFC 1123 subdomain):
    - Lowercase alphanumeric and hyphens only
    - Must start with alphanumeric
    - Must end with alphanumeric
    - Max 253 characters
    - At least 1 character
    - No consecutive hyphens

    Args:
        name: Pod name to validate

    Returns:
        True if valid Kubernetes pod name

    Examples:
        >>> is_valid_k8s_pod_name("api-server-5f8d6c7b-abc12")
        True

        >>> is_valid_k8s_pod_name("API-SERVER")  # Uppercase not allowed
        False

        >>> is_valid_k8s_pod_name("pod_with_underscores")  # Underscores not allowed
        False
    """
    if not name:
        return False

    # Must match RFC 1123 subdomain pattern
    if not re.match(r'^[a-z0-9]([a-z0-9-]{0,251}[a-z0-9])?$', name):
        return False

    # Cannot contain consecutive hyphens
    if '--' in name:
        return False

    return True


def extract_namespace_and_pod(namespaced_name: str) -> tuple[str, str]:
    """
    Extract namespace and pod name from 'namespace/pod-name' format

    Args:
        namespaced_name: String in format "namespace/pod-name" or just "pod-name"

    Returns:
        Tuple of (namespace, pod_name). If no namespace provided, uses "default"

    Examples:
        >>> extract_namespace_and_pod("production/api-server-abc123-xyz45")
        ('production', 'api-server-abc123-xyz45')

        >>> extract_namespace_and_pod("nginx-deployment-abc123-xyz45")
        ('default', 'nginx-deployment-abc123-xyz45')
    """
    if '/' in namespaced_name:
        parts = namespaced_name.split('/', 1)
        namespace = parts[0].strip()
        pod_full = parts[1].strip()
    else:
        namespace = "default"
        pod_full = namespaced_name.strip()

    # Only run LLM cleaning if the raw value isn't already a valid pod name.
    # This prevents the cleaning pipeline from mangling clean kubectl-style input.
    if is_valid_k8s_pod_name(pod_full):
        pod_name = pod_full
    else:
        pod_name = clean_pod_name(pod_full)
    return namespace, pod_name


def extract_all_pod_names(text: str) -> list[str]:
    """
    Extract all valid pod names from text

    Useful when LLM returns multiple pod names in a list or narrative format.
    Automatically deduplicates and validates pod names.

    Args:
        text: Text potentially containing multiple pod names

    Returns:
        Sorted list of unique, valid pod names found in text

    Examples:
        >>> text = \"\"\"
        ... The following pods are failing:
        ... 1. api-server-5f8d6c7b-abc12
        ... 2. frontend-7d5f8c9b-xyz89
        ... \"\"\"
        >>> extract_all_pod_names(text)
        ['api-server-5f8d6c7b-abc12', 'frontend-7d5f8c9b-xyz89']
    """
    if not text:
        return []

    text = text[:_MAX_INPUT_LEN]
    pod_names = set()

    # Pattern 1: Standard deployment pods (name-hash-hash)
    # More flexible pattern to handle various hash lengths (5-10 chars each)
    matches = re.findall(r'\b([a-z0-9]+(?:-[a-z0-9]+)*-[a-z0-9]{5,10}-[a-z0-9]{5,10})\b', text.lower())
    pod_names.update(matches)

    # Pattern 2: StatefulSet pods (name-ordinal)
    matches = re.findall(r'\b([a-z0-9]+(?:-[a-z0-9]+)*-\d+)\b', text.lower())
    pod_names.update(matches)

    # Filter out common false positives
    filtered_names = []
    filter_words = {'name', 'ready', 'status', 'restarts', 'age'}
    for name in pod_names:
        # Must be valid K8s pod name
        if is_valid_k8s_pod_name(name):
            # Filter out common non-pod words (whole-word match only, not substrings)
            # Pod names like "alertmanager" should not be rejected just because they
            # contain the substring "name".
            parts = name.split('-')
            if not any(part in filter_words for part in parts):
                filtered_names.append(name)

    return sorted(filtered_names)
