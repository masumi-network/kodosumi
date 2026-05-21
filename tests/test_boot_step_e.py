"""
Tests for Boot Process Step E: Update Meta (Database Update)

Tests the following functions:
- fetch_registered_flows() - Fetch flows from GET /flow
- get_expose_name_from_flow_url() - Extract expose name from URL
- get_existing_meta() - Get existing meta from database
- merge_flow_with_meta() - Merge flow with existing meta
- _step_update_meta() - Full update meta step generator
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import yaml
import time

from kodosumi.service.expose.boot import (
    FlowStatus,
    DiscoveredFlow,
    fetch_registered_flows,
    get_expose_name_from_base_url,
    get_path_from_base_url,
    get_existing_meta,
    merge_flow_with_meta,
    _step_update_meta,
    BootProgress,
    BootStep,
    MessageType,
)
from kodosumi.service.expose.models import ExposeMeta, create_meta_template, slugify_summary


# =============================================================================
# get_expose_name_from_base_url Tests
# =============================================================================

class TestGetExposeNameFromBaseUrl:
    """Tests for get_expose_name_from_base_url function."""

    def test_extracts_from_full_url(self):
        """Test extracting expose name from full base_url."""
        assert get_expose_name_from_base_url("http://localhost:8005/my-app/") == "my-app"

    def test_extracts_from_url_with_endpoint(self):
        """Test extracting expose name with endpoint path."""
        assert get_expose_name_from_base_url("http://localhost:8005/test-agent/run") == "test-agent"

    def test_extracts_from_url_with_nested_path(self):
        """Test extracting from URL with nested endpoint path."""
        assert get_expose_name_from_base_url("http://127.0.0.1:8005/my-service/v1/execute") == "my-service"

    def test_handles_https(self):
        """Test handling HTTPS URLs."""
        assert get_expose_name_from_base_url("https://example.com/my-app/run") == "my-app"

    def test_returns_none_for_empty(self):
        """Test returns None for empty string."""
        assert get_expose_name_from_base_url("") is None

    def test_returns_none_for_root_only(self):
        """Test returns None for root path only."""
        assert get_expose_name_from_base_url("http://localhost:8005/") is None


class TestGetPathFromBaseUrl:
    """Tests for get_path_from_base_url function."""

    def test_extracts_path(self):
        """Test extracting path from base_url."""
        assert get_path_from_base_url("http://localhost:8005/my-app/run") == "/my-app/run"

    def test_extracts_path_with_trailing_slash(self):
        """Test extracting path with trailing slash."""
        assert get_path_from_base_url("http://localhost:8005/test/") == "/test/"

    def test_returns_root_for_empty(self):
        """Test returns root for invalid URL."""
        assert get_path_from_base_url("") == "/"


# =============================================================================
# slugify_summary Tests
# =============================================================================

class TestSlugifySummary:
    """Tests for slugify_summary function."""

    def test_basic_conversion(self):
        """Test basic lowercase conversion."""
        assert slugify_summary("Hello World") == "hello-world"

    def test_uppercase_to_lowercase(self):
        """Test uppercase is converted to lowercase."""
        assert slugify_summary("RUN AGENT") == "run-agent"

    def test_special_characters_replaced(self):
        """Test special characters are replaced with hyphens."""
        assert slugify_summary("Run Agent (v2.0)") == "run-agent-v2-0"

    def test_consecutive_hyphens_collapsed(self):
        """Test consecutive hyphens are collapsed to single hyphen."""
        assert slugify_summary("Run  --  Agent") == "run-agent"

    def test_leading_trailing_hyphens_stripped(self):
        """Test leading and trailing hyphens are stripped."""
        assert slugify_summary("--Run Agent--") == "run-agent"

    def test_empty_returns_default(self):
        """Test empty string returns default."""
        assert slugify_summary("") == "unnamed-flow"

    def test_none_returns_default(self):
        """Test None returns default."""
        assert slugify_summary(None) == "unnamed-flow"

    def test_whitespace_only_returns_default(self):
        """Test whitespace only returns default."""
        assert slugify_summary("   ") == "unnamed-flow"

    def test_preserves_numbers(self):
        """Test numbers are preserved."""
        assert slugify_summary("Agent v2.1.3") == "agent-v2-1-3"

    def test_unicode_removed(self):
        """Test unicode characters are replaced."""
        assert slugify_summary("Run Ägent") == "run-gent"


# =============================================================================
# fetch_registered_flows Tests
# =============================================================================

class TestFetchRegisteredFlows:
    """Tests for fetch_registered_flows function."""

    @pytest.mark.asyncio
    async def test_fetches_flows_successfully(self):
        """Test successfully fetching flows with paginated response."""
        import httpx

        # API returns paginated response: {"items": [...], "offset": ...}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "items": [
                {"uid": "1", "base_url": "http://localhost:8005/app/run", "method": "POST", "summary": "Run"},
                {"uid": "2", "base_url": "http://localhost:8005/app/query", "method": "GET", "summary": "Query"},
            ],
            "offset": None  # No more pages
        }

        with patch.object(httpx.AsyncClient, "__aenter__", return_value=MagicMock(get=AsyncMock(return_value=mock_response))):
            with patch.object(httpx.AsyncClient, "__aexit__", return_value=None):
                result = await fetch_registered_flows("http://localhost:3370", {"token": "abc"})

        assert len(result) == 2
        assert result[0]["base_url"] == "http://localhost:8005/app/run"

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        """Test returns empty list on error."""
        import httpx

        with patch.object(httpx.AsyncClient, "__aenter__", side_effect=Exception("Connection error")):
            result = await fetch_registered_flows("http://localhost:3370", None)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_200(self):
        """Test returns empty list on non-200 status."""
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch.object(httpx.AsyncClient, "__aenter__", return_value=MagicMock(get=AsyncMock(return_value=mock_response))):
            with patch.object(httpx.AsyncClient, "__aexit__", return_value=None):
                result = await fetch_registered_flows("http://localhost:3370", None)

        assert result == []


# =============================================================================
# get_existing_meta Tests
# =============================================================================

class TestGetExistingMeta:
    """Tests for get_existing_meta function."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_expose(self):
        """Test returns empty list when expose doesn't exist."""
        with patch("kodosumi.service.expose.boot.db.get_expose") as mock_get:
            mock_get.return_value = None

            result = await get_existing_meta("nonexistent")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_meta(self):
        """Test returns empty list when expose has no meta."""
        with patch("kodosumi.service.expose.boot.db.get_expose") as mock_get:
            mock_get.return_value = {"name": "test", "meta": None}

            result = await get_existing_meta("test")

        assert result == []

    @pytest.mark.asyncio
    async def test_parses_existing_meta(self):
        """Test parses existing meta from YAML."""
        meta_yaml = """
- url: /test/run
  data: "summary: Run"
  state: alive
  heartbeat: 1700000000.0
"""
        with patch("kodosumi.service.expose.boot.db.get_expose") as mock_get:
            mock_get.return_value = {"name": "test", "meta": meta_yaml}

            result = await get_existing_meta("test")

        assert len(result) == 1
        assert result[0].url == "/test/run"
        assert result[0].state == "alive"


# =============================================================================
# merge_flow_with_meta Tests
# =============================================================================

class TestMergeFlowWithMeta:
    """Tests for merge_flow_with_meta function."""

    def test_creates_new_meta_when_none_exists(self):
        """Test creates new meta using template when no existing meta."""
        flow = {
            "base_url": "http://localhost:8005/app/run",
            "summary": "Run Agent",
            "description": "Execute the workflow",
            "author": "dev@example.com",
            "organization": "MyOrg",
            "tags": ["agent", "workflow"],
        }

        result = merge_flow_with_meta(flow, None, "alive", 1700000000.0)

        assert result.url == "/app/run"
        assert result.state == "alive"
        assert result.heartbeat == 1700000000.0
        # Should use template format
        assert "# Flow metadata configuration" in result.data

    def test_preserves_existing_meta_data(self):
        """Test preserves existing meta data fields."""
        flow = {
            "base_url": "http://localhost:8005/app/run",
            "summary": "New Summary",
            "description": "New description",
        }

        existing = ExposeMeta(
            url="/app/run",
            data="# User edited data\ndisplay: My Custom Name",
            state="dead",
            heartbeat=1600000000.0,
        )

        result = merge_flow_with_meta(flow, existing, "alive", 1700000000.0)

        # Should preserve user data
        assert "# User edited data" in result.data
        # But update state and heartbeat
        assert result.state == "alive"
        assert result.heartbeat == 1700000000.0

    def test_updates_state_only(self):
        """Test only updates state and heartbeat."""
        flow = {"base_url": "http://localhost:8005/app/run"}

        existing = ExposeMeta(
            url="/app/run",
            data="description: My description",
            state="dead",
            heartbeat=1600000000.0,
        )

        result = merge_flow_with_meta(flow, existing, "alive", 1700000000.0)

        assert result.state == "alive"
        assert result.heartbeat == 1700000000.0
        assert result.data == "description: My description"


# =============================================================================
# _step_update_meta Tests
# =============================================================================

class TestStepUpdateMeta:
    """Tests for _step_update_meta generator function."""

    @pytest.mark.asyncio
    async def test_step_start_message(self):
        """Test step emits start message."""
        progress = BootProgress()
        messages = []

        with patch("kodosumi.service.expose.boot.fetch_registered_flows") as mock_fetch:
            mock_fetch.return_value = []

            async for msg in _step_update_meta("http://localhost:3370", None, {}, progress):
                messages.append(msg)

        assert messages[0].msg_type == MessageType.STEP_START
        assert messages[0].step == BootStep.UPDATE

    @pytest.mark.asyncio
    async def test_warns_when_no_flows(self):
        """Test emits warning when no flows returned."""
        progress = BootProgress()
        messages = []

        with patch("kodosumi.service.expose.boot.fetch_registered_flows") as mock_fetch:
            mock_fetch.return_value = []

            async for msg in _step_update_meta("http://localhost:3370", None, {}, progress):
                messages.append(msg)

        warnings = [m for m in messages if m.msg_type == MessageType.WARNING]
        assert len(warnings) >= 1
        assert any("No flows" in m.message for m in warnings)

    @pytest.mark.asyncio
    async def test_processes_flows(self):
        """Test processes flows and saves meta."""
        mock_flows = [
            {
                "base_url": "http://localhost:8005/test-app/run",
                "summary": "Run",
                "description": "Execute",
                "author": None,
                "organization": None,
                "tags": [],
            }
        ]

        flow = DiscoveredFlow(
            app_name="test-app",
            path="/test-app/run",
            method="POST",
            summary="Run",
            description="",
            tags=[],
        )

        flow_statuses = {
            "test-app": [
                FlowStatus(
                    flow=flow,
                    state="alive",
                    response_code=200,
                    checked_at=time.time(),
                )
            ]
        }

        progress = BootProgress()
        messages = []

        with patch("kodosumi.service.expose.boot.fetch_registered_flows") as mock_fetch, \
             patch("kodosumi.service.expose.boot.get_existing_meta") as mock_existing, \
             patch("kodosumi.service.expose.boot.db.update_expose_meta") as mock_update:

            mock_fetch.return_value = mock_flows
            mock_existing.return_value = []
            mock_update.return_value = None

            async for msg in _step_update_meta("http://localhost:3370", None, flow_statuses, progress):
                messages.append(msg)

        # Check message flow
        types = [m.msg_type for m in messages]
        assert MessageType.STEP_START in types
        assert MessageType.ACTIVITY in types
        assert MessageType.RESULT in types
        assert MessageType.STEP_END in types

        # Check db was called
        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_preserves_existing_meta(self):
        """Test preserves existing meta data fields."""
        mock_flows = [
            {
                "base_url": "http://localhost:8005/test-app/run",
                "summary": "New Summary",
                "description": "New description",
                "author": None,
                "organization": None,
                "tags": [],
            }
        ]

        flow = DiscoveredFlow(
            app_name="test-app",
            path="/test-app/run",
            method="POST",
            summary="Run",
            description="",
            tags=[],
        )

        flow_statuses = {
            "test-app": [
                FlowStatus(
                    flow=flow,
                    state="alive",
                    response_code=200,
                    checked_at=time.time(),
                )
            ]
        }

        existing_meta = ExposeMeta(
            url="/test-app/run",
            data="# User edited\ndescription: My Description",
            state="dead",
            heartbeat=1600000000.0,
        )

        progress = BootProgress()
        saved_yaml = None

        async def capture_save(name, yaml_str):
            nonlocal saved_yaml
            saved_yaml = yaml_str

        with patch("kodosumi.service.expose.boot.fetch_registered_flows") as mock_fetch, \
             patch("kodosumi.service.expose.boot.get_existing_meta") as mock_existing, \
             patch("kodosumi.service.expose.boot.db.update_expose_meta", side_effect=capture_save):

            mock_fetch.return_value = mock_flows
            mock_existing.return_value = [existing_meta]

            async for msg in _step_update_meta("http://localhost:3370", None, flow_statuses, progress):
                pass

        # Parse saved meta
        parsed = yaml.safe_load(saved_yaml)
        assert len(parsed) == 1
        # Should preserve user data
        assert "# User edited" in parsed[0]["data"]
        # But update state
        assert parsed[0]["state"] == "alive"

    @pytest.mark.asyncio
    async def test_creates_new_meta_from_template(self):
        """Test creates new meta using template for new flows."""
        mock_flows = [
            {
                "base_url": "http://localhost:8005/test-app/new-endpoint",
                "summary": "New Endpoint",
                "description": "A brand new endpoint",
                "author": "dev@example.com",
                "organization": "MyOrg",
                "tags": ["new", "api"],
            }
        ]

        flow = DiscoveredFlow(
            app_name="test-app",
            path="/test-app/new-endpoint",
            method="POST",
            summary="New Endpoint",
            description="A brand new endpoint",
            tags=["new", "api"],
        )

        flow_statuses = {
            "test-app": [
                FlowStatus(
                    flow=flow,
                    state="alive",
                    response_code=200,
                    checked_at=time.time(),
                )
            ]
        }

        progress = BootProgress()
        saved_yaml = None

        async def capture_save(name, yaml_str):
            nonlocal saved_yaml
            saved_yaml = yaml_str

        with patch("kodosumi.service.expose.boot.fetch_registered_flows") as mock_fetch, \
             patch("kodosumi.service.expose.boot.get_existing_meta") as mock_existing, \
             patch("kodosumi.service.expose.boot.db.update_expose_meta", side_effect=capture_save):

            mock_fetch.return_value = mock_flows
            mock_existing.return_value = []  # No existing meta

            async for msg in _step_update_meta("http://localhost:3370", None, flow_statuses, progress):
                pass

        # Parse saved meta
        parsed = yaml.safe_load(saved_yaml)
        assert len(parsed) == 1
        # Should use template format
        assert "# Flow metadata configuration" in parsed[0]["data"]
        assert "New Endpoint" in parsed[0]["data"]

    @pytest.mark.asyncio
    async def test_counts_new_and_preserved(self):
        """Test counts new and preserved flows in summary."""
        mock_flows = [
            {"base_url": "http://localhost:8005/app/existing", "summary": "Existing", "description": "", "author": None, "organization": None, "tags": []},
            {"base_url": "http://localhost:8005/app/new", "summary": "New", "description": "", "author": None, "organization": None, "tags": []},
        ]

        existing_meta = ExposeMeta(
            url="/app/existing",
            name="existing",
            data="summary: Existing",
            state="dead",
            heartbeat=1600000000.0,
        )

        flows = [
            DiscoveredFlow(app_name="app", path="/app/existing", method="POST", summary="", description="", tags=[]),
            DiscoveredFlow(app_name="app", path="/app/new", method="POST", summary="", description="", tags=[]),
        ]

        flow_statuses = {
            "app": [
                FlowStatus(flow=flows[0], state="alive", response_code=200, checked_at=time.time()),
                FlowStatus(flow=flows[1], state="alive", response_code=200, checked_at=time.time()),
            ]
        }

        progress = BootProgress()
        messages = []

        with patch("kodosumi.service.expose.boot.fetch_registered_flows") as mock_fetch, \
             patch("kodosumi.service.expose.boot.get_existing_meta") as mock_existing, \
             patch("kodosumi.service.expose.boot.db.update_expose_meta"):

            mock_fetch.return_value = mock_flows
            mock_existing.return_value = [existing_meta]

            async for msg in _step_update_meta("http://localhost:3370", None, flow_statuses, progress):
                messages.append(msg)

        end_msg = next(m for m in messages if m.msg_type == MessageType.STEP_END)
        assert "1 new" in end_msg.message
        assert "1 preserved" in end_msg.message

    @pytest.mark.asyncio
    async def test_handles_db_error(self):
        """Test handles database errors gracefully."""
        mock_flows = [
            {"base_url": "http://localhost:8005/app/run", "summary": "Run", "description": "", "author": None, "organization": None, "tags": []}
        ]

        flow = DiscoveredFlow(
            app_name="app",
            path="/app/run",
            method="POST",
            summary="Run",
            description="",
            tags=[],
        )

        flow_statuses = {
            "app": [FlowStatus(flow=flow, state="alive", response_code=200, checked_at=time.time())]
        }

        progress = BootProgress()
        messages = []

        with patch("kodosumi.service.expose.boot.fetch_registered_flows") as mock_fetch, \
             patch("kodosumi.service.expose.boot.get_existing_meta") as mock_existing, \
             patch("kodosumi.service.expose.boot.db.update_expose_meta") as mock_update:

            mock_fetch.return_value = mock_flows
            mock_existing.return_value = []
            mock_update.side_effect = Exception("Database error")

            async for msg in _step_update_meta("http://localhost:3370", None, flow_statuses, progress):
                messages.append(msg)

        warnings = [m for m in messages if m.msg_type == MessageType.WARNING]
        assert len(warnings) >= 1
        assert any("Failed to update meta" in m.message for m in warnings)


# =============================================================================
# Integration Tests
# =============================================================================

class TestUpdateMetaIntegration:
    """Integration tests for metadata update."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_merge(self):
        """Test full pipeline merging new and existing flows."""
        mock_flows = [
            {"base_url": "http://localhost:8005/agent/run", "summary": "Run Agent", "description": "Execute agent", "author": "dev@co.com", "organization": "MyCo", "tags": ["agent"]},
            {"base_url": "http://localhost:8005/agent/status", "summary": "Status", "description": "Check status", "author": None, "organization": None, "tags": []},
        ]

        # One existing, one new
        existing_meta = ExposeMeta(
            url="/agent/run",
            data="# Custom data\ndescription: My custom description",
            state="dead",
            heartbeat=1600000000.0,
        )

        flows = [
            DiscoveredFlow(app_name="agent", path="/agent/run", method="POST", summary="Run Agent", description="Execute agent", tags=["agent"], author="dev@co.com"),
            DiscoveredFlow(app_name="agent", path="/agent/status", method="GET", summary="Status", description="Check status", tags=[]),
        ]

        flow_statuses = {
            "agent": [
                FlowStatus(flow=flows[0], state="alive", response_code=200, checked_at=1700000000.0),
                FlowStatus(flow=flows[1], state="alive", response_code=200, checked_at=1700000000.0),
            ]
        }

        saved_data = {}

        async def capture_save(name, yaml_str):
            saved_data[name] = yaml_str

        with patch("kodosumi.service.expose.boot.fetch_registered_flows") as mock_fetch, \
             patch("kodosumi.service.expose.boot.get_existing_meta") as mock_existing, \
             patch("kodosumi.service.expose.boot.db.update_expose_meta", side_effect=capture_save):

            mock_fetch.return_value = mock_flows
            mock_existing.return_value = [existing_meta]

            async for _ in _step_update_meta("http://localhost:3370", None, flow_statuses, BootProgress()):
                pass

        # Parse saved YAML
        assert "agent" in saved_data
        parsed = yaml.safe_load(saved_data["agent"])
        assert len(parsed) == 2

        # Check existing entry was preserved
        run_entry = next(e for e in parsed if e["url"] == "/agent/run")
        assert "# Custom data" in run_entry["data"]  # Preserved
        assert run_entry["state"] == "alive"  # Updated
        assert run_entry["heartbeat"] == 1700000000.0  # Updated

        # Check new entry was created from template
        status_entry = next(e for e in parsed if e["url"] == "/agent/status")
        assert "# Flow metadata configuration" in status_entry["data"]  # Template
        assert status_entry["state"] == "alive"
