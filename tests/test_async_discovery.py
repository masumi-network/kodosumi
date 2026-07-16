"""
Tests for async flow discovery (#84).

Verifies that cold-start apps (OpenAPI not immediately available)
get their flow metadata populated via background retry.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from kodosumi.service.expose.boot import (
    _async_flow_discovery,
    DiscoveredFlow,
    FlowStatus,
    BootProgress,
    BOOT_ASYNC_DISCOVERY_TIMEOUT,
    BOOT_ASYNC_DISCOVERY_INTERVAL,
)


def _make_flow(app_name: str, path: str = None) -> DiscoveredFlow:
    return DiscoveredFlow(
        app_name=app_name,
        path=path or f"/{app_name}/",
        method="POST",
        summary="test",
        description="",
        tags=["test"],
    )


def _make_spec(app_name: str) -> dict:
    """Minimal OpenAPI spec with x-kodosumi marker."""
    return {
        "paths": {
            f"/{app_name}/": {
                "post": {
                    "summary": "test",
                    "description": "",
                    "tags": ["test"],
                    "x-openapi-extra": {"x-kodosumi": True},
                }
            }
        }
    }


class TestAsyncFlowDiscovery:

    @pytest.mark.asyncio
    async def test_resolves_app_on_second_retry(self):
        """App's OpenAPI fails first, succeeds on retry → meta updated."""
        call_count = 0

        async def mock_fetch(addr, name):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return (None, f"http://x/{name}/openapi.json", "Timeout")
            return (_make_spec(name), f"http://x/{name}/openapi.json", None)

        with patch("kodosumi.service.expose.boot.fetch_openapi_spec",
                    side_effect=mock_fetch), \
             patch("kodosumi.service.expose.boot.extract_kodosumi_endpoints",
                    return_value=[_make_flow("cold-app")]), \
             patch("kodosumi.service.expose.boot.check_flow_health",
                    new_callable=AsyncMock, return_value=("alive", 200)), \
             patch("kodosumi.service.expose.boot._step_update_meta") as mock_meta, \
             patch("kodosumi.service.expose.boot.BOOT_ASYNC_DISCOVERY_INTERVAL", 0.01), \
             patch("kodosumi.service.expose.boot.BOOT_ASYNC_DISCOVERY_TIMEOUT", 5):

            async def consume_meta(*args, **kwargs):
                return
                yield  # make it an async generator
            mock_meta.side_effect = consume_meta

            await _async_flow_discovery(
                ["cold-app"], "http://serve:8005", "http://dash:8265",
                "http://app:3370", None,
            )

        assert call_count == 2
        mock_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_logs_warning(self, caplog):
        """App never becomes available → timeout + WARNING log."""
        async def mock_fetch_never(addr, name):
            return (None, f"http://x/{name}/openapi.json", "Timeout")

        with patch("kodosumi.service.expose.boot.fetch_openapi_spec",
                    side_effect=mock_fetch_never), \
             patch("kodosumi.service.expose.boot.BOOT_ASYNC_DISCOVERY_INTERVAL", 0.01), \
             patch("kodosumi.service.expose.boot.BOOT_ASYNC_DISCOVERY_TIMEOUT", 0.05):

            with caplog.at_level(logging.WARNING):
                await _async_flow_discovery(
                    ["stuck-app"], "http://serve:8005", "",
                    "http://app:3370", None,
                )

        timeout_logs = [r for r in caplog.records
                        if r.msg == "boot.async_discovery.timeout"]
        assert len(timeout_logs) == 1
        assert "stuck-app" in timeout_logs[0]._slog["unresolved"]

    @pytest.mark.asyncio
    async def test_no_duplicate_meta_on_already_resolved(self):
        """App discovered on first try → resolved immediately, no retry."""
        async def mock_fetch_ok(addr, name):
            return (_make_spec(name), f"http://x/{name}/openapi.json", None)

        meta_calls = 0

        async def mock_meta(*args, **kwargs):
            nonlocal meta_calls
            meta_calls += 1
            return
            yield

        with patch("kodosumi.service.expose.boot.fetch_openapi_spec",
                    side_effect=mock_fetch_ok), \
             patch("kodosumi.service.expose.boot.extract_kodosumi_endpoints",
                    return_value=[_make_flow("app-a")]), \
             patch("kodosumi.service.expose.boot.check_flow_health",
                    new_callable=AsyncMock, return_value=("alive", 200)), \
             patch("kodosumi.service.expose.boot._step_update_meta",
                    side_effect=mock_meta), \
             patch("kodosumi.service.expose.boot.BOOT_ASYNC_DISCOVERY_INTERVAL", 0.01), \
             patch("kodosumi.service.expose.boot.BOOT_ASYNC_DISCOVERY_TIMEOUT", 1):

            await _async_flow_discovery(
                ["app-a"], "http://serve:8005", "",
                "http://app:3370", None,
            )

        assert meta_calls == 1

    @pytest.mark.asyncio
    async def test_multiple_apps_partial_resolution(self):
        """Two apps: one resolves, other times out."""
        async def mock_fetch(addr, name):
            if name == "warm-app":
                return (_make_spec(name), f"http://x/{name}/openapi.json", None)
            return (None, f"http://x/{name}/openapi.json", "Timeout")

        resolved_apps = []

        async def mock_meta(app_server, cookies, flow_statuses, progress):
            for app_name in flow_statuses:
                resolved_apps.append(app_name)
            return
            yield

        with patch("kodosumi.service.expose.boot.fetch_openapi_spec",
                    side_effect=mock_fetch), \
             patch("kodosumi.service.expose.boot.extract_kodosumi_endpoints",
                    return_value=[_make_flow("warm-app")]), \
             patch("kodosumi.service.expose.boot.check_flow_health",
                    new_callable=AsyncMock, return_value=("alive", 200)), \
             patch("kodosumi.service.expose.boot._step_update_meta",
                    side_effect=mock_meta), \
             patch("kodosumi.service.expose.boot.BOOT_ASYNC_DISCOVERY_INTERVAL", 0.01), \
             patch("kodosumi.service.expose.boot.BOOT_ASYNC_DISCOVERY_TIMEOUT", 0.05):

            await _async_flow_discovery(
                ["warm-app", "cold-app"], "http://serve:8005", "",
                "http://app:3370", None,
            )

        assert "warm-app" in resolved_apps
        assert "cold-app" not in resolved_apps

    @pytest.mark.asyncio
    async def test_app_with_no_kodosumi_endpoints_resolved(self):
        """App returns OpenAPI but no x-kodosumi endpoints → resolved without meta."""
        async def mock_fetch(addr, name):
            return ({"paths": {}}, f"http://x/{name}/openapi.json", None)

        with patch("kodosumi.service.expose.boot.fetch_openapi_spec",
                    side_effect=mock_fetch), \
             patch("kodosumi.service.expose.boot.extract_kodosumi_endpoints",
                    return_value=[]), \
             patch("kodosumi.service.expose.boot._step_update_meta") as mock_meta, \
             patch("kodosumi.service.expose.boot.BOOT_ASYNC_DISCOVERY_INTERVAL", 0.01), \
             patch("kodosumi.service.expose.boot.BOOT_ASYNC_DISCOVERY_TIMEOUT", 1):

            await _async_flow_discovery(
                ["no-flows-app"], "http://serve:8005", "",
                "http://app:3370", None,
            )

        mock_meta.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_pending_list_returns_immediately(self):
        """No pending apps → no polling, no errors."""
        with patch("kodosumi.service.expose.boot.fetch_openapi_spec") as mock_fetch:
            await _async_flow_discovery(
                [], "http://serve:8005", "",
                "http://app:3370", None,
            )
        mock_fetch.assert_not_called()
