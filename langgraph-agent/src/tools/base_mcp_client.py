"""Simplified MCP client using Streamable HTTP transport.

The Go MCP server uses Streamable HTTP (mcp.NewStreamableHTTPHandler),
so we use streamablehttp_client to match.

Uses per-call connections to avoid anyio cross-task cancel scope issues
that occur when AsyncExitStack persists connections across asyncio tasks.
"""

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable

from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
from httpx import ConnectError, NetworkError, RemoteProtocolError
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from ..config import get_settings
from ..exceptions import ToolError


logger = logging.getLogger(__name__)


_RETRYABLE_ERRORS = (
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    ConnectError,
    NetworkError,
    RemoteProtocolError,
)


class BaseMCPClient:
    """Simple MCP client using per-call Streamable HTTP connections."""

    def __init__(self, endpoint: str, timeout: int = 30):
        self.endpoint = endpoint
        self.timeout = timeout
        self.client_name = self.__class__.__name__
        self._settings = get_settings()

    def _backoff_delay(self, attempt: int) -> float:
        """Compute exponential backoff with jitter.

        Args:
            attempt: 1-based attempt number; delay applied before the next attempt.

        Returns:
            Seconds to sleep before retrying.
        """
        return (2 ** attempt) * 0.5 + random.random()

    async def _retry_with_backoff(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        max_retries: int,
        label: str,
    ) -> Any:
        """Run coro_factory() with retry on transient errors.

        Args:
            coro_factory: Zero-arg callable returning a coroutine to execute.
            max_retries: Maximum number of attempts (including the first).
            label: Human-readable label for log messages (e.g., tool name or endpoint).

        Returns:
            Result from coro_factory on success.

        Raises:
            ToolError: After all retries are exhausted or on non-retryable error.
        """
        for attempt in range(1, max_retries + 1):
            try:
                return await coro_factory()

            except _RETRYABLE_ERRORS as e:
                if attempt < max_retries:
                    logger.warning(
                        "Transient error for '%s' (attempt %d/%d): %s",
                        label, attempt, max_retries, e,
                    )
                    await asyncio.sleep(self._backoff_delay(attempt))
                    continue
                raise ToolError("'%s' failed after %d attempts: %s" % (label, max_retries, e)) from e

            except ExceptionGroup as e:
                all_retryable = all(isinstance(sub, _RETRYABLE_ERRORS) for sub in e.exceptions)
                if all_retryable and attempt < max_retries:
                    logger.warning(
                        "Retryable ExceptionGroup for '%s' (attempt %d/%d): %s",
                        label, attempt, max_retries, e,
                    )
                    await asyncio.sleep(self._backoff_delay(attempt))
                    continue
                raise ToolError("'%s' failed: %s" % (label, e)) from e

            except Exception as e:
                raise ToolError("'%s' unexpected error: %s" % (label, e)) from e

    async def _call_tool_with_session(self, tool_name: str, arguments: dict) -> Any:
        """Create a fresh connection, call the tool, and clean up."""
        async with streamablehttp_client(
            url=self.endpoint,
            timeout=self._settings.mcp_connection_timeout,
            sse_read_timeout=self._settings.mcp_sse_read_timeout,
            terminate_on_close=False,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                if getattr(result, 'isError', False):
                    error_text = self._extract_content(result)
                    raise ToolError(f"Tool '{tool_name}' returned error: {error_text}")
                return self._extract_content(result)

    def _extract_content(self, result: Any) -> Any:
        """Extract content from MCP tool result."""
        if hasattr(result, 'content') and result.content:
            if isinstance(result.content, list) and len(result.content) > 0:
                if len(result.content) > 1:
                    logger.warning(
                        "_extract_content: received %d content items; returning only the first. "
                        "Dropped: %r",
                        len(result.content),
                        result.content[1:],
                    )
                return result.content[0].text if hasattr(result.content[0], 'text') else result.content[0]
            return result.content
        return result

    async def call_tool(self, tool_name: str, arguments: dict, max_retries: int = 2) -> Any:
        """Call MCP tool with timeout and retry on connection errors.

        Creates a fresh connection per call to avoid cross-task issues
        with anyio cancel scopes.
        
        Args:
            tool_name: Name of the MCP tool to invoke.
            arguments: Tool arguments as a dictionary.
            max_retries: Maximum number of retry attempts for connection errors (default: 2).
        
        Returns:
            Result from the MCP tool.
        
        Raises:
            ValueError: If tool_name is empty or whitespace-only.
            ToolError: If the tool fails or all retries are exhausted.
        """
        if not tool_name or not tool_name.strip():
            raise ValueError("tool_name must not be empty")

        async def _invoke() -> Any:
            try:
                return await asyncio.wait_for(
                    self._call_tool_with_session(tool_name, arguments),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                raise ToolError("Tool '%s' timed out after %ds" % (tool_name, self.timeout)) from None

        return await self._retry_with_backoff(_invoke, max_retries, tool_name)

    async def connect(self, max_retries: int = 3) -> None:
        """Verify connectivity to the MCP server."""
        async def _attempt() -> None:
            async with streamablehttp_client(
                url=self.endpoint,
                timeout=self._settings.mcp_connection_timeout,
                sse_read_timeout=self._settings.mcp_sse_read_timeout,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    logger.info("Connected to MCP server: %s", self.endpoint)

        await self._retry_with_backoff(_attempt, max_retries, self.endpoint)

    async def ensure_healthy_session(self) -> bool:
        """Verify MCP server is reachable."""
        try:
            await self.connect(max_retries=1)
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """No-op: connections are per-call, no persistent state to clean up."""
        pass

    async def __aenter__(self) -> "BaseMCPClient":
        """Async context manager entry: return self."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Async context manager exit: clean up resources."""
        await self.close()
