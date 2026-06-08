"""Wrapper for Jenkins MCP server tools."""

import logging

from ..exceptions import ValidationError
from .base_mcp_client import BaseMCPClient

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONSOLE_BYTES = 100000


class JenkinsTools(BaseMCPClient):
    """Client for interacting with the Jenkins MCP server."""

    def _validate_url(self, url: str) -> None:
        if not url or not url.strip():
            raise ValidationError(
                "Jenkins URL cannot be empty",
                field="jenkins_url",
                value=url,
                agent_name=self.client_name,
            )

    async def get_build_info(self, jenkins_url: str) -> str:
        """Fetch build metadata for a Jenkins build URL."""
        self._validate_url(jenkins_url)
        return await self.call_tool(
            "jenkins_get_build_info",
            {"jenkins_url": jenkins_url},
        )

    async def get_console_log(
        self, jenkins_url: str, max_bytes: int = DEFAULT_MAX_CONSOLE_BYTES
    ) -> str:
        """Fetch truncated console log for a Jenkins build URL."""
        self._validate_url(jenkins_url)
        return await self.call_tool(
            "jenkins_get_console_log",
            {"jenkins_url": jenkins_url, "max_bytes": max_bytes},
        )

    async def get_parent_build_info(self, jenkins_url: str) -> str:
        """Fetch upstream/parent trigger info for a Jenkins build URL."""
        self._validate_url(jenkins_url)
        return await self.call_tool(
            "jenkins_get_parent_build_info",
            {"jenkins_url": jenkins_url},
        )
