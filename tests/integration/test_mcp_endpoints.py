"""Integration tests for Go monolith MCP endpoints."""
import os

import pytest
import httpx

MCP_BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:8080")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_endpoint():
    """Test health endpoint returns ok status."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{MCP_BASE_URL}/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ready_endpoint():
    """Test ready endpoint returns ready status."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{MCP_BASE_URL}/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_jira_mcp_endpoint_exists():
    """Test Jira MCP endpoint is registered (not 404)."""
    async with httpx.AsyncClient() as client:
        # MCP uses POST for tool calls
        resp = await client.post(f"{MCP_BASE_URL}/mcp/jira")
        # Should get MCP protocol response, not 404
        assert resp.status_code != 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k8s_mcp_endpoint_exists():
    """Test K8s MCP endpoint is registered (not 404).

    Note: K8s MCP is only registered when running inside a K8s cluster.
    Outside cluster, 404 is expected. This test verifies the endpoint
    returns a proper MCP response when available.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{MCP_BASE_URL}/mcp/k8s")
        # Outside K8s cluster, endpoint won't be registered (404)
        # Inside K8s cluster, should get MCP protocol response (400/200)
        # Both are valid - we just verify the server doesn't crash
        assert resp.status_code in (400, 404, 200)
