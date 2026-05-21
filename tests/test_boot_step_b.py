"""
Tests for Boot Process Step B: Health Check

Tests the following functions:
- is_final_state() - Check if status is final
- query_ray_serve_status() - Query Ray Serve API
- poll_until_final_state() - Poll until all apps final
- _step_health_check() - Full health check step generator
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from kodosumi.service.expose.boot import (
    is_final_state,
    query_ray_serve_status,
    poll_until_final_state,
    _step_health_check,
    BootStep,
    MessageType,
    BootProgress,
    FINAL_STATES,
    TRANSITIONAL_STATES,
    BOOT_HEALTH_TIMEOUT,
    BOOT_POLL_INTERVAL,
)


class TestIsFinalState:
    """Tests for is_final_state()"""

    def test_running_is_final(self):
        assert is_final_state("RUNNING") is True

    def test_unhealthy_is_final(self):
        assert is_final_state("UNHEALTHY") is True

    def test_deploy_failed_is_final(self):
        assert is_final_state("DEPLOY_FAILED") is True

    def test_not_started_is_final(self):
        assert is_final_state("NOT_STARTED") is True

    def test_deploying_is_not_final(self):
        assert is_final_state("DEPLOYING") is False

    def test_deleting_is_not_final(self):
        assert is_final_state("DELETING") is False

    def test_unknown_is_not_final(self):
        assert is_final_state("UNKNOWN") is False

    def test_all_final_states(self):
        for state in FINAL_STATES:
            assert is_final_state(state) is True

    def test_all_transitional_states(self):
        for state in TRANSITIONAL_STATES:
            assert is_final_state(state) is False


class TestQueryRayServeStatus:
    """Tests for query_ray_serve_status()"""

    @pytest.mark.asyncio
    async def test_parses_running_apps(self):
        mock_response = {
            "applications": {
                "app-1": {"name": "app-1", "status": "RUNNING", "message": ""},
                "app-2": {"name": "app-2", "status": "DEPLOYING", "message": "Installing deps"}
            }
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = MagicMock(
                json=lambda: mock_response,
                raise_for_status=lambda: None
            )
            mock_client.return_value.__aenter__.return_value = mock_instance

            result = await query_ray_serve_status("http://localhost:8265")

            assert "app-1" in result
            assert result["app-1"]["status"] == "RUNNING"
            assert "app-2" in result
            assert result["app-2"]["status"] == "DEPLOYING"

    @pytest.mark.asyncio
    async def test_handles_empty_applications(self):
        mock_response = {"applications": {}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = MagicMock(
                json=lambda: mock_response,
                raise_for_status=lambda: None
            )
            mock_client.return_value.__aenter__.return_value = mock_instance

            result = await query_ray_serve_status("http://localhost:8265")

            assert result == {}

    @pytest.mark.asyncio
    async def test_strips_trailing_slash(self):
        mock_response = {"applications": {}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = MagicMock(
                json=lambda: mock_response,
                raise_for_status=lambda: None
            )
            mock_client.return_value.__aenter__.return_value = mock_instance

            await query_ray_serve_status("http://localhost:8265/")

            # Check the URL called
            mock_instance.get.assert_called_with("http://localhost:8265/api/serve/applications/")


class TestPollUntilFinalState:
    """Tests for poll_until_final_state()"""

    @pytest.mark.asyncio
    async def test_returns_immediately_when_all_running(self):
        with patch("kodosumi.service.expose.boot.query_ray_serve_status") as mock_query:
            mock_query.return_value = {
                "app-1": {"name": "app-1", "status": "RUNNING", "message": ""},
                "app-2": {"name": "app-2", "status": "RUNNING", "message": ""}
            }

            result = await poll_until_final_state(
                "http://localhost:8265",
                ["app-1", "app-2"],
                timeout=10,
                interval=0.1
            )

            assert result["app-1"]["status"] == "RUNNING"
            assert result["app-2"]["status"] == "RUNNING"

    @pytest.mark.asyncio
    async def test_waits_for_deploying_to_finish(self):
        call_count = 0

        async def mock_query(url):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return {"app-1": {"name": "app-1", "status": "DEPLOYING", "message": ""}}
            return {"app-1": {"name": "app-1", "status": "RUNNING", "message": ""}}

        with patch("kodosumi.service.expose.boot.query_ray_serve_status", side_effect=mock_query):
            result = await poll_until_final_state(
                "http://localhost:8265",
                ["app-1"],
                timeout=10,
                interval=0.01
            )

            assert result["app-1"]["status"] == "RUNNING"
            assert call_count >= 3

    @pytest.mark.asyncio
    async def test_timeout_raises_error(self):
        with patch("kodosumi.service.expose.boot.query_ray_serve_status") as mock_query:
            mock_query.return_value = {
                "app-1": {"name": "app-1", "status": "DEPLOYING", "message": ""}
            }

            with pytest.raises(TimeoutError):
                await poll_until_final_state(
                    "http://localhost:8265",
                    ["app-1"],
                    timeout=0.1,
                    interval=0.05
                )

    @pytest.mark.asyncio
    async def test_handles_mixed_final_states(self):
        with patch("kodosumi.service.expose.boot.query_ray_serve_status") as mock_query:
            mock_query.return_value = {
                "app-1": {"name": "app-1", "status": "RUNNING", "message": ""},
                "app-2": {"name": "app-2", "status": "DEPLOY_FAILED", "message": "Error"}
            }

            result = await poll_until_final_state(
                "http://localhost:8265",
                ["app-1", "app-2"],
                timeout=10,
                interval=0.1
            )

            assert result["app-1"]["status"] == "RUNNING"
            assert result["app-2"]["status"] == "DEPLOY_FAILED"


class TestStepHealthCheck:
    """Tests for _step_health_check()"""

    @pytest.mark.asyncio
    async def test_yields_step_start_message(self):
        with patch("kodosumi.service.expose.boot.query_ray_serve_status") as mock_query, \
             patch("kodosumi.service.expose.boot.db") as mock_db:
            mock_query.return_value = {
                "app-1": {"name": "app-1", "status": "RUNNING", "message": ""}
            }
            mock_db.update_expose_state = AsyncMock()

            progress = BootProgress()
            messages = []
            async for msg in _step_health_check("http://localhost:8265", ["app-1"], progress):
                messages.append(msg)

            step_start = [m for m in messages if m.msg_type == MessageType.STEP_START]
            assert len(step_start) == 1
            assert step_start[0].step == BootStep.HEALTH

    @pytest.mark.asyncio
    async def test_yields_step_end_with_summary(self):
        with patch("kodosumi.service.expose.boot.query_ray_serve_status") as mock_query, \
             patch("kodosumi.service.expose.boot.db") as mock_db:
            mock_query.return_value = {
                "app-1": {"name": "app-1", "status": "RUNNING", "message": ""}
            }
            mock_db.update_expose_state = AsyncMock()

            progress = BootProgress()
            messages = []
            async for msg in _step_health_check("http://localhost:8265", ["app-1"], progress):
                messages.append(msg)

            step_end = [m for m in messages if m.msg_type == MessageType.STEP_END]
            assert len(step_end) == 1
            assert "1 running" in step_end[0].message
            assert "0 failed" in step_end[0].message

    @pytest.mark.asyncio
    async def test_handles_empty_app_list(self):
        progress = BootProgress()
        messages = []
        async for msg in _step_health_check("http://localhost:8265", [], progress):
            messages.append(msg)

        warnings = [m for m in messages if m.msg_type == MessageType.WARNING]
        assert any("No applications" in m.message for m in warnings)

    @pytest.mark.asyncio
    async def test_updates_database_on_running(self):
        with patch("kodosumi.service.expose.boot.query_ray_serve_status") as mock_query, \
             patch("kodosumi.service.expose.boot.db") as mock_db:
            mock_query.return_value = {
                "app-1": {"name": "app-1", "status": "RUNNING", "message": ""}
            }
            mock_db.update_expose_state = AsyncMock()

            progress = BootProgress()
            messages = []
            async for msg in _step_health_check("http://localhost:8265", ["app-1"], progress):
                messages.append(msg)

            # Check database was updated with RUNNING state
            mock_db.update_expose_state.assert_called()
            call_args = mock_db.update_expose_state.call_args_list[0]
            assert call_args[0][0] == "app-1"
            assert call_args[0][1] == "RUNNING"

    @pytest.mark.asyncio
    async def test_updates_database_on_failure(self):
        with patch("kodosumi.service.expose.boot.query_ray_serve_status") as mock_query, \
             patch("kodosumi.service.expose.boot.db") as mock_db:
            mock_query.return_value = {
                "app-1": {"name": "app-1", "status": "DEPLOY_FAILED", "message": "Error"}
            }
            mock_db.update_expose_state = AsyncMock()

            progress = BootProgress()
            messages = []
            async for msg in _step_health_check("http://localhost:8265", ["app-1"], progress):
                messages.append(msg)

            # Check database was updated with UNHEALTHY state
            mock_db.update_expose_state.assert_called()
            call_args = mock_db.update_expose_state.call_args_list[0]
            assert call_args[0][0] == "app-1"
            assert call_args[0][1] == "UNHEALTHY"

    @pytest.mark.asyncio
    async def test_emits_activity_for_deploying(self):
        call_count = 0

        async def mock_query(url):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return {"app-1": {"name": "app-1", "status": "DEPLOYING", "message": "Installing"}}
            return {"app-1": {"name": "app-1", "status": "RUNNING", "message": ""}}

        with patch("kodosumi.service.expose.boot.query_ray_serve_status", side_effect=mock_query), \
             patch("kodosumi.service.expose.boot.db") as mock_db, \
             patch("kodosumi.service.expose.boot.BOOT_POLL_INTERVAL", 0.01):
            mock_db.update_expose_state = AsyncMock()

            progress = BootProgress()
            messages = []
            async for msg in _step_health_check("http://localhost:8265", ["app-1"], progress):
                messages.append(msg)

            # Should have activity message for DEPLOYING
            activities = [m for m in messages if m.msg_type == MessageType.ACTIVITY]
            assert len(activities) >= 1
            assert any("DEPLOYING" in m.message for m in activities)

    @pytest.mark.asyncio
    async def test_returns_final_statuses_in_data(self):
        with patch("kodosumi.service.expose.boot.query_ray_serve_status") as mock_query, \
             patch("kodosumi.service.expose.boot.db") as mock_db:
            mock_query.return_value = {
                "app-1": {"name": "app-1", "status": "RUNNING", "message": ""},
                "app-2": {"name": "app-2", "status": "UNHEALTHY", "message": "Bad"}
            }
            mock_db.update_expose_state = AsyncMock()

            progress = BootProgress()
            messages = []
            async for msg in _step_health_check("http://localhost:8265", ["app-1", "app-2"], progress):
                messages.append(msg)

            step_end = [m for m in messages if m.msg_type == MessageType.STEP_END][0]
            assert step_end.data is not None
            assert "final_statuses" in step_end.data
            assert step_end.data["final_statuses"]["app-1"]["status"] == "RUNNING"
            assert step_end.data["final_statuses"]["app-2"]["status"] == "UNHEALTHY"


class TestStepHealthCheckIntegration:
    """Integration tests for the full health check flow."""

    @pytest.mark.asyncio
    async def test_tracks_multiple_apps_independently(self):
        """Test that apps with different deployment times are tracked independently."""
        call_count = 0

        async def mock_query(url):
            nonlocal call_count
            call_count += 1
            # app-1 becomes RUNNING quickly, app-2 takes longer
            if call_count < 2:
                return {
                    "app-1": {"name": "app-1", "status": "DEPLOYING", "message": ""},
                    "app-2": {"name": "app-2", "status": "DEPLOYING", "message": ""}
                }
            elif call_count < 3:
                return {
                    "app-1": {"name": "app-1", "status": "RUNNING", "message": ""},
                    "app-2": {"name": "app-2", "status": "DEPLOYING", "message": ""}
                }
            else:
                return {
                    "app-1": {"name": "app-1", "status": "RUNNING", "message": ""},
                    "app-2": {"name": "app-2", "status": "RUNNING", "message": ""}
                }

        with patch("kodosumi.service.expose.boot.query_ray_serve_status", side_effect=mock_query), \
             patch("kodosumi.service.expose.boot.db") as mock_db, \
             patch("kodosumi.service.expose.boot.BOOT_POLL_INTERVAL", 0.01):
            mock_db.update_expose_state = AsyncMock()

            progress = BootProgress()
            messages = []
            async for msg in _step_health_check("http://localhost:8265", ["app-1", "app-2"], progress):
                messages.append(msg)

            # Both should reach final state
            step_end = [m for m in messages if m.msg_type == MessageType.STEP_END][0]
            assert "2 running" in step_end.message
            assert "0 failed" in step_end.message

            # Database should be updated for both
            assert mock_db.update_expose_state.call_count == 2
