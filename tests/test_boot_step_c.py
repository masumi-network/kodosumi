"""
Tests for Boot Process Step C: Register Flows

Tests the following functions:
- fetch_openapi_spec() - Fetch OpenAPI JSON from deployed app
- extract_kodosumi_endpoints() - Extract marked endpoints from spec
- _step_register_flows() - Full register flows step generator
"""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from kodosumi.service.expose.boot import (
    DiscoveredFlow,
    fetch_openapi_spec,
    extract_kodosumi_endpoints,
    _step_register_flows,
    BootStep,
    MessageType,
    BootProgress,
)


class TestDiscoveredFlow:
    """Tests for DiscoveredFlow dataclass."""

    def test_creates_with_required_fields(self):
        flow = DiscoveredFlow(
            app_name="my-app",
            path="/run",
            method="POST",
            summary="Run the agent",
            description="Execute the agent workflow",
            tags=["agent"]
        )

        assert flow.app_name == "my-app"
        assert flow.path == "/run"
        assert flow.method == "POST"
        assert flow.author is None
        assert flow.organization is None

    def test_creates_with_optional_fields(self):
        flow = DiscoveredFlow(
            app_name="my-app",
            path="/run",
            method="POST",
            summary="Run",
            description="",
            tags=[],
            author="dev@example.com",
            organization="Acme Corp"
        )

        assert flow.author == "dev@example.com"
        assert flow.organization == "Acme Corp"


class TestFetchOpenAPISpec:
    """Tests for fetch_openapi_spec()."""

    @pytest.mark.asyncio
    async def test_fetches_valid_spec(self):
        mock_spec = {
            "openapi": "3.0.0",
            "paths": {"/run": {"post": {"summary": "Run"}}}
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = MagicMock(
                status_code=200,
                json=lambda: mock_spec,
                raise_for_status=lambda: None
            )
            mock_client.return_value.__aenter__.return_value = mock_instance

            spec, url, error = await fetch_openapi_spec("http://localhost:8005", "my-app")

            assert spec == mock_spec
            assert url == "http://localhost:8005/my-app/openapi.json"
            assert error is None
            mock_instance.get.assert_called_with("http://localhost:8005/my-app/openapi.json")

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = MagicMock(status_code=404)
            mock_client.return_value.__aenter__.return_value = mock_instance

            spec, url, error = await fetch_openapi_spec("http://localhost:8005", "my-app")

            assert spec is None
            assert url == "http://localhost:8005/my-app/openapi.json"
            assert "404" in error

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = Exception("Connection refused")
            mock_client.return_value.__aenter__.return_value = mock_instance

            spec, url, error = await fetch_openapi_spec("http://localhost:8005", "my-app")

            assert spec is None
            assert url is not None
            assert "Error" in error

    @pytest.mark.asyncio
    async def test_strips_trailing_slash(self):
        mock_spec = {"openapi": "3.0.0", "paths": {}}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = MagicMock(
                status_code=200,
                json=lambda: mock_spec,
                raise_for_status=lambda: None
            )
            mock_client.return_value.__aenter__.return_value = mock_instance

            spec, url, error = await fetch_openapi_spec("http://localhost:8005/", "my-app")

            assert url == "http://localhost:8005/my-app/openapi.json"
            mock_instance.get.assert_called_with("http://localhost:8005/my-app/openapi.json")


class TestExtractKodosumiEndpoints:
    """Tests for extract_kodosumi_endpoints()."""

    def test_extracts_endpoint_with_x_kodosumi(self):
        """Test primary marker used by ServeAPI (x-kodosumi)."""
        spec = {
            "paths": {
                "/run": {
                    "post": {
                        "summary": "Run Agent",
                        "description": "Execute workflow",
                        "tags": ["agent"],
                        "x-kodosumi": True  # primary marker from const.py
                    }
                }
            }
        }

        flows = extract_kodosumi_endpoints(spec, "my-app")

        assert len(flows) == 1
        assert flows[0].app_name == "my-app"
        assert flows[0].path == "/my-app/run"  # full path with app prefix
        assert flows[0].method == "POST"
        assert flows[0].summary == "Run Agent"

    def test_extracts_endpoint_with_x_kodosumi_api(self):
        """Test legacy marker (x-kodosumi-api) for backward compatibility."""
        spec = {
            "paths": {
                "/run": {
                    "post": {
                        "summary": "Run Agent",
                        "description": "Execute workflow",
                        "tags": ["agent"],
                        "x-kodosumi-api": True  # legacy marker
                    }
                }
            }
        }

        flows = extract_kodosumi_endpoints(spec, "my-app")

        assert len(flows) == 1
        assert flows[0].app_name == "my-app"
        assert flows[0].path == "/my-app/run"  # full path with app prefix
        assert flows[0].method == "POST"
        assert flows[0].summary == "Run Agent"

    def test_extracts_endpoint_with_KODOSUMI_API(self):
        spec = {
            "paths": {
                "/execute": {
                    "post": {
                        "summary": "Execute",
                        "KODOSUMI_API": True
                    }
                }
            }
        }

        flows = extract_kodosumi_endpoints(spec, "my-app")

        assert len(flows) == 1
        assert flows[0].path == "/my-app/execute"  # full path with app prefix

    def test_extracts_endpoint_with_x_openapi_extra(self):
        spec = {
            "paths": {
                "/process": {
                    "post": {
                        "summary": "Process",
                        "x-openapi-extra": {
                            "KODOSUMI_API": True
                        }
                    }
                }
            }
        }

        flows = extract_kodosumi_endpoints(spec, "my-app")

        assert len(flows) == 1
        assert flows[0].path == "/my-app/process"  # full path with app prefix

    def test_ignores_endpoints_without_marker(self):
        spec = {
            "paths": {
                "/health": {
                    "get": {
                        "summary": "Health check"
                    }
                },
                "/run": {
                    "post": {
                        "summary": "Run",
                        "x-kodosumi-api": True
                    }
                }
            }
        }

        flows = extract_kodosumi_endpoints(spec, "my-app")

        assert len(flows) == 1
        assert flows[0].path == "/my-app/run"  # full path with app prefix

    def test_ignores_non_http_methods(self):
        spec = {
            "paths": {
                "/run": {
                    "parameters": [{"name": "id"}],
                    "post": {
                        "summary": "Run",
                        "x-kodosumi-api": True
                    }
                }
            }
        }

        flows = extract_kodosumi_endpoints(spec, "my-app")

        assert len(flows) == 1
        assert flows[0].method == "POST"

    def test_extracts_author_and_organization(self):
        spec = {
            "paths": {
                "/run": {
                    "post": {
                        "summary": "Run",
                        "x-kodosumi-api": True,
                        "x-author": "alice@example.com",  # actual constant from const.py
                        "x-organization": "Acme"  # actual constant from const.py
                    }
                }
            }
        }

        flows = extract_kodosumi_endpoints(spec, "my-app")

        assert len(flows) == 1
        assert flows[0].author == "alice@example.com"
        assert flows[0].organization == "Acme"

    def test_handles_empty_paths(self):
        spec = {"paths": {}}
        flows = extract_kodosumi_endpoints(spec, "my-app")
        assert flows == []

    def test_handles_missing_paths(self):
        spec = {}
        flows = extract_kodosumi_endpoints(spec, "my-app")
        assert flows == []

    def test_extracts_multiple_methods(self):
        spec = {
            "paths": {
                "/resource": {
                    "get": {
                        "summary": "Get",
                        "x-kodosumi-api": True
                    },
                    "post": {
                        "summary": "Create",
                        "x-kodosumi-api": True
                    }
                }
            }
        }

        flows = extract_kodosumi_endpoints(spec, "my-app")

        assert len(flows) == 2
        methods = {f.method for f in flows}
        assert methods == {"GET", "POST"}


class TestStepRegisterFlows:
    """Tests for _step_register_flows()."""

    @pytest.mark.asyncio
    async def test_yields_step_start_message(self):
        with patch("kodosumi.service.expose.boot.fetch_openapi_spec") as mock_fetch, \
             patch("httpx.AsyncClient") as mock_client:
            mock_fetch.return_value = ({"paths": {}}, "http://localhost:8005/app-1/openapi.json", None)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = []
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            progress = BootProgress()
            messages = []
            async for msg in _step_register_flows(
                "http://localhost:8005", "http://localhost:3370", ["app-1"], None, progress
            ):
                messages.append(msg)

            step_start = [m for m in messages if m.msg_type == MessageType.STEP_START]
            assert len(step_start) == 1
            assert step_start[0].step == BootStep.REGISTER

    @pytest.mark.asyncio
    async def test_yields_step_end_with_summary(self):
        mock_spec = {
            "paths": {
                "/run": {"post": {"summary": "Run", "x-kodosumi-api": True}}
            }
        }

        with patch("kodosumi.service.expose.boot.fetch_openapi_spec") as mock_fetch, \
             patch("httpx.AsyncClient") as mock_client:
            mock_fetch.return_value = (mock_spec, "http://localhost:8005/app-1/openapi.json", None)

            # Mock successful registration
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [{"summary": "Run"}]
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            progress = BootProgress()
            messages = []
            async for msg in _step_register_flows(
                "http://localhost:8005", "http://localhost:3370", ["app-1"], None, progress
            ):
                messages.append(msg)

            step_end = [m for m in messages if m.msg_type == MessageType.STEP_END]
            assert len(step_end) == 1
            assert "1 registered" in step_end[0].message
            assert "1 discovered" in step_end[0].message

    @pytest.mark.asyncio
    async def test_handles_empty_app_list(self):
        progress = BootProgress()
        messages = []
        async for msg in _step_register_flows(
            "http://localhost:8005", "http://localhost:3370", [], None, progress
        ):
            messages.append(msg)

        warnings = [m for m in messages if m.msg_type == MessageType.WARNING]
        assert any("No running applications" in m.message for m in warnings)

    @pytest.mark.asyncio
    async def test_handles_openapi_not_available(self):
        """Test that discovery continues even if OpenAPI not available."""
        with patch("kodosumi.service.expose.boot.fetch_openapi_spec") as mock_fetch, \
             patch("httpx.AsyncClient") as mock_client:
            mock_fetch.return_value = (None, "http://localhost:8005/app-1/openapi.json", "404 Not Found")

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = []
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            progress = BootProgress()
            messages = []
            async for msg in _step_register_flows(
                "http://localhost:8005", "http://localhost:3370", ["app-1"], None, progress
            ):
                messages.append(msg)

            # Should have activity about OpenAPI not available
            activities = [m for m in messages if m.msg_type == MessageType.ACTIVITY]
            assert any("not available" in m.message.lower() for m in activities)

    @pytest.mark.asyncio
    async def test_info_when_no_kodosumi_endpoints(self):
        with patch("kodosumi.service.expose.boot.fetch_openapi_spec") as mock_fetch, \
             patch("httpx.AsyncClient") as mock_client:
            mock_fetch.return_value = ({
                "paths": {
                    "/health": {"get": {"summary": "Health"}}  # No marker
                }
            }, "http://localhost:8005/app-1/openapi.json", None)

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = []
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            progress = BootProgress()
            messages = []
            async for msg in _step_register_flows(
                "http://localhost:8005", "http://localhost:3370", ["app-1"], None, progress
            ):
                messages.append(msg)

            info_msgs = [m for m in messages if m.msg_type == MessageType.INFO]
            # Should mention x-kodosumi (the actual marker used by ServeAPI)
            assert any("x-kodosumi" in m.message for m in info_msgs)

    @pytest.mark.asyncio
    async def test_returns_discovered_flows_in_data(self):
        mock_spec = {
            "paths": {
                "/run": {"post": {"summary": "Run", "x-kodosumi-api": True}},
                "/query": {"get": {"summary": "Query", "x-kodosumi-api": True}}
            }
        }

        with patch("kodosumi.service.expose.boot.fetch_openapi_spec") as mock_fetch, \
             patch("httpx.AsyncClient") as mock_client:
            mock_fetch.return_value = (mock_spec, "http://localhost:8005/app-1/openapi.json", None)

            # Mock successful registration
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [{"summary": "Run"}, {"summary": "Query"}]
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            progress = BootProgress()
            messages = []
            async for msg in _step_register_flows(
                "http://localhost:8005", "http://localhost:3370", ["app-1"], None, progress
            ):
                messages.append(msg)

            step_end = [m for m in messages if m.msg_type == MessageType.STEP_END][0]
            assert step_end.data is not None
            assert "discovered_flows" in step_end.data
            assert len(step_end.data["discovered_flows"]) == 2

    @pytest.mark.asyncio
    async def test_processes_multiple_apps(self):
        mock_spec = {
            "paths": {
                "/run": {"post": {"summary": "Run", "x-kodosumi-api": True}}
            }
        }

        with patch("kodosumi.service.expose.boot.fetch_openapi_spec") as mock_fetch, \
             patch("httpx.AsyncClient") as mock_client:
            mock_fetch.return_value = (mock_spec, "http://localhost:8005/app/openapi.json", None)

            # Mock successful registration
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [{"summary": "Run"}, {"summary": "Run"}]
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            progress = BootProgress()
            messages = []
            async for msg in _step_register_flows(
                "http://localhost:8005", "http://localhost:3370", ["app-1", "app-2"], None, progress
            ):
                messages.append(msg)

            step_end = [m for m in messages if m.msg_type == MessageType.STEP_END][0]
            # 2 flows discovered (1 per app)
            assert len(step_end.data["discovered_flows"]) == 2
            # Check both apps represented
            apps = {f.app_name for f in step_end.data["discovered_flows"]}
            assert apps == {"app-1", "app-2"}

    @pytest.mark.asyncio
    async def test_calls_flow_register_with_routes_endpoint(self):
        """Test that POST /flow/register is called with /-/routes URL."""
        mock_spec = {
            "paths": {
                "/run": {"post": {"summary": "Run", "x-kodosumi-api": True}}
            }
        }

        with patch("kodosumi.service.expose.boot.fetch_openapi_spec") as mock_fetch, \
             patch("httpx.AsyncClient") as mock_client:
            mock_fetch.return_value = (mock_spec, "http://localhost:8005/my-app/openapi.json", None)

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [{"summary": "Run"}]
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            progress = BootProgress()
            messages = []
            async for msg in _step_register_flows(
                "http://localhost:8005", "http://localhost:3370", ["my-app"], {"auth": "token"}, progress
            ):
                messages.append(msg)

            # Verify POST was called to /flow/register with /-/routes URL
            mock_instance.post.assert_called_once()
            call_args = mock_instance.post.call_args
            assert call_args[0][0] == "http://localhost:3370/flow/register"
            # Should use /-/routes endpoint, not per-app openapi.json
            assert call_args[1]["json"] == {"url": "http://localhost:8005/-/routes"}


class TestStepRegisterFlowsIntegration:
    """Integration tests for the full register flows step."""

    @pytest.mark.asyncio
    async def test_mixed_apps_with_and_without_flows(self):
        """Test handling apps where some have flows and some don't."""

        async def mock_fetch(url, app_name):
            if app_name == "app-with-flows":
                return ({
                    "paths": {
                        "/run": {"post": {"summary": "Run", "x-kodosumi-api": True}}
                    }
                }, f"http://localhost:8005/{app_name}/openapi.json", None)
            elif app_name == "app-no-marker":
                return ({
                    "paths": {
                        "/health": {"get": {"summary": "Health"}}
                    }
                }, f"http://localhost:8005/{app_name}/openapi.json", None)
            else:
                return (None, f"http://localhost:8005/{app_name}/openapi.json", "404 Not Found")

        with patch("kodosumi.service.expose.boot.fetch_openapi_spec", side_effect=mock_fetch), \
             patch("httpx.AsyncClient") as mock_client:
            # Mock successful registration via /-/routes
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [{"summary": "Run"}]
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            progress = BootProgress()
            messages = []
            async for msg in _step_register_flows(
                "http://localhost:8005",
                "http://localhost:3370",
                ["app-with-flows", "app-no-marker", "app-no-spec"],
                None,
                progress
            ):
                messages.append(msg)

            # Should have discovered 1 flow (from app-with-flows)
            step_end = [m for m in messages if m.msg_type == MessageType.STEP_END][0]
            assert len(step_end.data["discovered_flows"]) == 1
            assert step_end.data["discovered_flows"][0].app_name == "app-with-flows"

            # Info message for app without markers
            info_msgs = [m for m in messages if m.msg_type == MessageType.INFO]
            assert any("x-kodosumi" in m.message for m in info_msgs)
