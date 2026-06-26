"""
Tests for #73 + #60 — Scale-to-Zero Probing via Ray Dashboard

Verifies that:
- check_all_flows / _step_retrieve_flows use the Ray dashboard control-plane API
  (replica-free) instead of HEAD/GET probes when ray_dashboard is configured.
- On ConnectError from the dashboard, the code falls back to the existing
  check_flow_health HEAD probe.
- When ray_dashboard is empty, the original HEAD behaviour is preserved.
- _check_availability in sumi/control.py uses the dashboard probe with a 10-second
  TTL in-process cache; no GET to ray_serve_address.
"""

import time
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch, call

from kodosumi.service.expose.boot import (
    DiscoveredFlow,
    FlowStatus,
    BootProgress,
    BootStep,
    MessageType,
    ValidationResult,
    check_all_flows,
    _step_retrieve_flows,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_flow(app_name: str = "test-agent", path: str = "/test-agent/run") -> DiscoveredFlow:
    return DiscoveredFlow(
        app_name=app_name,
        path=path,
        method="POST",
        summary="Run",
        description="",
        tags=[],
    )


# =============================================================================
# check_all_flows — dashboard-first logic
# =============================================================================

class TestCheckAllFlowsDashboard:
    """Tests for check_all_flows with ray_dashboard configured."""

    @pytest.mark.asyncio
    async def test_dashboard_returns_alive(self):
        """
        When check_app_running returns valid=True, state must be 'alive'
        and check_flow_health (HEAD) must NOT be called.
        """
        flow = _make_flow()

        mock_result = ValidationResult(valid=True, message="RUNNING")

        with (
            patch(
                "kodosumi.service.expose.boot.check_app_running",
                new=AsyncMock(return_value=mock_result),
            ) as mock_dashboard,
            patch(
                "kodosumi.service.expose.boot.check_flow_health",
                new=AsyncMock(return_value=("alive", 200)),
            ) as mock_head,
        ):
            results = await check_all_flows(
                "http://localhost:8005",
                [flow],
                ray_dashboard="http://localhost:8265",
            )

        statuses = results["test-agent"]
        assert len(statuses) == 1
        assert statuses[0].state == "alive"
        mock_dashboard.assert_awaited_once_with("http://localhost:8265", "test-agent")
        mock_head.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dashboard_returns_dead(self):
        """
        When check_app_running returns valid=False with a non-'not found' message,
        state must be 'dead'.
        """
        flow = _make_flow()

        mock_result = ValidationResult(valid=False, message="Status is UNHEALTHY")

        with (
            patch(
                "kodosumi.service.expose.boot.check_app_running",
                new=AsyncMock(return_value=mock_result),
            ),
            patch(
                "kodosumi.service.expose.boot.check_flow_health",
                new=AsyncMock(return_value=("alive", 200)),
            ) as mock_head,
        ):
            results = await check_all_flows(
                "http://localhost:8005",
                [flow],
                ray_dashboard="http://localhost:8265",
            )

        assert results["test-agent"][0].state == "dead"
        mock_head.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dashboard_not_found(self):
        """
        When check_app_running returns valid=False with message containing 'not found',
        state must be 'not-found'.
        """
        flow = _make_flow()

        mock_result = ValidationResult(
            valid=False, message="Application 'test-agent' not found"
        )

        with (
            patch(
                "kodosumi.service.expose.boot.check_app_running",
                new=AsyncMock(return_value=mock_result),
            ),
            patch(
                "kodosumi.service.expose.boot.check_flow_health",
                new=AsyncMock(),
            ) as mock_head,
        ):
            results = await check_all_flows(
                "http://localhost:8005",
                [flow],
                ray_dashboard="http://localhost:8265",
            )

        assert results["test-agent"][0].state == "not-found"
        mock_head.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dashboard_connect_error_falls_back_to_head(self):
        """
        When check_app_running raises httpx.ConnectError, check_flow_health
        (HEAD) must be invoked as fallback.
        """
        flow = _make_flow()

        with (
            patch(
                "kodosumi.service.expose.boot.check_app_running",
                new=AsyncMock(return_value=ValidationResult(
                    valid=False, message="Dashboard timeout",
                    details={"unreachable": True})),
            ),
            patch(
                "kodosumi.service.expose.boot.check_flow_health",
                new=AsyncMock(return_value=("alive", 200)),
            ) as mock_head,
        ):
            results = await check_all_flows(
                "http://localhost:8005",
                [flow],
                ray_dashboard="http://localhost:8265",
            )

        # Fallback HEAD probe must have been called
        mock_head.assert_awaited_once()
        assert results["test-agent"][0].state == "alive"

    @pytest.mark.asyncio
    async def test_no_dashboard_uses_head(self):
        """
        When ray_dashboard is empty, check_app_running must NOT be called;
        check_flow_health (HEAD) is used directly.
        """
        flow = _make_flow()

        with (
            patch(
                "kodosumi.service.expose.boot.check_app_running",
                new=AsyncMock(return_value=ValidationResult(valid=True, message="RUNNING")),
            ) as mock_dashboard,
            patch(
                "kodosumi.service.expose.boot.check_flow_health",
                new=AsyncMock(return_value=("alive", 200)),
            ) as mock_head,
        ):
            results = await check_all_flows(
                "http://localhost:8005",
                [flow],
                ray_dashboard="",
            )

        mock_dashboard.assert_not_awaited()
        mock_head.assert_awaited_once()
        assert results["test-agent"][0].state == "alive"


# =============================================================================
# _step_retrieve_flows — dashboard-first integration
# =============================================================================

class TestStepRetrieveFlowsDashboard:
    """Tests for _step_retrieve_flows generator with dashboard probe."""

    @pytest.mark.asyncio
    async def test_dashboard_alive_state_propagates(self):
        """
        With ray_dashboard configured and check_app_running returning valid=True,
        the STEP_END data must contain state='alive' and check_flow_health must
        NOT have been called.
        """
        flows = [_make_flow()]
        progress = BootProgress()
        messages = []

        mock_result = ValidationResult(valid=True, message="RUNNING")

        with (
            patch(
                "kodosumi.service.expose.boot.check_app_running",
                new=AsyncMock(return_value=mock_result),
            ),
            patch(
                "kodosumi.service.expose.boot.check_flow_health",
                new=AsyncMock(return_value=("alive", 200)),
            ) as mock_head,
        ):
            async for msg in _step_retrieve_flows(
                "http://localhost:8005",
                flows,
                progress,
                ray_dashboard="http://localhost:8265",
            ):
                messages.append(msg)

        end_msg = next(m for m in messages if m.msg_type == MessageType.STEP_END)
        assert "test-agent" in end_msg.data["flow_statuses"]
        assert end_msg.data["flow_statuses"]["test-agent"][0].state == "alive"
        mock_head.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_found_state_propagates(self):
        """With 'not found' dashboard response, state == 'not-found' in step data."""
        flows = [_make_flow()]
        progress = BootProgress()
        messages = []

        mock_result = ValidationResult(valid=False, message="Application 'test-agent' not found")

        with patch(
            "kodosumi.service.expose.boot.check_app_running",
            new=AsyncMock(return_value=mock_result),
        ):
            async for msg in _step_retrieve_flows(
                "http://localhost:8005",
                flows,
                progress,
                ray_dashboard="http://localhost:8265",
            ):
                messages.append(msg)

        end_msg = next(m for m in messages if m.msg_type == MessageType.STEP_END)
        assert end_msg.data["flow_statuses"]["test-agent"][0].state == "not-found"

    @pytest.mark.asyncio
    async def test_connect_error_falls_back_to_head(self):
        """On ConnectError, _step_retrieve_flows falls back to HEAD probe."""
        flows = [_make_flow()]
        progress = BootProgress()
        messages = []

        with (
            patch(
                "kodosumi.service.expose.boot.check_app_running",
                new=AsyncMock(return_value=ValidationResult(
                    valid=False, message="Dashboard timeout",
                    details={"unreachable": True})),
            ),
            patch(
                "kodosumi.service.expose.boot.check_flow_health",
                new=AsyncMock(return_value=("alive", 200)),
            ) as mock_head,
        ):
            async for msg in _step_retrieve_flows(
                "http://localhost:8005",
                flows,
                progress,
                ray_dashboard="http://localhost:8265",
            ):
                messages.append(msg)

        mock_head.assert_awaited_once()
        end_msg = next(m for m in messages if m.msg_type == MessageType.STEP_END)
        assert end_msg.data["flow_statuses"]["test-agent"][0].state == "alive"


# =============================================================================
# sumi/control.py — _check_availability
# =============================================================================

class TestCheckAvailabilityDashboard:
    """
    Tests for _check_availability in sumi/control.py.

    All tests import _check_availability directly and clear the module-level
    cache before each test to avoid cross-test pollution.
    """

    def setup_method(self):
        # Clear the in-process availability cache before each test
        import kodosumi.service.sumi.control as ctrl
        ctrl._availability_cache.clear()

    @pytest.mark.asyncio
    async def test_dashboard_available(self):
        """
        When check_app_running returns valid=True, AvailabilityResponse.status
        must be 'available' and no request must reach ray_serve_address.
        """
        import kodosumi.service.sumi.control as ctrl
        from kodosumi.service.expose.models import ExposeMeta

        # Minimal ExposeMeta stub
        meta_stub = ExposeMeta(url="/my-agent/run", enabled=True, state="alive")
        mock_result = ValidationResult(valid=True, message="RUNNING")

        with (
            patch(
                "kodosumi.service.sumi.control._get_meta_entry",
                new=AsyncMock(return_value=({"name": "my-agent"}, meta_stub)),
            ),
            patch(
                "kodosumi.service.sumi.control.check_app_running",
                new=AsyncMock(return_value=mock_result),
            ) as mock_dashboard,
            patch(
                "kodosumi.service.sumi.control.HTTPXClient",
            ) as mock_http,
        ):
            resp = await ctrl._check_availability(
                "my-agent", "", "http://localhost:8005",
                ray_dashboard="http://localhost:8265",
            )

        assert resp.status == "available"
        mock_dashboard.assert_awaited_once_with("http://localhost:8265", "my-agent")
        # HTTPXClient (HEAD) must NOT have been used
        mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_dashboard_unavailable(self):
        """
        When check_app_running returns valid=False, status must be 'unavailable'.
        """
        import kodosumi.service.sumi.control as ctrl
        from kodosumi.service.expose.models import ExposeMeta

        meta_stub = ExposeMeta(url="/my-agent/run", enabled=True, state="alive")
        mock_result = ValidationResult(valid=False, message="Status is DEPLOY_FAILED")

        with (
            patch(
                "kodosumi.service.sumi.control._get_meta_entry",
                new=AsyncMock(return_value=({"name": "my-agent"}, meta_stub)),
            ),
            patch(
                "kodosumi.service.sumi.control.check_app_running",
                new=AsyncMock(return_value=mock_result),
            ),
        ):
            resp = await ctrl._check_availability(
                "my-agent", "", "http://localhost:8005",
                ray_dashboard="http://localhost:8265",
            )

        assert resp.status == "unavailable"

    @pytest.mark.asyncio
    async def test_fallback_uses_head_not_get(self):
        """
        When ray_dashboard is empty, _head_availability is used and the HTTP
        method must be HEAD (not GET).
        """
        import kodosumi.service.sumi.control as ctrl
        from kodosumi.service.expose.models import ExposeMeta

        meta_stub = ExposeMeta(url="/my-agent/run", enabled=True, state="alive")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_response)
        mock_client.get = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "kodosumi.service.sumi.control._get_meta_entry",
                new=AsyncMock(return_value=({"name": "my-agent"}, meta_stub)),
            ),
            patch(
                "kodosumi.service.sumi.control.HTTPXClient",
                return_value=mock_client,
            ),
        ):
            resp = await ctrl._check_availability(
                "my-agent", "", "http://localhost:8005",
                ray_dashboard="",
            )

        assert resp.status == "available"
        mock_client.head.assert_awaited_once()
        mock_client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_connect_error_fallback_to_head(self):
        """
        When dashboard raises ConnectError, _check_availability falls back to
        HEAD probe.
        """
        import kodosumi.service.sumi.control as ctrl
        from kodosumi.service.expose.models import ExposeMeta

        meta_stub = ExposeMeta(url="/my-agent/run", enabled=True, state="alive")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.head = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "kodosumi.service.sumi.control._get_meta_entry",
                new=AsyncMock(return_value=({"name": "my-agent"}, meta_stub)),
            ),
            patch(
                "kodosumi.service.sumi.control.check_app_running",
                new=AsyncMock(return_value=ValidationResult(
                    valid=False, message="Dashboard timeout",
                    details={"unreachable": True})),
            ),
            patch(
                "kodosumi.service.sumi.control.HTTPXClient",
                return_value=mock_client,
            ),
        ):
            resp = await ctrl._check_availability(
                "my-agent", "", "http://localhost:8005",
                ray_dashboard="http://localhost:8265",
            )

        assert resp.status == "available"
        mock_client.head.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_debounce(self):
        """
        Two calls within _AVAILABILITY_CACHE_TTL seconds must only invoke
        check_app_running once (second call is served from cache).
        """
        import kodosumi.service.sumi.control as ctrl
        from kodosumi.service.expose.models import ExposeMeta

        meta_stub = ExposeMeta(url="/my-agent/run", enabled=True, state="alive")
        mock_result = ValidationResult(valid=True, message="RUNNING")

        with (
            patch(
                "kodosumi.service.sumi.control._get_meta_entry",
                new=AsyncMock(return_value=({"name": "my-agent"}, meta_stub)),
            ),
            patch(
                "kodosumi.service.sumi.control.check_app_running",
                new=AsyncMock(return_value=mock_result),
            ) as mock_dashboard,
        ):
            resp1 = await ctrl._check_availability(
                "my-agent", "", "http://localhost:8005",
                ray_dashboard="http://localhost:8265",
            )
            resp2 = await ctrl._check_availability(
                "my-agent", "", "http://localhost:8005",
                ray_dashboard="http://localhost:8265",
            )

        assert resp1.status == "available"
        assert resp2.status == "available"
        # dashboard called only once — second response came from cache
        assert mock_dashboard.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        """
        After _AVAILABILITY_CACHE_TTL seconds, the cache is stale and
        check_app_running is called again.
        """
        import kodosumi.service.sumi.control as ctrl
        from kodosumi.service.expose.models import ExposeMeta

        meta_stub = ExposeMeta(url="/my-agent/run", enabled=True, state="alive")
        mock_result = ValidationResult(valid=True, message="RUNNING")

        with (
            patch(
                "kodosumi.service.sumi.control._get_meta_entry",
                new=AsyncMock(return_value=({"name": "my-agent"}, meta_stub)),
            ),
            patch(
                "kodosumi.service.sumi.control.check_app_running",
                new=AsyncMock(return_value=mock_result),
            ) as mock_dashboard,
        ):
            # First call — populates cache
            await ctrl._check_availability(
                "my-agent", "", "http://localhost:8005",
                ray_dashboard="http://localhost:8265",
            )
            # Expire the cache by back-dating the timestamp
            expose_key = "my-agent"
            ts, resp = ctrl._availability_cache[expose_key]
            ctrl._availability_cache[expose_key] = (
                ts - ctrl._AVAILABILITY_CACHE_TTL - 1, resp
            )
            # Second call — cache is stale, should call dashboard again
            await ctrl._check_availability(
                "my-agent", "", "http://localhost:8005",
                ray_dashboard="http://localhost:8265",
            )

        assert mock_dashboard.await_count == 2

    @pytest.mark.asyncio
    async def test_not_found_expose(self):
        """
        When _get_meta_entry raises NotFoundException, status is 'unavailable'
        and check_app_running is NOT called.
        """
        import kodosumi.service.sumi.control as ctrl
        from litestar.exceptions import NotFoundException

        with (
            patch(
                "kodosumi.service.sumi.control._get_meta_entry",
                new=AsyncMock(side_effect=NotFoundException(detail="not found")),
            ),
            patch(
                "kodosumi.service.sumi.control.check_app_running",
                new=AsyncMock(),
            ) as mock_dashboard,
        ):
            resp = await ctrl._check_availability(
                "missing-agent", "", "http://localhost:8005",
                ray_dashboard="http://localhost:8265",
            )

        assert resp.status == "unavailable"
        mock_dashboard.assert_not_awaited()
