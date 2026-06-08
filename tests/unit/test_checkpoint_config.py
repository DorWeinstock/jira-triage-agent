"""Tests for checkpoint configuration settings."""

import os
import pytest


class TestCheckpointSettings:
    """Test checkpoint configuration loading with flat settings structure."""

    def test_checkpoint_enabled_defaults_to_true(self, monkeypatch):
        """Checkpoint should be enabled by default."""
        monkeypatch.delenv("CHECKPOINT_ENABLED", raising=False)

        # Force reload of settings
        from src.config import get_settings
        get_settings.cache_clear()

        # Test via flat settings structure
        settings = get_settings()
        assert settings.checkpoint_enabled is True

    def test_checkpoint_enabled_can_be_disabled(self, monkeypatch):
        """Checkpoint can be disabled via env var."""
        monkeypatch.setenv("CHECKPOINT_ENABLED", "false")

        from src.config import get_settings
        get_settings.cache_clear()

        # Test via flat settings structure
        settings = get_settings()
        assert settings.checkpoint_enabled is False

    def test_checkpoint_namespace_defaults_to_same_namespace(self, monkeypatch):
        """Checkpoint namespace should default to agent's namespace."""
        monkeypatch.delenv("CHECKPOINT_NAMESPACE", raising=False)

        from src.config import get_settings
        get_settings.cache_clear()

        # Test via flat settings structure
        settings = get_settings()
        assert settings.checkpoint_namespace == "jira-k8s-agent"

    def test_checkpoint_namespace_can_be_overridden(self, monkeypatch):
        """Checkpoint namespace can be set via env var."""
        monkeypatch.setenv("CHECKPOINT_NAMESPACE", "custom-ns")

        from src.config import get_settings
        get_settings.cache_clear()

        # Test via flat settings structure
        settings = get_settings()
        assert settings.checkpoint_namespace == "custom-ns"

    def test_checkpoint_configmap_name_has_default(self, monkeypatch):
        """Checkpoint configmap name should have a default."""
        monkeypatch.delenv("CHECKPOINT_CONFIGMAP_NAME", raising=False)

        from src.config import get_settings
        get_settings.cache_clear()

        # Test via flat settings structure
        settings = get_settings()
        assert settings.checkpoint_configmap_name == "agent-checkpoints"
