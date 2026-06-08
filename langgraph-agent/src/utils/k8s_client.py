"""Shared Kubernetes async client utilities.

This module provides a mixin class for managing kubernetes-asyncio client
lifecycle, including proper initialization with proxy bypass and cleanup.
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class K8sAsyncClientMixin:
    """Mixin for classes that need kubernetes-asyncio client access.

    Provides:
    - Lazy client initialization with in-cluster/kubeconfig fallback
    - Proxy bypass for corporate environments
    - Proper cleanup via close() method

    Usage:
        class MyService(K8sAsyncClientMixin):
            def __init__(self):
                super().__init__()  # Initialize mixin attributes
                # ... your init code

            async def do_something(self):
                client = await self._get_k8s_client()
                await client.list_namespaced_config_map(...)

            # Don't forget to call close() when done!
    """

    def __init__(self) -> None:
        """Initialize instance-level client attributes."""
        self._k8s_client: Any = None
        self._api_client: Any = None
        self._k8s_lock = asyncio.Lock()

    async def _get_k8s_client(self) -> Any:
        """Get or create the K8s CoreV1Api client.

        Returns:
            Kubernetes CoreV1Api client instance
        """
        if self._k8s_client is not None:
            return self._k8s_client

        async with self._k8s_lock:
            # Double-checked locking: re-check inside lock
            if self._k8s_client is None:
                from kubernetes_asyncio import client, config

                try:
                    # Try in-cluster config first (running in K8s)
                    config.load_incluster_config()
                except config.ConfigException:
                    # Fall back to kubeconfig (local development)
                    await config.load_kube_config()

                # Disable proxy for in-cluster K8s API access
                # This prevents corporate proxies from intercepting internal cluster traffic
                configuration = client.Configuration.get_default_copy()
                configuration.proxy = None
                self._api_client = client.ApiClient(configuration)
                self._k8s_client = client.CoreV1Api(self._api_client)

        return self._k8s_client

    async def close(self) -> None:
        """Close the K8s API client and release resources.

        This should be called when the service is no longer needed to
        prevent 'Unclosed client session' warnings from aiohttp.
        """
        if self._api_client is not None:
            await self._api_client.close()
            self._api_client = None
            self._k8s_client = None
            logger.debug("Closed K8s API client for %s", self.__class__.__name__)

    async def __aenter__(self) -> "K8sAsyncClientMixin":
        """Support `async with` usage for guaranteed cleanup."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Close the K8s API client on context exit."""
        await self.close()
