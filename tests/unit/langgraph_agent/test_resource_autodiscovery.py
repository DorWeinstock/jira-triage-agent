"""Tests for K8sInvestigator resource auto-discovery from deployment specs."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.k8s_investigator import K8sInvestigator


def _make_investigator(mock_tools=None):
    """Create K8sInvestigator with mocked tools and LLM."""
    if mock_tools is None:
        mock_tools = MagicMock()
    with patch("src.agents.k8s_investigator.create_diagnosis_llm") as mock_llm_factory:
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="mock analysis"))
        mock_llm_factory.return_value = mock_llm
        investigator = K8sInvestigator(mock_tools)
    return investigator


class TestDiscoverReferencedResources:
    """Test _discover_referenced_resources parsing of REFERENCED_RESOURCES section."""

    @pytest.fixture
    def investigator(self):
        """Create K8sInvestigator with mocked tools."""
        return _make_investigator()

    def test_parse_full_referenced_resources(self, investigator):
        """Parse deployment data with all reference types."""
        deployment_data = """Found 1 deployment(s):

NAME: notification-service
  NAMESPACE: production
  REPLICAS: 2/2 (ready/desired)
  AVAILABLE: 2
  UP-TO-DATE: 2
  AGE: 5d
  REFERENCED_RESOURCES:
    CONFIGMAPS: app-settings, notification-config
    SECRETS: db-credentials, tls-cert
    SERVICE_ACCOUNT: notification-sa
    PVCS: data-volume
"""
        result = investigator._discover_referenced_resources(deployment_data)

        assert sorted(result["configmaps"]) == ["app-settings", "notification-config"]
        assert sorted(result["secrets"]) == ["db-credentials", "tls-cert"]
        assert result["service_accounts"] == ["notification-sa"]
        assert result["pvcs"] == ["data-volume"]

    def test_parse_configmaps_only(self, investigator):
        """Parse deployment with only configmap references."""
        deployment_data = """Found 1 deployment(s):

NAME: simple-app
  NAMESPACE: production
  REPLICAS: 1/1 (ready/desired)
  REFERENCED_RESOURCES:
    CONFIGMAPS: app-config
"""
        result = investigator._discover_referenced_resources(deployment_data)

        assert result["configmaps"] == ["app-config"]
        assert result["secrets"] == []
        assert result["service_accounts"] == []
        assert result["pvcs"] == []

    def test_parse_no_referenced_resources_section(self, investigator):
        """Handle deployment data with no REFERENCED_RESOURCES section."""
        deployment_data = """Found 1 deployment(s):

NAME: bare-deployment
  NAMESPACE: production
  REPLICAS: 1/1 (ready/desired)
  AVAILABLE: 1
  UP-TO-DATE: 1
  AGE: 3d
"""
        result = investigator._discover_referenced_resources(deployment_data)

        assert result["configmaps"] == []
        assert result["secrets"] == []
        assert result["service_accounts"] == []
        assert result["pvcs"] == []

    def test_parse_empty_string(self, investigator):
        """Handle empty deployment data."""
        result = investigator._discover_referenced_resources("")

        assert result["configmaps"] == []
        assert result["secrets"] == []
        assert result["service_accounts"] == []
        assert result["pvcs"] == []

    def test_parse_multiple_deployments(self, investigator):
        """Parse data with multiple deployments - aggregate all references."""
        deployment_data = """Found 2 deployment(s):

NAME: frontend
  NAMESPACE: production
  REPLICAS: 2/2 (ready/desired)
  REFERENCED_RESOURCES:
    CONFIGMAPS: frontend-config
    SECRETS: tls-cert

NAME: backend
  NAMESPACE: production
  REPLICAS: 3/3 (ready/desired)
  REFERENCED_RESOURCES:
    CONFIGMAPS: backend-config, shared-config
    SECRETS: db-credentials
    SERVICE_ACCOUNT: backend-sa
"""
        result = investigator._discover_referenced_resources(deployment_data)

        assert sorted(result["configmaps"]) == ["backend-config", "frontend-config", "shared-config"]
        assert sorted(result["secrets"]) == ["db-credentials", "tls-cert"]
        assert result["service_accounts"] == ["backend-sa"]

    def test_parse_dedup_across_deployments(self, investigator):
        """Deduplicate resources referenced by multiple deployments."""
        deployment_data = """Found 2 deployment(s):

NAME: app-a
  REFERENCED_RESOURCES:
    CONFIGMAPS: shared-config
    SECRETS: shared-secret

NAME: app-b
  REFERENCED_RESOURCES:
    CONFIGMAPS: shared-config
    SECRETS: shared-secret
"""
        result = investigator._discover_referenced_resources(deployment_data)

        assert result["configmaps"] == ["shared-config"]
        assert result["secrets"] == ["shared-secret"]


class TestMergeDiscoveredResources:
    """Test merging discovered resources into affected_resources for parallel fetch."""

    @pytest.fixture
    def investigator(self):
        """Create K8sInvestigator with mocked tools."""
        mock_tools = MagicMock()
        mock_tools.kubectl_get = AsyncMock(return_value="mock resource data")
        mock_tools.kubectl_events = AsyncMock(return_value="mock events")
        mock_tools.kubectl_logs = AsyncMock(return_value="mock logs")
        mock_tools.ensure_healthy_session = AsyncMock(return_value=True)
        inv = _make_investigator(mock_tools)
        return inv

    @pytest.mark.asyncio
    async def test_merge_discovered_configmaps_into_affected(self, investigator):
        """Discovered configmaps from deployment spec merge into affected_resources."""
        state = {
            "namespace": "production",
            "affected_resources": {
                "deployments": ["notification-service"],
                "services": [],
                "configmaps": [],
                "secrets": [],
            },
        }

        # Mock deployment data with REFERENCED_RESOURCES
        deployment_data = """Found 1 deployment(s):

NAME: notification-service
  NAMESPACE: production
  REPLICAS: 2/2 (ready/desired)
  REFERENCED_RESOURCES:
    CONFIGMAPS: notification-config, app-settings
    SECRETS: db-credentials
"""
        investigator.tools.kubectl_get = AsyncMock(return_value=deployment_data)

        # Call the method that discovers and merges
        context = investigator._extract_investigation_context(state)
        findings = await investigator._perform_investigation(state, context)

        # The affected_resources should now have the discovered resources merged
        assert "notification-config" in state["affected_resources"]["configmaps"]
        assert "app-settings" in state["affected_resources"]["configmaps"]
        assert "db-credentials" in state["affected_resources"]["secrets"]

    @pytest.mark.asyncio
    async def test_merge_dedup_with_existing_affected(self, investigator):
        """Discovered resources don't duplicate already-known affected resources."""
        state = {
            "namespace": "production",
            "affected_resources": {
                "deployments": ["my-app"],
                "services": [],
                "configmaps": ["existing-config"],
                "secrets": [],
            },
        }

        deployment_data = """Found 1 deployment(s):

NAME: my-app
  NAMESPACE: production
  REPLICAS: 1/1 (ready/desired)
  REFERENCED_RESOURCES:
    CONFIGMAPS: existing-config, new-config
"""
        investigator.tools.kubectl_get = AsyncMock(return_value=deployment_data)

        context = investigator._extract_investigation_context(state)
        findings = await investigator._perform_investigation(state, context)

        # Should have both, but existing-config only once
        configmaps = state["affected_resources"]["configmaps"]
        assert configmaps.count("existing-config") == 1
        assert "new-config" in configmaps

    @pytest.mark.asyncio
    async def test_merge_handles_missing_affected_keys(self, investigator):
        """Handle affected_resources with no configmaps/secrets keys."""
        state = {
            "namespace": "production",
            "affected_resources": {
                "deployments": ["my-app"],
                "services": [],
                # No configmaps or secrets keys
            },
        }

        deployment_data = """Found 1 deployment(s):

NAME: my-app
  NAMESPACE: production
  REPLICAS: 1/1 (ready/desired)
  REFERENCED_RESOURCES:
    CONFIGMAPS: discovered-config
    SECRETS: discovered-secret
"""
        investigator.tools.kubectl_get = AsyncMock(return_value=deployment_data)

        context = investigator._extract_investigation_context(state)
        findings = await investigator._perform_investigation(state, context)

        assert "discovered-config" in state["affected_resources"]["configmaps"]
        assert "discovered-secret" in state["affected_resources"]["secrets"]

    @pytest.mark.asyncio
    async def test_no_referenced_resources_no_change(self, investigator):
        """When deployment has no REFERENCED_RESOURCES, affected_resources unchanged."""
        state = {
            "namespace": "production",
            "affected_resources": {
                "deployments": ["bare-app"],
                "services": [],
                "configmaps": [],
                "secrets": [],
            },
        }

        deployment_data = """Found 1 deployment(s):

NAME: bare-app
  NAMESPACE: production
  REPLICAS: 1/1 (ready/desired)
  AVAILABLE: 1
"""
        investigator.tools.kubectl_get = AsyncMock(return_value=deployment_data)

        context = investigator._extract_investigation_context(state)
        findings = await investigator._perform_investigation(state, context)

        # Should remain empty
        assert state["affected_resources"]["configmaps"] == []
        assert state["affected_resources"]["secrets"] == []
