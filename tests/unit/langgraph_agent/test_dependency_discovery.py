"""Unit tests for dependency_discovery module.

Tests cover:
- Pod name extraction from deployment names
- Service reference extraction and validation
- Deployment name parsing from kubectl output
- Issue classification by relationship to target
- Edge cases and error scenarios
"""

import pytest
from src.agents.dependency_discovery import (
    get_deployment_from_pod_name,
    extract_service_references,
    extract_deployment_names,
    classify_namespace_issues,
)


# --- get_deployment_from_pod_name ---

@pytest.mark.parametrize("pod_name,expected", [
    ("order-service-5f8d6c7b-abc12", "order-service"),
    ("api-server-prod-7c9d8e6f-xyz99", "api-server-prod"),
    ("simple-abc12", "simple"),
    ("single", "single"),
])
def test_get_deployment_from_pod_name(pod_name, expected):
    """Test deployment name extraction from various pod name formats."""
    assert get_deployment_from_pod_name(pod_name) == expected


def test_get_deployment_from_pod_name_single_segment(caplog):
    """Test that single-segment pod names are returned as-is and logged."""
    import logging
    with caplog.at_level(logging.DEBUG):
        pod_name = "single"
        result = get_deployment_from_pod_name(pod_name)
    assert result == "single"
    assert "no hyphen segments" in caplog.text.lower()


def test_get_deployment_from_pod_name_one_suffix(caplog):
    """Test that pod names with only one suffix segment are logged as inaccurate."""
    import logging
    with caplog.at_level(logging.DEBUG):
        pod_name = "simple-abc12"
        result = get_deployment_from_pod_name(pod_name)
    assert result == "simple"
    assert "only one suffix segment" in caplog.text.lower()


# --- extract_service_references: input validation ---

def test_extract_service_references_raises_on_empty_namespace():
    """Test that empty namespace raises ValueError."""
    with pytest.raises(ValueError, match="namespace"):
        extract_service_references("some yaml", "")


def test_extract_service_references_raises_on_none_namespace():
    """Test that None namespace raises ValueError."""
    with pytest.raises(ValueError, match="namespace"):
        extract_service_references("some yaml", None)


def test_extract_service_references_raises_on_whitespace_only_namespace():
    """Test that whitespace-only namespace raises ValueError."""
    with pytest.raises(ValueError, match="namespace"):
        extract_service_references("some yaml", "   ")


def test_extract_service_references_empty_yaml_returns_empty():
    """Test that empty YAML returns empty list."""
    assert extract_service_references("", "default") == []


def test_extract_service_references_none_yaml_returns_empty():
    """Test that None YAML returns empty list."""
    assert extract_service_references(None, "default") == []


# --- extract_service_references: DNS regex ---

def test_extract_service_references_finds_svc_dns():
    """Test finding standard .svc.cluster.local DNS references."""
    yaml = "value: my-service.default.svc.cluster.local"
    refs = extract_service_references(yaml, "default")
    assert any(r["service"] == "my-service" for r in refs)


def test_extract_service_references_finds_svc_without_cluster_local():
    """Test finding .svc references without cluster.local suffix."""
    yaml = "value: my-service.default.svc"
    refs = extract_service_references(yaml, "default")
    assert any(r["service"] == "my-service" for r in refs)


def test_extract_service_references_no_short_token_false_positive():
    """Test that 2-char tokens are not matched (too short)."""
    yaml = "value: ab.default.svc.cluster.local"
    refs = extract_service_references(yaml, "default")
    assert not any(r["service"] == "ab" for r in refs)


def test_extract_service_references_no_single_char_false_positive():
    """Test that 1-char tokens are not matched."""
    yaml = "value: a.default.svc"
    refs = extract_service_references(yaml, "default")
    assert not any(r["service"] == "a" for r in refs)


def test_extract_service_references_finds_valid_short_service():
    """Test that 4+ char service names are matched."""
    yaml = "value: test.default.svc.cluster.local"
    refs = extract_service_references(yaml, "default")
    assert any(r["service"] == "test" for r in refs)


def test_extract_service_references_no_ip_false_positive():
    """Test that IP-like patterns are not harvested as service names."""
    yaml = 'DB_HOST: "192.168.1.1"\nDB_URL: "database.default.svc"'
    refs = extract_service_references(yaml, "default")
    # Should find database but not 192 or 168 or 1
    service_names = [r["service"] for r in refs]
    assert not any(name.startswith("192") for name in service_names)
    assert not any(name == "1" for name in service_names)


def test_extract_service_references_env_var_host():
    """Test extraction from HOST environment variable pattern."""
    yaml = '"DB_HOST": "mydb.default.svc.cluster.local"'
    refs = extract_service_references(yaml, "default")
    assert any(r["service"] == "mydb" for r in refs)


def test_extract_service_references_env_var_url():
    """Test extraction from URL environment variable pattern."""
    yaml = '"API_URL": "api-service.default.svc.cluster.local"'
    refs = extract_service_references(yaml, "default")
    assert any(r["service"] == "api-service" for r in refs)


def test_extract_service_references_env_var_endpoint():
    """Test extraction from ENDPOINT environment variable pattern."""
    yaml = '"SERVICE_ENDPOINT": "endpoint-svc.default.svc"'
    refs = extract_service_references(yaml, "default")
    assert any(r["service"] == "endpoint-svc" for r in refs)


def test_extract_service_references_env_var_service():
    """Test extraction from SERVICE environment variable pattern."""
    yaml = '"MY_SERVICE": "myservice.default.svc.cluster.local"'
    refs = extract_service_references(yaml, "default")
    assert any(r["service"] == "myservice" for r in refs)


def test_extract_service_references_deduplicates():
    """Test that duplicate service names are deduplicated."""
    yaml = """
    value1: my-service.default.svc.cluster.local
    value2: my-service.default.svc
    """
    refs = extract_service_references(yaml, "default")
    service_names = [r["service"] for r in refs]
    assert service_names.count("my-service") == 1


def test_extract_service_references_no_env_var_with_short_service():
    """Test that env-var extraction also respects min 4-char limit."""
    yaml = '"DB_HOST": "ab.example.com"'
    refs = extract_service_references(yaml, "default")
    # "ab" is only 2 chars, should not be extracted
    assert not any(r["service"] == "ab" for r in refs)


# --- extract_deployment_names ---

def test_extract_deployment_names_skips_header():
    """Test that kubectl header line is skipped."""
    output = "NAME   READY   UP-TO-DATE\nnginx  1/1     1\n"
    names = extract_deployment_names(output)
    assert names == ["nginx"]
    assert "NAME" not in names


def test_extract_deployment_names_does_not_skip_name_containing_deployment():
    """Test that deployments with 'NAME' in their name are NOT skipped.
    
    This is the regression test for the old 'NAME' in line check.
    """
    output = "NAME   READY\nnamespace-controller  1/1\n"
    names = extract_deployment_names(output)
    assert "namespace-controller" in names


def test_extract_deployment_names_multiple_name_containing():
    """Test multiple deployments that contain 'NAME' substring."""
    output = """NAME             READY   STATUS
namespace-ctrl   1/1     Running
name-checker     1/1     Running
my-app           1/1     Running
"""
    names = extract_deployment_names(output)
    assert "namespace-ctrl" in names
    assert "name-checker" in names
    assert "my-app" in names
    # Header should not be included
    assert len(names) == 3


def test_extract_deployment_names_skips_no_resources():
    """Test that 'No resources' message is skipped."""
    output = "No resources found in default namespace.\n"
    names = extract_deployment_names(output)
    assert names == []


def test_extract_deployment_names_empty_input():
    """Test that empty input returns empty list."""
    assert extract_deployment_names("") == []


def test_extract_deployment_names_only_header():
    """Test that input with only header returns empty list."""
    output = "NAME   READY   UP-TO-DATE   AVAILABLE\n"
    names = extract_deployment_names(output)
    assert names == []


def test_extract_deployment_names_filters_short_names():
    """Test that names with length < 2 are filtered out."""
    output = """NAME   READY
a      1/1
ab     1/1
abc    1/1
"""
    names = extract_deployment_names(output)
    # "a" should be filtered (len < 2), "ab" and "abc" should pass (len >= 2)
    assert "a" not in names
    assert "ab" in names
    assert "abc" in names


def test_extract_deployment_names_filters_equals_sign():
    """Test that lines starting with '=' are filtered out."""
    output = """NAME        READY
==========  =====
my-deploy   1/1
"""
    names = extract_deployment_names(output)
    assert names == ["my-deploy"]
    assert not any("=" in name for name in names)


def test_extract_deployment_names_multi_column():
    """Test parsing multi-column kubectl output."""
    output = """NAME           READY   UP-TO-DATE   AVAILABLE   AGE
nginx-deploy   3/3     3            3           2d
api-server     2/2     2            2           5h
"""
    names = extract_deployment_names(output)
    assert "nginx-deploy" in names
    assert "api-server" in names
    assert len(names) == 2


# --- classify_namespace_issues ---

def test_classify_namespace_issues_target_issue():
    """Test that pods from target deployment are classified as target_issues."""
    findings = {
        "resources": {
            "pods": "NAME                      READY  STATUS\ntarget-deploy-abc-def     0/1    CrashLoopBackOff\n"
        }
    }
    result = classify_namespace_issues(findings, "target-deploy", {"verified_dependencies": []})
    
    assert len(result["target_issues"]) == 1
    assert result["target_issues"][0]["pod"] == "target-deploy-abc-def"
    assert len(result["dependency_issues"]) == 0
    assert len(result["unrelated_issues"]) == 0


def test_classify_namespace_issues_dependency_issue():
    """Test that pods from verified dependencies are classified as dependency_issues."""
    findings = {
        "resources": {
            "pods": "NAME                   READY  STATUS\ndatabase-xyz-123       0/1    Error\n"
        }
    }
    dependencies = {
        "verified_dependencies": [{"name": "database"}],
        "evidence": {"database": "service reference"}
    }
    result = classify_namespace_issues(findings, "target-deploy", dependencies)
    
    assert len(result["dependency_issues"]) == 1
    assert result["dependency_issues"][0]["pod"] == "database-xyz-123"
    assert result["dependency_issues"][0]["dependency"] == "database"
    assert len(result["target_issues"]) == 0


def test_classify_namespace_issues_unrelated_issue():
    """Test that unrelated pods are classified as unrelated_issues."""
    findings = {
        "resources": {
            "pods": "NAME               READY  STATUS\nunrelated-app-abc  0/1    Pending\n"
        }
    }
    result = classify_namespace_issues(findings, "target-deploy", {"verified_dependencies": []})
    
    assert len(result["unrelated_issues"]) == 1
    assert result["unrelated_issues"][0]["pod"] == "unrelated-app-abc"
    assert "NO EVIDENCE" in result["unrelated_issues"][0]["relationship"]


def test_classify_namespace_issues_header_line_not_misclassified():
    """Test that header line is not misclassified as an issue.
    
    This is the regression test for the old 'NAME' in line check.
    """
    findings = {
        "resources": {
            "pods": "NAME   READY   STATUS\nnginx-abc-def  1/1  CrashLoopBackOff\n"
        }
    }
    result = classify_namespace_issues(findings, "unrelated", {"verified_dependencies": []})
    
    all_pods = (
        [i["pod"] for i in result["target_issues"]]
        + [i["pod"] for i in result["dependency_issues"]]
        + [i["pod"] for i in result["unrelated_issues"]]
    )
    # Header row should not be present
    assert "NAME" not in all_pods
    # But the actual pod should be
    assert "nginx-abc-def" in all_pods


def test_classify_namespace_issues_multiple_problems():
    """Test classification with multiple problems in different categories."""
    findings = {
        "resources": {
            "pods": """NAME                     READY  STATUS
target-deploy-hash-123   0/1    CrashLoopBackOff
redis-xyz-456            0/1    Error
cache-memcached-789      1/1    Running
"""
        }
    }
    dependencies = {
        "verified_dependencies": [{"name": "redis"}],
        "evidence": {"redis": "env var"}
    }
    result = classify_namespace_issues(findings, "target-deploy", dependencies)
    
    assert len(result["target_issues"]) == 1
    assert len(result["dependency_issues"]) == 1  # redis pod matches
    # cache-memcached is running (no error), won't be classified


def test_classify_namespace_issues_ignores_non_error_pods():
    """Test that only pods with error indicators are classified."""
    findings = {
        "resources": {
            "pods": """NAME          READY  STATUS
app-abc-123   1/1    Running
app-def-456   0/1    CrashLoopBackOff
"""
        }
    }
    result = classify_namespace_issues(findings, "app", {"verified_dependencies": []})
    
    # Only CrashLoopBackOff pod should be classified
    all_issues = result["target_issues"] + result["dependency_issues"] + result["unrelated_issues"]
    assert len(all_issues) == 1
    assert "app-def-456" in all_issues[0]["pod"]


def test_classify_namespace_issues_recognizes_multiple_error_indicators():
    """Test that all error indicators are recognized."""
    error_indicators = ["CrashLoopBackOff", "Error", "ImagePullBackOff", "Pending", "CreateContainerConfigError"]
    
    for indicator in error_indicators:
        findings = {
            "resources": {
                "pods": f"NAME         READY  STATUS\ntest-abc123  0/1    {indicator}\n"
            }
        }
        result = classify_namespace_issues(findings, "unrelated", {"verified_dependencies": []})
        
        all_issues = result["target_issues"] + result["dependency_issues"] + result["unrelated_issues"]
        assert len(all_issues) == 1, f"Should recognize {indicator}"
