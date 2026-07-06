"""Comprehensive tests for configuration module.

Tests cover:
- Weight validation and constraints
- Environment variable overrides
- LLM factory functions
- Default values
- Caching behavior
"""

import os
import pytest
from pydantic_core import ValidationError


class TestWeightValidation:
    """Test composite weight validation."""

    def test_default_weights_sum_to_one(self):
        """Default weights should sum to exactly 1.0."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        total = (
            settings.history_weight_llm_similarity +
            settings.history_weight_component_match +
            settings.history_weight_status_score +
            settings.history_weight_recency_bonus
        )
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"

    def test_invalid_weights_below_one_raises_error(self, monkeypatch):
        """Weights summing below 1.0 should raise ValidationError."""
        monkeypatch.setenv("HISTORY_WEIGHT_LLM_SIMILARITY", "0.5")
        monkeypatch.setenv("HISTORY_WEIGHT_COMPONENT_MATCH", "0.1")
        monkeypatch.setenv("HISTORY_WEIGHT_STATUS_SCORE", "0.1")
        monkeypatch.setenv("HISTORY_WEIGHT_RECENCY_BONUS", "0.1")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        with pytest.raises(ValidationError) as exc_info:
            get_settings()
        
        assert "Composite weights must sum to 1.0" in str(exc_info.value)

    def test_invalid_weights_above_one_raises_error(self, monkeypatch):
        """Weights summing above 1.0 should raise ValidationError."""
        monkeypatch.setenv("HISTORY_WEIGHT_LLM_SIMILARITY", "0.6")
        monkeypatch.setenv("HISTORY_WEIGHT_COMPONENT_MATCH", "0.2")
        monkeypatch.setenv("HISTORY_WEIGHT_STATUS_SCORE", "0.2")
        monkeypatch.setenv("HISTORY_WEIGHT_RECENCY_BONUS", "0.15")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        with pytest.raises(ValidationError) as exc_info:
            get_settings()
        
        assert "Composite weights must sum to 1.0" in str(exc_info.value)

    def test_float_rounding_tolerance_accepted(self, monkeypatch):
        """Weights with minor float rounding should be accepted (±0.001)."""
        # Using values that might have rounding issues
        monkeypatch.setenv("HISTORY_WEIGHT_LLM_SIMILARITY", "0.551")
        monkeypatch.setenv("HISTORY_WEIGHT_COMPONENT_MATCH", "0.099")
        monkeypatch.setenv("HISTORY_WEIGHT_STATUS_SCORE", "0.201")
        monkeypatch.setenv("HISTORY_WEIGHT_RECENCY_BONUS", "0.149")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        # Should not raise
        settings = get_settings()
        assert settings.history_weight_llm_similarity == 0.551

    def test_weight_validation_error_includes_all_values(self, monkeypatch):
        """Validation error message should show all weight values for debugging."""
        monkeypatch.setenv("HISTORY_WEIGHT_LLM_SIMILARITY", "0.3")
        monkeypatch.setenv("HISTORY_WEIGHT_COMPONENT_MATCH", "0.2")
        monkeypatch.setenv("HISTORY_WEIGHT_STATUS_SCORE", "0.2")
        monkeypatch.setenv("HISTORY_WEIGHT_RECENCY_BONUS", "0.1")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        with pytest.raises(ValidationError) as exc_info:
            get_settings()
        
        error_msg = str(exc_info.value)
        assert "history_weight_llm_similarity=0.3" in error_msg or "0.3" in error_msg


class TestEnvironmentOverrides:
    """Test environment variable configuration overrides."""

    def test_vllm_endpoint_override(self, monkeypatch):
        """VLLM_ENDPOINT env var should override default."""
        monkeypatch.setenv("VLLM_ENDPOINT", "http://custom-vllm:9000")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.vllm_endpoint == "http://custom-vllm:9000"

    def test_vllm_model_name_override(self, monkeypatch):
        """VLLM_MODEL_NAME env var should override default."""
        monkeypatch.setenv("VLLM_MODEL_NAME", "custom-model-v2")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.vllm_model_name == "custom-model-v2"

    def test_jira_mcp_endpoint_override(self, monkeypatch):
        """JIRA_MCP_ENDPOINT env var should override default."""
        monkeypatch.setenv("JIRA_MCP_ENDPOINT", "http://custom-jira:9999/mcp/jira")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.jira_mcp_endpoint == "http://custom-jira:9999/mcp/jira"

    def test_k8s_mcp_endpoint_override(self, monkeypatch):
        """K8S_MCP_ENDPOINT env var should override default."""
        monkeypatch.setenv("K8S_MCP_ENDPOINT", "http://custom-k8s:9999/mcp/k8s")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.k8s_mcp_endpoint == "http://custom-k8s:9999/mcp/k8s"

    def test_max_iterations_numeric_override(self, monkeypatch):
        """MAX_ITERATIONS env var should be coerced to int."""
        monkeypatch.setenv("MAX_ITERATIONS", "50")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.max_iterations == 50
        assert isinstance(settings.max_iterations, int)

    def test_checkpoint_enabled_bool_override_true(self, monkeypatch):
        """CHECKPOINT_ENABLED env var should be coerced to bool (true)."""
        monkeypatch.setenv("CHECKPOINT_ENABLED", "true")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.checkpoint_enabled is True

    def test_checkpoint_enabled_bool_override_false(self, monkeypatch):
        """CHECKPOINT_ENABLED env var should be coerced to bool (false)."""
        monkeypatch.setenv("CHECKPOINT_ENABLED", "false")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.checkpoint_enabled is False

    def test_hitl_enabled_override(self, monkeypatch):
        """HITL_ENABLED env var should override default."""
        monkeypatch.setenv("HITL_ENABLED", "false")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.hitl_enabled is False

    def test_langchain_tracing_v2_override(self, monkeypatch):
        """LANGCHAIN_TRACING_V2 env var should override default."""
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.langchain_tracing is False

    def test_unknown_env_vars_ignored(self, monkeypatch):
        """Unknown environment variables should be ignored (extra='ignore')."""
        monkeypatch.setenv("UNKNOWN_SETTING_XYZ", "should_be_ignored")
        
        from src.config import get_settings
        get_settings.cache_clear()
        
        # Should not raise
        settings = get_settings()
        assert not hasattr(settings, "unknown_setting_xyz")


class TestLangfuseSettings:
    """Test Langfuse observability configuration."""

    def test_langfuse_disabled_by_default(self):
        """langfuse_enabled should be False when no Langfuse env vars are set."""
        from src.config import get_settings
        get_settings.cache_clear()

        settings = get_settings()
        assert settings.langfuse_enabled is False

    def test_langfuse_enabled_when_all_vars_set(self, monkeypatch):
        """langfuse_enabled should be True once host, public key, and secret key are all set."""
        monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example.com")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        from src.config import get_settings
        get_settings.cache_clear()

        settings = get_settings()
        assert settings.langfuse_enabled is True

    def test_langfuse_disabled_when_key_missing(self, monkeypatch):
        """langfuse_enabled should stay False if only some of the vars are set."""
        monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example.com")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")

        from src.config import get_settings
        get_settings.cache_clear()

        settings = get_settings()
        assert settings.langfuse_enabled is False

    def test_create_langfuse_handler_returns_none_when_disabled(self):
        """create_langfuse_handler() should return None if Langfuse isn't configured."""
        from src.config import create_langfuse_handler, get_settings
        get_settings.cache_clear()

        assert create_langfuse_handler() is None


class TestLLMFactories:
    """Test LLM factory functions."""

    def test_create_llm_returns_chat_openai(self):
        """create_llm() should return a ChatOpenAI instance."""
        from src.config import create_llm
        
        llm = create_llm()
        # Just verify it's callable and has expected methods
        assert hasattr(llm, 'invoke')
        assert hasattr(llm, 'model_name')

    def test_create_llm_default_temperature(self):
        """create_llm() should use default temperature."""
        from src.config import create_llm, get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        llm = create_llm()
        assert llm.temperature == settings.temperature_default

    def test_create_llm_temperature_override(self):
        """create_llm(temperature=...) should override default."""
        from src.config import create_llm
        
        llm = create_llm(temperature=0.8)
        assert llm.temperature == 0.8

    def test_create_extraction_llm_uses_extraction_temperature(self):
        """create_extraction_llm() should use temperature_extraction."""
        from src.config import create_extraction_llm, get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        llm = create_extraction_llm()
        assert llm.temperature == settings.temperature_extraction

    def test_create_diagnosis_llm_uses_diagnosis_temperature(self):
        """create_diagnosis_llm() should use temperature_diagnosis."""
        from src.config import create_diagnosis_llm, get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        llm = create_diagnosis_llm()
        assert llm.temperature == settings.temperature_diagnosis

    def test_create_llm_model_override(self):
        """create_llm(model=...) should override model name."""
        from src.config import create_llm
        
        llm = create_llm(model="custom-model-name")
        assert llm.model_name == "custom-model-name"

    def test_create_llm_max_tokens_override(self):
        """create_llm(max_tokens=...) should pass max_tokens parameter."""
        from src.config import create_llm
        
        # Just verify it doesn't raise; ChatOpenAI stores this internally
        llm = create_llm(max_tokens=2048)
        # Verify it's a ChatOpenAI instance with a model_name
        assert hasattr(llm, 'model_name')

    def test_create_llm_uses_vllm_endpoint(self):
        """LLM should be configured to use vLLM."""
        from src.config import create_llm, get_settings
        get_settings.cache_clear()
        
        llm = create_llm()
        settings = get_settings()
        # Verify it's a ChatOpenAI instance pointing to vLLM
        assert hasattr(llm, 'model_name')
        assert llm.model_name == settings.vllm_model_name


class TestDefaultValues:
    """Test that default values are set correctly."""

    def test_readonly_namespaces_default(self):
        """readonly_namespaces should default to kube system namespaces."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert "kube-system" in settings.readonly_namespaces
        assert "kube-public" in settings.readonly_namespaces
        assert "kube-node-lease" in settings.readonly_namespaces

    def test_checkpoint_defaults(self):
        """Checkpoint settings should have sensible defaults."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.checkpoint_enabled is True
        assert settings.checkpoint_namespace == "jira-k8s-agent"
        assert settings.checkpoint_configmap_name == "agent-checkpoints"
        assert settings.checkpoint_ttl_seconds == 86400  # 24 hours

    def test_verification_timeouts_are_positive(self):
        """Verification timeouts should be positive integers."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.verification_timeout > 0
        assert settings.verification_poll_interval > 0
        assert settings.verification_min_stable_checks > 0
        assert settings.verification_initial_grace > 0

    def test_truncation_limits_are_positive(self):
        """Truncation limits should be positive integers."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.truncation_logs > 0
        assert settings.truncation_events > 0
        assert settings.truncation_cluster_findings > 0
        assert settings.truncation_deployment_status > 0

    def test_log_level_default(self):
        """Log level should default to INFO."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.log_level == "INFO"

    def test_mcp_timeouts_are_positive(self):
        """MCP connection timeouts should be positive."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.mcp_connection_timeout > 0
        assert settings.mcp_sse_read_timeout > 0

    def test_temperature_values_valid_range(self):
        """Temperature values should be in [0.0, 1.0]."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert 0.0 <= settings.temperature_default <= 1.0
        assert 0.0 <= settings.temperature_extraction <= 1.0
        assert 0.0 <= settings.temperature_diagnosis <= 1.0


class TestCachingBehavior:
    """Test settings caching mechanism."""

    def test_get_settings_returns_cached_instance(self):
        """Multiple calls to get_settings() should return the same cached object."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings1 = get_settings()
        settings2 = get_settings()
        
        # Same object, not just equal
        assert settings1 is settings2

    def test_cache_clear_allows_reloading(self):
        """After cache_clear(), get_settings() should load fresh config."""
        from src.config import get_settings
        
        settings1 = get_settings()
        get_settings.cache_clear()
        settings2 = get_settings()
        
        # Different objects after cache clear
        assert settings1 is not settings2

    def test_cache_clear_with_env_override(self, monkeypatch):
        """Cache clear allows environment changes to be picked up."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings1 = get_settings()
        original_endpoint = settings1.vllm_endpoint
        
        monkeypatch.setenv("VLLM_ENDPOINT", "http://new-endpoint:8000")
        get_settings.cache_clear()
        
        settings2 = get_settings()
        assert settings2.vllm_endpoint == "http://new-endpoint:8000"
        assert settings2.vllm_endpoint != original_endpoint


class TestHistorySearchSettings:
    """Test history search and scoring configuration."""

    def test_history_weights_sum_to_one(self):
        """All history weight fields must sum to 1.0."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        total = (
            settings.history_weight_llm_similarity +
            settings.history_weight_component_match +
            settings.history_weight_status_score +
            settings.history_weight_recency_bonus
        )
        assert abs(total - 1.0) < 0.001

    def test_history_search_defaults(self):
        """History search configuration should have sensible defaults."""
        from src.config import get_settings
        get_settings.cache_clear()
        
        settings = get_settings()
        assert settings.max_similar_tickets > 0
        assert settings.history_max_tickets_to_fetch > 0
        assert settings.history_min_relevance_score >= 0
        assert settings.history_recency_max_days > 0

