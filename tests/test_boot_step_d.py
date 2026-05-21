"""
Tests for Boot Process Step D: Retrieve Flows (HEAD Requests)

Tests the following functions:
- FlowStatus dataclass
- check_flow_health() - Send HEAD request to flow endpoint
- check_all_flows() - Check all flows in parallel
- _step_retrieve_flows() - Full retrieve flows step generator
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
import time

from kodosumi.service.expose.boot import (
    FlowStatus,
    DiscoveredFlow,
    check_flow_health,
    check_all_flows,
    _step_retrieve_flows,
    BootProgress,
    BootStep,
    MessageType,
)


# =============================================================================
# FlowStatus Tests
# =============================================================================

class TestFlowStatus:
    """Tests for FlowStatus dataclass."""

    def test_create_alive_status(self):
        """Test creating an alive flow status."""
        flow = DiscoveredFlow(
            app_name="test-app",
            path="/test-app/run",
            method="POST",
            summary="Run",
            description="",
            tags=[],
        )

        status = FlowStatus(
            flow=flow,
            state="alive",
            response_code=200,
            checked_at=1700000000.0,
        )

        assert status.state == "alive"
        assert status.response_code == 200
        assert status.checked_at == 1700000000.0
        assert status.flow.app_name == "test-app"

    def test_create_dead_status(self):
        """Test creating a dead flow status."""
        flow = DiscoveredFlow(
            app_name="test-app",
            path="/test-app/run",
            method="POST",
            summary="Run",
            description="",
            tags=[],
        )

        status = FlowStatus(
            flow=flow,
            state="dead",
            response_code=500,
            checked_at=1700000000.0,
        )

        assert status.state == "dead"
        assert status.response_code == 500

    def test_create_timeout_status(self):
        """Test creating a timeout flow status."""
        flow = DiscoveredFlow(
            app_name="test-app",
            path="/test-app/run",
            method="POST",
            summary="Run",
            description="",
            tags=[],
        )

        status = FlowStatus(
            flow=flow,
            state="timeout",
            response_code=None,
            checked_at=1700000000.0,
        )

        assert status.state == "timeout"
        assert status.response_code is None


# =============================================================================
# check_flow_health Tests
# =============================================================================

class TestCheckFlowHealth:
    """Tests for check_flow_health function."""

    @pytest.mark.asyncio
    async def test_returns_alive_on_200(self):
        """Test 200 OK returns alive."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 200

            mock_client = AsyncMock()
            mock_client.head.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_cls.return_value = mock_client

            state, code = await check_flow_health("http://localhost:8005", "/app/run")

        assert state == "alive"
        assert code == 200

    @pytest.mark.asyncio
    async def test_returns_alive_on_204(self):
        """Test 204 No Content returns alive."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 204

            mock_client = AsyncMock()
            mock_client.head.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_cls.return_value = mock_client

            state, code = await check_flow_health("http://localhost:8005", "/app/run")

        assert state == "alive"
        assert code == 204

    @pytest.mark.asyncio
    async def test_returns_alive_on_405(self):
        """Test 405 Method Not Allowed returns alive (endpoint exists)."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 405

            mock_client = AsyncMock()
            mock_client.head.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_cls.return_value = mock_client

            state, code = await check_flow_health("http://localhost:8005", "/app/run")

        assert state == "alive"
        assert code == 405

    @pytest.mark.asyncio
    async def test_returns_not_found_on_404(self):
        """Test 404 Not Found returns not-found."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 404

            mock_client = AsyncMock()
            mock_client.head.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_cls.return_value = mock_client

            state, code = await check_flow_health("http://localhost:8005", "/app/run")

        assert state == "not-found"
        assert code == 404

    @pytest.mark.asyncio
    async def test_returns_dead_on_500(self):
        """Test 500 Internal Server Error returns dead."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 500

            mock_client = AsyncMock()
            mock_client.head.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_cls.return_value = mock_client

            state, code = await check_flow_health("http://localhost:8005", "/app/run")

        assert state == "dead"
        assert code == 500

    @pytest.mark.asyncio
    async def test_returns_dead_on_502(self):
        """Test 502 Bad Gateway returns dead."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 502

            mock_client = AsyncMock()
            mock_client.head.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_cls.return_value = mock_client

            state, code = await check_flow_health("http://localhost:8005", "/app/run")

        assert state == "dead"
        assert code == 502

    @pytest.mark.asyncio
    async def test_returns_timeout_on_timeout(self):
        """Test timeout exception returns timeout state."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.head.side_effect = httpx.TimeoutException("Timeout")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_cls.return_value = mock_client

            state, code = await check_flow_health("http://localhost:8005", "/app/run")

        assert state == "timeout"
        assert code is None

    @pytest.mark.asyncio
    async def test_returns_dead_on_connection_error(self):
        """Test connection error returns dead."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.head.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_cls.return_value = mock_client

            state, code = await check_flow_health("http://localhost:8005", "/app/run")

        assert state == "dead"
        assert code is None

    @pytest.mark.asyncio
    async def test_strips_trailing_slash(self):
        """Test URL is correctly constructed with trailing slash."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.status_code = 200

            mock_client = AsyncMock()
            mock_client.head.return_value = mock_response
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_cls.return_value = mock_client

            await check_flow_health("http://localhost:8005/", "/app/run")

            # Check the URL was constructed correctly
            call_args = mock_client.head.call_args
            assert call_args[0][0] == "http://localhost:8005/app/run"


# =============================================================================
# check_all_flows Tests
# =============================================================================

class TestCheckAllFlows:
    """Tests for check_all_flows function."""

    @pytest.mark.asyncio
    async def test_checks_all_flows_in_parallel(self):
        """Test all flows are checked and grouped by app."""
        flows = [
            DiscoveredFlow(
                app_name="app1",
                path="/app1/run",
                method="POST",
                summary="Run",
                description="",
                tags=[],
            ),
            DiscoveredFlow(
                app_name="app1",
                path="/app1/query",
                method="GET",
                summary="Query",
                description="",
                tags=[],
            ),
            DiscoveredFlow(
                app_name="app2",
                path="/app2/execute",
                method="POST",
                summary="Execute",
                description="",
                tags=[],
            ),
        ]

        with patch("kodosumi.service.expose.boot.check_flow_health") as mock_check:
            mock_check.return_value = ("alive", 200)

            results = await check_all_flows("http://localhost:8005", flows)

        # Should be grouped by app
        assert "app1" in results
        assert "app2" in results
        assert len(results["app1"]) == 2
        assert len(results["app2"]) == 1

    @pytest.mark.asyncio
    async def test_handles_mixed_statuses(self):
        """Test handling mixed alive/dead statuses."""
        flows = [
            DiscoveredFlow(
                app_name="app1",
                path="/app1/run",
                method="POST",
                summary="Run",
                description="",
                tags=[],
            ),
            DiscoveredFlow(
                app_name="app1",
                path="/app1/broken",
                method="POST",
                summary="Broken",
                description="",
                tags=[],
            ),
        ]

        async def mock_check(addr, path):
            if "broken" in path:
                return ("dead", 500)
            return ("alive", 200)

        with patch("kodosumi.service.expose.boot.check_flow_health", side_effect=mock_check):
            results = await check_all_flows("http://localhost:8005", flows)

        assert len(results["app1"]) == 2
        states = {s.state for s in results["app1"]}
        assert states == {"alive", "dead"}

    @pytest.mark.asyncio
    async def test_empty_flows_returns_empty(self):
        """Test empty flows list returns empty dict."""
        results = await check_all_flows("http://localhost:8005", [])
        assert results == {}

    @pytest.mark.asyncio
    async def test_records_check_time(self):
        """Test checked_at timestamp is recorded."""
        flows = [
            DiscoveredFlow(
                app_name="app1",
                path="/app1/run",
                method="POST",
                summary="Run",
                description="",
                tags=[],
            ),
        ]

        before = time.time()

        with patch("kodosumi.service.expose.boot.check_flow_health") as mock_check:
            mock_check.return_value = ("alive", 200)
            results = await check_all_flows("http://localhost:8005", flows)

        after = time.time()

        status = results["app1"][0]
        assert before <= status.checked_at <= after


# =============================================================================
# _step_retrieve_flows Tests
# =============================================================================

class TestStepRetrieveFlows:
    """Tests for _step_retrieve_flows generator function."""

    @pytest.mark.asyncio
    async def test_step_with_no_flows(self):
        """Test step with empty flows list."""
        progress = BootProgress()
        messages = []

        async for msg in _step_retrieve_flows("http://localhost:8005", [], progress):
            messages.append(msg)

        assert len(messages) >= 2
        assert messages[0].msg_type == MessageType.STEP_START
        assert any(m.msg_type == MessageType.WARNING for m in messages)
        assert any(m.msg_type == MessageType.STEP_END for m in messages)

    @pytest.mark.asyncio
    async def test_step_tests_flows(self):
        """Test step tests flows and reports results."""
        flows = [
            DiscoveredFlow(
                app_name="test-app",
                path="/test-app/run",
                method="POST",
                summary="Run",
                description="",
                tags=[],
            ),
        ]

        progress = BootProgress()
        messages = []

        with patch("kodosumi.service.expose.boot.check_flow_health") as mock_check:
            mock_check.return_value = ("alive", 200)

            async for msg in _step_retrieve_flows("http://localhost:8005", flows, progress):
                messages.append(msg)

        # Check message types
        types = [m.msg_type for m in messages]
        assert MessageType.STEP_START in types
        assert MessageType.ACTIVITY in types
        assert MessageType.RESULT in types
        assert MessageType.STEP_END in types

        # Check result message
        result_msg = next(m for m in messages if m.msg_type == MessageType.RESULT)
        assert "HEAD" in result_msg.message
        assert "alive" in result_msg.result

    @pytest.mark.asyncio
    async def test_step_returns_flow_statuses(self):
        """Test step returns flow statuses in data."""
        flows = [
            DiscoveredFlow(
                app_name="test-app",
                path="/test-app/run",
                method="POST",
                summary="Run",
                description="",
                tags=[],
            ),
        ]

        progress = BootProgress()
        messages = []

        with patch("kodosumi.service.expose.boot.check_flow_health") as mock_check:
            mock_check.return_value = ("alive", 200)

            async for msg in _step_retrieve_flows("http://localhost:8005", flows, progress):
                messages.append(msg)

        end_msg = next(m for m in messages if m.msg_type == MessageType.STEP_END)
        assert end_msg.data is not None
        assert "flow_statuses" in end_msg.data
        assert "test-app" in end_msg.data["flow_statuses"]

    @pytest.mark.asyncio
    async def test_step_counts_alive_and_dead(self):
        """Test step counts alive and dead endpoints."""
        flows = [
            DiscoveredFlow(
                app_name="app",
                path="/app/good",
                method="POST",
                summary="Good",
                description="",
                tags=[],
            ),
            DiscoveredFlow(
                app_name="app",
                path="/app/bad",
                method="POST",
                summary="Bad",
                description="",
                tags=[],
            ),
        ]

        async def mock_check(addr, path):
            if "bad" in path:
                return ("dead", 500)
            return ("alive", 200)

        progress = BootProgress()
        messages = []

        with patch("kodosumi.service.expose.boot.check_flow_health", side_effect=mock_check):
            async for msg in _step_retrieve_flows("http://localhost:8005", flows, progress):
                messages.append(msg)

        end_msg = next(m for m in messages if m.msg_type == MessageType.STEP_END)
        assert "1 alive" in end_msg.message
        assert "1 dead" in end_msg.message

    @pytest.mark.asyncio
    async def test_step_progress_tracking(self):
        """Test progress is tracked correctly."""
        flows = [
            DiscoveredFlow(
                app_name="app",
                path="/app/run",
                method="POST",
                summary="Run",
                description="",
                tags=[],
            ),
        ]

        progress = BootProgress()

        with patch("kodosumi.service.expose.boot.check_flow_health") as mock_check:
            mock_check.return_value = ("alive", 200)

            async for msg in _step_retrieve_flows("http://localhost:8005", flows, progress):
                pass

        assert progress.current_step == 3
        assert progress.step_name == "Retrieve Flows"
        assert progress.activities_done == 1


# =============================================================================
# Integration Tests
# =============================================================================

class TestRetrieveFlowsIntegration:
    """Integration tests for flow retrieval."""

    @pytest.mark.asyncio
    async def test_full_retrieval_pipeline(self):
        """Test complete flow retrieval from multiple apps."""
        flows = [
            DiscoveredFlow(
                app_name="agent1",
                path="/agent1/run",
                method="POST",
                summary="Run Agent 1",
                description="Execute agent 1",
                tags=["agent"],
            ),
            DiscoveredFlow(
                app_name="agent2",
                path="/agent2/execute",
                method="POST",
                summary="Execute Agent 2",
                description="Execute agent 2",
                tags=["agent"],
            ),
        ]

        async def mock_check(addr, path):
            if "agent1" in path:
                return ("alive", 200)
            return ("alive", 405)  # Method not allowed but still alive

        progress = BootProgress()
        messages = []

        with patch("kodosumi.service.expose.boot.check_flow_health", side_effect=mock_check):
            async for msg in _step_retrieve_flows("http://localhost:8005", flows, progress):
                messages.append(msg)

        # Both should be alive
        end_msg = next(m for m in messages if m.msg_type == MessageType.STEP_END)
        assert "2 alive" in end_msg.message
        assert "0 dead" in end_msg.message
