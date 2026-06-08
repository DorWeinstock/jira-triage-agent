"""Unit tests for langgraph_agent.src.utils.pod_name_parser."""

import pytest

from src.utils.pod_name_parser import (
    clean_pod_name,
    extract_all_pod_names,
    extract_namespace_and_pod,
    is_valid_k8s_pod_name,
)


# ---------------------------------------------------------------------------
# is_valid_k8s_pod_name
# ---------------------------------------------------------------------------

class TestIsValidK8sPodName:
    @pytest.mark.parametrize("name", [
        "api-server-5f8d6c7b-abc12",
        "cassandra-0",
        "nginx",
        "a",
        "a" * 253,
    ])
    def test_valid_names(self, name):
        assert is_valid_k8s_pod_name(name) is True

    @pytest.mark.parametrize("name", [
        "",
        "API-SERVER",
        "-pod",
        "pod-",
        "pod--name",
        "pod_with_underscores",
        "a" * 254,
    ])
    def test_invalid_names(self, name):
        assert is_valid_k8s_pod_name(name) is False


# ---------------------------------------------------------------------------
# clean_pod_name
# ---------------------------------------------------------------------------

class TestCleanPodName:
    @pytest.mark.parametrize("llm_response,expected", [
        # Already clean
        ("api-server-5f8d6c7b-abc12", "api-server-5f8d6c7b-abc12"),
        # Explanation prefix
        ("The pod is: api-server-5f8d6c7b-abc12", "api-server-5f8d6c7b-abc12"),
        ("I found the pod: nginx-6b4f8d7c-klm45", "nginx-6b4f8d7c-klm45"),
        # Markdown formatting
        ("`nginx-deployment-6b4f8d7c-klm45`", "nginx-deployment-6b4f8d7c-klm45"),
        ('"frontend-7d5f8c9b-xyz89"', "frontend-7d5f8c9b-xyz89"),
        ("**api-server-abc123-def45**", "api-server-abc123-def45"),
        # StatefulSet
        ("cassandra-0", "cassandra-0"),
        # Mixed case normalised
        ("API-SERVER-5F8D6C7B-ABC12", "api-server-5f8d6c7b-abc12"),
    ])
    def test_extraction(self, llm_response, expected):
        assert clean_pod_name(llm_response) == expected

    @pytest.mark.parametrize("llm_response", [
        "",
        "   ",
        "I couldn't find any pod name in the ticket.",
        "Unable to determine pod name from description.",
        "No pod mentioned in the ticket.",
    ])
    def test_no_pod_returns_empty(self, llm_response):
        assert clean_pod_name(llm_response) == ""

    def test_length_guard_truncates_before_regex(self):
        """Megabyte input must complete in < 100 ms (no ReDoS)."""
        import time
        big = "word " * 200_000 + " api-server-5f8d6c7b-abc12"
        t0 = time.time()
        clean_pod_name(big)
        assert time.time() - t0 < 0.1


# ---------------------------------------------------------------------------
# is_valid_k8s_pod_name — redundant islower guard removed (step 2)
# ---------------------------------------------------------------------------

class TestIsValidAfterRefactor:
    def test_digit_only_name_is_valid(self):
        # "123" has no islower() chars but must be accepted by RFC 1123 regex
        assert is_valid_k8s_pod_name("123") is True

    def test_uppercase_rejected_by_regex_alone(self):
        # Without the islower() guard, re.match still rejects uppercase
        assert is_valid_k8s_pod_name("Pod") is False


# ---------------------------------------------------------------------------
# extract_namespace_and_pod
# ---------------------------------------------------------------------------

class TestExtractNamespaceAndPod:
    def test_namespaced_clean_input_not_mangled(self):
        ns, pod = extract_namespace_and_pod("production/api-server-5f8d6c7b-abc12")
        assert ns == "production"
        assert pod == "api-server-5f8d6c7b-abc12"

    def test_no_namespace_defaults_to_default(self):
        ns, pod = extract_namespace_and_pod("cassandra-0")
        assert ns == "default"
        assert pod == "cassandra-0"

    def test_dirty_pod_part_still_cleaned(self):
        ns, pod = extract_namespace_and_pod("default/The pod is: nginx-abc123-xyz45")
        assert ns == "default"
        assert pod != ""

    def test_empty_input_returns_defaults(self):
        ns, pod = extract_namespace_and_pod("")
        assert ns == "default"
        assert pod == ""


# ---------------------------------------------------------------------------
# extract_all_pod_names
# ---------------------------------------------------------------------------

class TestExtractAllPodNames:
    def test_numbered_list(self):
        text = """
        1. api-server-5f8d6c7b-abc12
        2. frontend-7d5f8c9b-xyz89
        """
        names = extract_all_pod_names(text)
        assert "api-server-5f8d6c7b-abc12" in names
        assert "frontend-7d5f8c9b-xyz89" in names

    def test_deduplication(self):
        text = "api-server-5f8d6c7b-abc12 and api-server-5f8d6c7b-abc12 again"
        names = extract_all_pod_names(text)
        assert names.count("api-server-5f8d6c7b-abc12") == 1

    def test_statefulset_pods(self):
        names = extract_all_pod_names("cassandra-0 and cassandra-1 are failing")
        assert "cassandra-0" in names
        assert "cassandra-1" in names

    def test_empty_returns_empty_list(self):
        assert extract_all_pod_names("") == []
        assert extract_all_pod_names("No pods found.") == []

    def test_kubectl_header_not_extracted(self):
        text = "NAME  READY  STATUS\napi-server-abc12-xyz89  1/1  Running"
        names = extract_all_pod_names(text)
        assert "NAME" not in names
        assert "STATUS" not in names

    def test_length_guard_truncates_before_regex(self):
        import time
        big = "word " * 200_000
        t0 = time.time()
        extract_all_pod_names(big)
        assert time.time() - t0 < 0.1
