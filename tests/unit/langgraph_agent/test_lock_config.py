"""Unit tests for lock service configuration."""

import pytest


class TestLockSettings:
    """Test lock configuration settings."""

    def test_default_lock_settings(self, monkeypatch):
        """Test default lock configuration values."""
        monkeypatch.delenv("LOCK_ENABLED", raising=False)
        monkeypatch.delenv("LOCK_TTL_SECONDS", raising=False)
        monkeypatch.delenv("LOCK_NAMESPACE", raising=False)
        monkeypatch.delenv("LOCK_CONFIGMAP_NAME", raising=False)

        from src.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()

        assert settings.lock.enabled is True
        assert settings.lock.namespace == "jira-k8s-agent"
        assert settings.lock.ttl_seconds == 1800
        assert settings.lock.configmap_name == "langgraph-remediation-locks"

    def test_lock_disabled_via_env(self, monkeypatch):
        """Test lock can be disabled via environment variable."""
        monkeypatch.setenv("LOCK_ENABLED", "false")

        from src.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()

        assert settings.lock.enabled is False

    def test_lock_ttl_from_env(self, monkeypatch):
        """Test lock TTL can be configured via environment variable."""
        monkeypatch.setenv("LOCK_TTL_SECONDS", "3600")

        from src.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()

        assert settings.lock.ttl_seconds == 3600
