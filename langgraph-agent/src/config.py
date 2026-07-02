"""Configuration for the triage agent."""

import logging
from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # LLM (vLLM OpenAI-compatible endpoint)
    vllm_endpoint: str = Field(
        default="http://vllm-service:8000/v1",
        alias="VLLM_ENDPOINT",
    )
    vllm_model_name: str = Field(
        default="",
        alias="VLLM_MODEL_NAME",
    )
    temperature_default: float = 0.3
    temperature_extraction: float = 0.1
    max_tokens: int = 2048

    # Jira MCP endpoint (Go monolith)
    jira_mcp_endpoint: str = Field(
        default="http://jira-agent:8080/mcp/jira",
        alias="JIRA_MCP_ENDPOINT",
    )
    mcp_connection_timeout: int = Field(default=30, alias="MCP_CONNECTION_TIMEOUT")
    mcp_sse_read_timeout: int = Field(default=300, alias="MCP_SSE_READ_TIMEOUT")

    # Triage config — stored as raw CSV string, exposed as list via computed_field
    team_members_raw: str = Field(default="", alias="TEAM_MEMBERS")

    @computed_field
    @property
    def team_members(self) -> list[str]:
        return [m.strip() for m in self.team_members_raw.split(",") if m.strip()]
    processed_label: str = Field(
        default="triage-agent-done",
        alias="PROCESSED_LABEL",
    )
    # Must match pkg/poller/poller.go's LabelInvestigationInProgress constant —
    # stamped by the Go poller before dispatch, cleared here once triage completes.
    in_progress_label: str = Field(
        default="triage-in-progress",
        alias="IN_PROGRESS_LABEL",
    )

    # Rate limiting
    triage_rate_limit: str = Field(default="20/minute", alias="TRIAGE_RATE_LIMIT")

    # Observability (optional — disable by not setting LANGCHAIN_API_KEY)
    langchain_tracing: bool = Field(default=False, alias="LANGCHAIN_TRACING_V2")
    langchain_project: str = Field(default="jira-triage-agent", alias="LANGCHAIN_PROJECT")
    langchain_api_key: str = Field(default="", alias="LANGCHAIN_API_KEY")

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def create_llm(temperature: float | None = None):
    """Create a ChatOpenAI instance pointed at the vLLM server."""
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    temp = temperature if temperature is not None else settings.temperature_default

    logger.info(
        "Creating LLM: model=%s endpoint=%s temperature=%.2f",
        settings.vllm_model_name,
        settings.vllm_endpoint,
        temp,
    )

    return ChatOpenAI(
        base_url=settings.vllm_endpoint,
        api_key="not-needed",  # pragma: allowlist secret
        model=settings.vllm_model_name,
        temperature=temp,
        max_tokens=settings.max_tokens,
        max_completion_tokens=None,
        # Disable Qwen3 chain-of-thought. The prompt-level `/no_think` directive is
        # IGNORED by Qwen3.6 on the vLLM-Gaudi build (verified: it still emits a
        # <think> block). The working switch is chat_template_kwargs in the request
        # body, which yields a clean answer (e.g. just "ACTIONABLE", 3 tokens).
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )


def create_extraction_llm():
    settings = get_settings()
    return create_llm(temperature=settings.temperature_extraction)
