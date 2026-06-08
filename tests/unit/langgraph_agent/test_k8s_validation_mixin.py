"""Tests for K8sValidationMixin validation methods.

This module tests the shared validation logic used by K8sTools and
ReadOnlyK8sTools. The mixin provides standardized validation for
Kubernetes resource names, namespaces, and resource types.
"""

import pytest

from src.exceptions import ValidationError
from src.tools.k8s_validation import (
    K8sValidationMixin,
    K8S_NAME_PATTERN,
    K8S_NAMESPACE_PATTERN,
    VALID_RESOURCE_TYPES,
)


class MockClient(K8sValidationMixin):
    """Mock client for testing validation mixin methods."""

    client_name = "TestClient"


@pytest.fixture
def validator():
    """Create a mock client for validation testing."""
    return MockClient()


class TestValidateNamespace:
    """Tests for _validate_namespace() method."""

    def test_valid_namespace(self, validator):
        """Valid namespace names should not raise."""
        valid_names = [
            "default",
            "kube-system",
            "my-namespace",
            "namespace123",
            "a",
            "a1b2c3",
        ]
        for name in valid_names:
            validator._validate_namespace(name)  # Should not raise

    def test_empty_namespace_raises(self, validator):
        """Empty namespace should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_namespace("")

        assert "cannot be empty" in str(exc_info.value).lower()
        assert exc_info.value.field == "namespace"
        assert exc_info.value.agent_name == "TestClient"

    def test_none_namespace_raises(self, validator):
        """None namespace should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_namespace(None)

        assert "cannot be empty" in str(exc_info.value).lower()

    def test_invalid_format_uppercase(self, validator):
        """Uppercase namespace should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_namespace("MyNamespace")

        assert "invalid namespace format" in str(exc_info.value).lower()
        assert exc_info.value.field == "namespace"

    def test_invalid_format_starting_with_hyphen(self, validator):
        """Namespace starting with hyphen should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_namespace("-invalid")

        assert "invalid namespace format" in str(exc_info.value).lower()

    def test_invalid_format_ending_with_hyphen(self, validator):
        """Namespace ending with hyphen should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_namespace("invalid-")

        assert "invalid namespace format" in str(exc_info.value).lower()

    def test_invalid_format_special_characters(self, validator):
        """Namespace with special characters should raise ValidationError."""
        invalid_names = ["my_namespace", "my.namespace", "my@namespace"]
        for name in invalid_names:
            with pytest.raises(ValidationError) as exc_info:
                validator._validate_namespace(name)

            assert "invalid namespace format" in str(exc_info.value).lower()

    def test_too_long_namespace(self, validator):
        """Namespace exceeding 63 characters should raise ValidationError."""
        long_name = "a" * 64
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_namespace(long_name)

        assert "invalid namespace format" in str(exc_info.value).lower()

    def test_max_length_namespace(self, validator):
        """Namespace at exactly 63 characters should be valid."""
        max_name = "a" * 63
        validator._validate_namespace(max_name)  # Should not raise


class TestValidateResourceName:
    """Tests for _validate_resource_name() method."""

    def test_valid_resource_name(self, validator):
        """Valid resource names should not raise."""
        valid_names = [
            "my-pod",
            "api-server-abc123",
            "nginx",
            "a",
            "deployment-v2",
        ]
        for name in valid_names:
            validator._validate_resource_name(name)  # Should not raise

    def test_empty_name_raises(self, validator):
        """Empty resource name should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_resource_name("")

        assert "cannot be empty" in str(exc_info.value).lower()
        assert exc_info.value.field == "name"
        assert exc_info.value.agent_name == "TestClient"

    def test_none_name_raises(self, validator):
        """None resource name should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_resource_name(None)

        assert "cannot be empty" in str(exc_info.value).lower()

    def test_invalid_format_uppercase(self, validator):
        """Uppercase resource name should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_resource_name("MyPod")

        assert "invalid" in str(exc_info.value).lower()
        assert "format" in str(exc_info.value).lower()

    def test_custom_field_name(self, validator):
        """Custom field name should appear in error message."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_resource_name("", field="pod_name")

        assert exc_info.value.field == "pod_name"
        assert "Pod Name cannot be empty" in str(exc_info.value)

    def test_custom_field_name_with_underscores(self, validator):
        """Field names with underscores should be formatted as titles."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_resource_name("", field="container_name")

        assert "Container Name cannot be empty" in str(exc_info.value)

    def test_invalid_format_special_characters(self, validator):
        """Resource name with special characters should raise."""
        invalid_names = ["my_pod", "my.pod", "my:pod", "pod/name"]
        for name in invalid_names:
            with pytest.raises(ValidationError):
                validator._validate_resource_name(name)

    def test_max_length_resource_name(self, validator):
        """Resource name at 253 characters should be valid."""
        max_name = "a" * 253
        validator._validate_resource_name(max_name)  # Should not raise

    def test_too_long_resource_name(self, validator):
        """Resource name exceeding 253 characters should raise."""
        long_name = "a" * 254
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_resource_name(long_name)

        assert "invalid" in str(exc_info.value).lower()


class TestValidateResourceType:
    """Tests for _validate_resource_type() method."""

    def test_valid_resource_types(self, validator):
        """All valid resource types should not raise."""
        for resource_type in VALID_RESOURCE_TYPES:
            validator._validate_resource_type(resource_type)  # Should not raise

    def test_valid_resource_type_case_insensitive(self, validator):
        """Resource types should be validated case-insensitively."""
        validator._validate_resource_type("Pods")  # Should not raise
        validator._validate_resource_type("DEPLOYMENTS")  # Should not raise
        validator._validate_resource_type("Services")  # Should not raise

    def test_empty_resource_type_raises(self, validator):
        """Empty resource type should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_resource_type("")

        assert "cannot be empty" in str(exc_info.value).lower()
        assert exc_info.value.field == "resource_type"
        assert exc_info.value.agent_name == "TestClient"

    def test_none_resource_type_raises(self, validator):
        """None resource type should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_resource_type(None)

        assert "cannot be empty" in str(exc_info.value).lower()

    def test_invalid_resource_type(self, validator):
        """Invalid resource type should raise ValidationError."""
        invalid_types = ["containers", "volumes", "unknown", "cluster"]
        for resource_type in invalid_types:
            with pytest.raises(ValidationError) as exc_info:
                validator._validate_resource_type(resource_type)

            assert "invalid resource type" in str(exc_info.value).lower()
            assert resource_type in str(exc_info.value)

    def test_error_lists_valid_types(self, validator):
        """Error message should list valid resource types."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_resource_type("invalid")

        error_message = str(exc_info.value)
        assert "pods" in error_message.lower()
        assert "deployments" in error_message.lower()


class TestValidateTailLines:
    """Tests for _validate_tail_lines() method."""

    def test_none_tail_lines(self, validator):
        """None tail lines should not raise."""
        validator._validate_tail_lines(None)  # Should not raise

    def test_zero_tail_lines(self, validator):
        """Zero tail lines should not raise."""
        validator._validate_tail_lines(0)  # Should not raise

    def test_positive_tail_lines(self, validator):
        """Positive tail lines should not raise."""
        validator._validate_tail_lines(1)  # Should not raise
        validator._validate_tail_lines(100)  # Should not raise
        validator._validate_tail_lines(10000)  # Should not raise

    def test_negative_tail_lines_raises(self, validator):
        """Negative tail lines should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_tail_lines(-1)

        assert "non-negative" in str(exc_info.value).lower()
        assert exc_info.value.field == "tail_lines"
        assert exc_info.value.value == "-1"
        assert exc_info.value.agent_name == "TestClient"

    def test_large_negative_tail_lines_raises(self, validator):
        """Large negative tail lines should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validator._validate_tail_lines(-1000)

        assert "non-negative" in str(exc_info.value).lower()
        assert "-1000" in str(exc_info.value)


class TestPatternConstants:
    """Tests for regex pattern constants."""

    def test_namespace_pattern_matches_valid(self):
        """K8S_NAMESPACE_PATTERN should match valid namespaces."""
        valid = ["default", "kube-system", "a", "a1b2", "ns-123"]
        for name in valid:
            assert K8S_NAMESPACE_PATTERN.match(name), f"Should match: {name}"

    def test_namespace_pattern_rejects_invalid(self):
        """K8S_NAMESPACE_PATTERN should reject invalid namespaces."""
        invalid = ["", "-start", "end-", "UPPER", "under_score", "a" * 64]
        for name in invalid:
            assert not K8S_NAMESPACE_PATTERN.match(name), f"Should reject: {name}"

    def test_name_pattern_matches_valid(self):
        """K8S_NAME_PATTERN should match valid resource names."""
        valid = ["pod", "my-pod", "pod-123", "a", "a1b2c3"]
        for name in valid:
            assert K8S_NAME_PATTERN.match(name), f"Should match: {name}"

    def test_name_pattern_rejects_invalid(self):
        """K8S_NAME_PATTERN should reject invalid resource names."""
        invalid = ["", "-start", "end-", "UPPER", "under_score"]
        for name in invalid:
            assert not K8S_NAME_PATTERN.match(name), f"Should reject: {name}"


class TestValidResourceTypes:
    """Tests for VALID_RESOURCE_TYPES constant."""

    def test_contains_common_resources(self):
        """VALID_RESOURCE_TYPES should contain common K8s resources."""
        expected = [
            "pods",
            "deployments",
            "services",
            "configmaps",
            "secrets",
            "namespaces",
            "nodes",
            "events",
        ]
        for resource in expected:
            assert resource in VALID_RESOURCE_TYPES, f"Missing: {resource}"

    def test_is_frozenset(self):
        """VALID_RESOURCE_TYPES should be immutable (frozenset)."""
        assert isinstance(VALID_RESOURCE_TYPES, frozenset)

    def test_all_lowercase(self):
        """All resource types should be lowercase."""
        for resource in VALID_RESOURCE_TYPES:
            assert resource == resource.lower(), f"Not lowercase: {resource}"
