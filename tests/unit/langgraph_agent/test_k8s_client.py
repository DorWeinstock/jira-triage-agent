"""Tests for K8sAsyncClientMixin."""

import asyncio
import inspect
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.k8s_client import K8sAsyncClientMixin


class TestOptionalAnyCleanup:
    """Verify Optional[Any] was cleaned up to Any."""

    def test_no_fstring_in_close_logger(self):
        """logger.debug must use %-style, not f-string."""
        source = inspect.getsource(K8sAsyncClientMixin.close)
        assert 'f"' not in source and "f'" not in source, (
            "close() must not use f-strings in logger calls"
        )


class TestReturnTypeAnnotation:
    """Verify _get_k8s_client has return type annotation."""

    def test_get_k8s_client_has_return_annotation(self):
        """_get_k8s_client must have a return type annotation."""
        fn = K8sAsyncClientMixin._get_k8s_client
        assert fn.__annotations__.get("return") is not None, (
            "_get_k8s_client must have a return type annotation"
        )


class TestTOCTOURaceFix:
    """Verify asyncio.Lock prevents concurrent init double-creation."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        __import__("importlib.util").util.find_spec("kubernetes_asyncio") is None,
        reason="kubernetes_asyncio not installed",
    )
    async def test_concurrent_init_creates_single_client(self):
        """Two concurrent callers must not double-initialize ApiClient."""
        init_count = 0

        async def fake_load_incluster():
            pass

        def fake_get_default_copy():
            nonlocal init_count
            init_count += 1
            cfg = MagicMock()
            cfg.proxy = None
            return cfg

        with patch(
            "kubernetes_asyncio.config.load_incluster_config",
            new=AsyncMock(side_effect=fake_load_incluster),
        ), patch(
            "kubernetes_asyncio.client.Configuration.get_default_copy",
            side_effect=fake_get_default_copy,
        ), patch(
            "kubernetes_asyncio.client.ApiClient", return_value=MagicMock()
        ), patch(
            "kubernetes_asyncio.client.CoreV1Api", return_value=MagicMock()
        ):

            class TestService(K8sAsyncClientMixin):
                pass

            svc = TestService()
            # Launch two concurrent init calls — both see _k8s_client is None initially
            await asyncio.gather(svc._get_k8s_client(), svc._get_k8s_client())

        assert init_count == 1, f"Expected 1 init, got {init_count}"

    @pytest.mark.asyncio
    async def test_has_asyncio_lock_attribute(self):
        """Mixin must have _k8s_lock attribute."""
        svc = K8sAsyncClientMixin()
        assert hasattr(svc, "_k8s_lock"), "Mixin must have _k8s_lock attribute"
        assert isinstance(svc._k8s_lock, asyncio.Lock), "_k8s_lock must be asyncio.Lock"


class TestAsyncContextManager:
    """Verify __aenter__/__aexit__ support."""

    @pytest.mark.asyncio
    async def test_async_context_manager_returns_self(self):
        """__aenter__ must return the mixin instance."""

        class TestService(K8sAsyncClientMixin):
            pass

        svc = TestService()
        async with svc as ctx:
            assert ctx is svc

    @pytest.mark.asyncio
    async def test_async_context_manager_calls_close(self):
        """__aexit__ must call close()."""

        class TestService(K8sAsyncClientMixin):
            pass

        svc = TestService()
        close_called = False
        original_close = svc.close

        async def mock_close():
            nonlocal close_called
            close_called = True

        svc.close = mock_close

        async with svc:
            pass

        assert close_called, "close() was not called on __aexit__"

    @pytest.mark.asyncio
    async def test_async_context_manager_closes_on_exception(self):
        """__aexit__ must call close() even if exception occurs."""

        class TestService(K8sAsyncClientMixin):
            pass

        svc = TestService()
        close_called = False

        async def mock_close():
            nonlocal close_called
            close_called = True

        svc.close = mock_close

        with pytest.raises(ValueError):
            async with svc:
                raise ValueError("Test error")

        assert close_called, "close() was not called when exception raised"


class TestClientLazyInit:
    """Verify client initialization behavior."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        __import__("importlib.util").util.find_spec("kubernetes_asyncio") is None,
        reason="kubernetes_asyncio not installed",
    )
    async def test_client_lazy_initialization(self):
        """Client should only initialize on first _get_k8s_client call."""

        class TestService(K8sAsyncClientMixin):
            pass

        svc = TestService()
        assert svc._k8s_client is None
        assert svc._api_client is None

        with patch("kubernetes_asyncio.config.load_incluster_config", new=AsyncMock()), patch(
            "kubernetes_asyncio.client.Configuration.get_default_copy"
        ) as mock_get_config, patch(
            "kubernetes_asyncio.client.ApiClient"
        ) as mock_api_client, patch(
            "kubernetes_asyncio.client.CoreV1Api"
        ) as mock_core_v1:

            await svc._get_k8s_client()

            # Verify initialization was called
            mock_get_config.assert_called_once()
            mock_api_client.assert_called_once()
            mock_core_v1.assert_called_once()
