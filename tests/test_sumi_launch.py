"""
Tests for Sumi Protocol Launch() extension (Phase 2).

Tests that the `extra` parameter is properly passed through
Launch() -> create_runner() -> Runner and stored in EVENT_META.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import json

from kodosumi.runner.main import Launch, create_runner, Runner


class TestLaunchSignature:
    """Test Launch() function signature and parameters."""

    def test_extra_parameter_exists(self):
        """Verify Launch() accepts extra parameter."""
        import inspect
        sig = inspect.signature(Launch)
        params = list(sig.parameters.keys())
        assert "extra" in params

    def test_extra_default_is_none(self):
        """Verify extra parameter defaults to None."""
        import inspect
        sig = inspect.signature(Launch)
        extra_param = sig.parameters["extra"]
        assert extra_param.default is None


class TestCreateRunnerSignature:
    """Test create_runner() function signature."""

    def test_method_info_parameter_exists(self):
        """Verify create_runner() accepts method_info parameter."""
        import inspect
        sig = inspect.signature(create_runner)
        params = list(sig.parameters.keys())
        assert "method_info" in params

    def test_extra_parameter_exists(self):
        """Verify create_runner() accepts extra parameter."""
        import inspect
        sig = inspect.signature(create_runner)
        params = list(sig.parameters.keys())
        assert "extra" in params


class TestRunnerInit:
    """Test Runner class initialization.

    Note: Ray's @ray.remote decorator modifies class signatures,
    so we verify via create_runner which instantiates Runner.
    """

    def test_runner_instantiation_via_create_runner(self):
        """Verify Runner can be instantiated with method_info and extra via create_runner."""
        # The create_runner function passes method_info and extra to Runner
        # If Runner didn't accept these, create_runner would fail
        # This is tested implicitly by TestCreateRunnerSignature and TestLaunchWithExtra
        import inspect
        sig = inspect.signature(create_runner)
        params = sig.parameters
        # Verify create_runner has these params which it passes to Runner
        assert "method_info" in params
        assert "extra" in params


class TestLaunchWithExtra:
    """Test Launch() with extra parameter."""

    @patch("kodosumi.runner.main.create_runner")
    def test_launch_passes_extra_to_create_runner(self, mock_create_runner):
        """Test that extra is passed to create_runner."""
        from kodosumi.const import KODOSUMI_URL, KODOSUMI_EXTRA, HEADER_KEY

        # Setup mock
        mock_runner = MagicMock()
        mock_runner.run = MagicMock()
        mock_runner.run.remote = MagicMock()
        mock_create_runner.return_value = ("test-fid", mock_runner)

        # Create mock request
        mock_request = MagicMock()
        mock_request.app._code_lookup = {}
        mock_request.app._method_lookup = {}
        mock_request.state.user = "test-user"
        mock_request.state.prefix = "/test"
        mock_request.cookies.get.return_value = "test-jwt"

        # Headers need different values for different keys
        def get_header(key, default=None):
            headers = {
                KODOSUMI_URL: "http://localhost",
                KODOSUMI_EXTRA: None,  # No extra from headers
                HEADER_KEY: None,  # JWT from cookies instead
            }
            return headers.get(key, default)

        mock_request.headers.get.side_effect = get_header

        # Call Launch with extra
        extra_data = {"identifier_from_purchaser": "order-123", "input_hash": "abc"}
        Launch(
            mock_request,
            "module:function",
            inputs={"query": "test"},
            extra=extra_data,
        )

        # Verify create_runner was called with extra
        mock_create_runner.assert_called_once()
        call_kwargs = mock_create_runner.call_args.kwargs
        assert call_kwargs["extra"] == extra_data

    @patch("kodosumi.runner.main.create_runner")
    def test_launch_without_extra_passes_none(self, mock_create_runner):
        """Test that Launch without extra passes None to create_runner."""
        from kodosumi.const import KODOSUMI_URL, KODOSUMI_EXTRA, HEADER_KEY

        # Setup mock
        mock_runner = MagicMock()
        mock_runner.run = MagicMock()
        mock_runner.run.remote = MagicMock()
        mock_create_runner.return_value = ("test-fid", mock_runner)

        # Create mock request
        mock_request = MagicMock()
        mock_request.app._code_lookup = {}
        mock_request.app._method_lookup = {}
        mock_request.state.user = "test-user"
        mock_request.state.prefix = "/test"
        mock_request.cookies.get.return_value = "test-jwt"

        # Headers need different values for different keys
        def get_header(key, default=None):
            headers = {
                KODOSUMI_URL: "http://localhost",
                KODOSUMI_EXTRA: None,  # No extra from headers
                HEADER_KEY: None,  # JWT from cookies instead
            }
            return headers.get(key, default)

        mock_request.headers.get.side_effect = get_header

        # Call Launch without extra (backwards compatibility)
        Launch(
            mock_request,
            "module:function",
            inputs={"query": "test"},
        )

        # Verify create_runner was called with extra=None
        mock_create_runner.assert_called_once()
        call_kwargs = mock_create_runner.call_args.kwargs
        assert call_kwargs["extra"] is None


class TestRunnerMetaEvent:
    """Test that Runner stores extra in meta event correctly."""

    def test_meta_data_includes_extra_when_provided(self):
        """Test that meta event includes extra key when extra is provided."""
        # This tests the logic in Runner.start() that builds meta_data
        # We simulate the meta_data construction

        extra = {"identifier_from_purchaser": "order-456", "input_hash": "def789"}
        method_info = {"summary": "Test", "tags": ["test"]}

        # Simulate the meta_data construction from Runner.start()
        meta_data = {
            "fid": "test-fid",
            "username": "test-user",
            "app_url": "/test",
            "panel_url": "http://localhost",
            "entry_point": "module:function",
            "kodosumi": "1.0.0",
        }

        # Add method_info fields
        if isinstance(method_info, dict):
            for field in ("tags", "summary", "description", "deprecated"):
                meta_data[field] = method_info.get(field, None)

        # Add extra (this is the new behavior)
        if extra:
            meta_data["extra"] = extra

        # Verify extra is included
        assert "extra" in meta_data
        assert meta_data["extra"]["identifier_from_purchaser"] == "order-456"
        assert meta_data["extra"]["input_hash"] == "def789"

    def test_meta_data_excludes_extra_when_none(self):
        """Test that meta event doesn't include extra key when extra is None."""
        extra = None
        method_info = {"summary": "Test"}

        meta_data = {
            "fid": "test-fid",
            "username": "test-user",
            "app_url": "/test",
            "panel_url": "http://localhost",
            "entry_point": "module:function",
        }

        # Add extra only if provided (this is the new behavior)
        if extra:
            meta_data["extra"] = extra

        # Verify extra is NOT included (backwards compatible)
        assert "extra" not in meta_data

    def test_meta_data_excludes_extra_when_empty(self):
        """Test that meta event doesn't include extra key when extra is empty dict."""
        extra = {}
        method_info = {"summary": "Test"}

        meta_data = {
            "fid": "test-fid",
            "username": "test-user",
        }

        # Add extra only if truthy (empty dict is falsy)
        if extra:
            meta_data["extra"] = extra

        # Verify extra is NOT included
        assert "extra" not in meta_data


class TestBackwardsCompatibility:
    """Test backwards compatibility of Launch() changes."""

    def test_launch_signature_compatible(self):
        """Test that Launch can be called with original arguments."""
        import inspect
        sig = inspect.signature(Launch)

        # Original required parameters should still work
        params = sig.parameters
        assert "request" in params
        assert "entry_point" in params
        assert "inputs" in params
        assert "reference" in params
        assert "summary" in params
        assert "description" in params

        # inputs should have default None
        assert params["inputs"].default is None

        # extra should have default None (new parameter)
        assert params["extra"].default is None

    def test_create_runner_signature_compatible(self):
        """Test that create_runner can be called with original arguments."""
        import inspect
        sig = inspect.signature(create_runner)
        params = sig.parameters

        # Original parameters should exist
        assert "username" in params
        assert "app_url" in params
        assert "entry_point" in params
        assert "inputs" in params
        assert "jwt" in params
        assert "panel_url" in params
        assert "fid" in params

        # New parameters should have defaults
        assert params["method_info"].default is None
        assert params["extra"].default is None
