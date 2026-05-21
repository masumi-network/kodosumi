"""
Tests for Boot Process Step A: Deploy

Tests the following functions:
- load_serve_config() - Load and parse serve_config.yaml
- parse_bootstrap() - Parse expose bootstrap into Ray Serve app config
- run_serve_deploy() - Run 'serve deploy' command
- _step_deploy() - Full deploy step generator
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import yaml

from kodosumi.service.expose.boot import (
    load_serve_config,
    parse_bootstrap,
    run_serve_deploy,
    _step_deploy,
    BootStep,
    MessageType,
    BootProgress,
    DEFAULT_SERVE_CONFIG,
    RAY_SERVE_CONFIG,
)


class TestLoadServeConfig:
    """Tests for load_serve_config()"""

    def test_creates_default_when_missing(self, tmp_path):
        """Should create default config if file doesn't exist."""
        config_path = tmp_path / "serve_config.yaml"

        config = load_serve_config(str(config_path))

        assert config_path.exists()
        assert "proxy_location" in config
        assert "http_options" in config
        assert "applications" in config
        assert config["applications"] == []

    def test_parses_existing_config(self, tmp_path):
        """Should parse existing config file."""
        config_path = tmp_path / "serve_config.yaml"
        existing_config = {
            "proxy_location": "HeadOnly",
            "http_options": {"host": "127.0.0.1", "port": 9000},
            "applications": [{"name": "existing-app"}]
        }
        with open(config_path, "w") as f:
            yaml.dump(existing_config, f)

        config = load_serve_config(str(config_path))

        assert config["proxy_location"] == "HeadOnly"
        assert config["http_options"]["port"] == 9000
        assert len(config["applications"]) == 1

    def test_adds_applications_key_if_missing(self, tmp_path):
        """Should add empty applications list if not present."""
        config_path = tmp_path / "serve_config.yaml"
        minimal_config = {"proxy_location": "EveryNode"}
        with open(config_path, "w") as f:
            yaml.dump(minimal_config, f)

        config = load_serve_config(str(config_path))

        assert "applications" in config
        assert config["applications"] == []

    def test_creates_parent_directories(self, tmp_path):
        """Should create parent directories if they don't exist."""
        config_path = tmp_path / "nested" / "dir" / "serve_config.yaml"

        config = load_serve_config(str(config_path))

        assert config_path.exists()
        assert "applications" in config


class TestParseBootstrap:
    """Tests for parse_bootstrap()"""

    def test_parses_basic_bootstrap(self):
        """Should parse basic bootstrap with import_path."""
        bootstrap = "import_path: mymodule:app"

        result = parse_bootstrap(bootstrap, "test-app")

        assert result["import_path"] == "mymodule:app"
        assert result["name"] == "test-app"
        assert result["route_prefix"] == "/test-app"

    def test_parses_bootstrap_with_runtime_env(self):
        """Should parse bootstrap with runtime_env."""
        bootstrap = """
import_path: agents.demo:app
runtime_env:
  pip:
    - openai
    - pydantic
  working_dir: "."
"""

        result = parse_bootstrap(bootstrap, "demo-agent")

        assert result["import_path"] == "agents.demo:app"
        assert result["name"] == "demo-agent"
        assert result["route_prefix"] == "/demo-agent"
        assert "runtime_env" in result
        assert "openai" in result["runtime_env"]["pip"]

    def test_raises_on_empty_bootstrap(self):
        """Should raise ValueError for empty bootstrap."""
        with pytest.raises(ValueError, match="Empty bootstrap"):
            parse_bootstrap("", "test-app")

        with pytest.raises(ValueError, match="Empty bootstrap"):
            parse_bootstrap("   ", "test-app")

    def test_raises_on_invalid_yaml(self):
        """Should raise ValueError for invalid YAML."""
        with pytest.raises(ValueError, match="Invalid YAML"):
            parse_bootstrap("{{invalid yaml", "test-app")

    def test_raises_on_non_dict_yaml(self):
        """Should raise ValueError if YAML is not a dict."""
        with pytest.raises(ValueError, match="must be a YAML dict"):
            parse_bootstrap("- item1\n- item2", "test-app")

    def test_raises_on_missing_import_path(self):
        """Should raise ValueError if import_path is missing."""
        with pytest.raises(ValueError, match="missing 'import_path'"):
            parse_bootstrap("runtime_env:\n  pip: [requests]", "test-app")

    def test_overrides_name_and_route(self):
        """Should override name and route_prefix even if present in bootstrap."""
        bootstrap = """
import_path: mymodule:app
name: original-name
route_prefix: /original-route
"""

        result = parse_bootstrap(bootstrap, "new-name")

        assert result["name"] == "new-name"
        assert result["route_prefix"] == "/new-name"


class TestRunServeDeploy:
    """Tests for run_serve_deploy()"""

    @pytest.mark.asyncio
    async def test_returns_success_on_zero_exit(self):
        """Should return success when serve deploy exits with 0."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"Deployed successfully", b"")
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            returncode, stdout, stderr = await run_serve_deploy("/tmp/config.yaml")

            assert returncode == 0
            assert "Deployed successfully" in stdout
            assert stderr == ""

    @pytest.mark.asyncio
    async def test_returns_error_on_nonzero_exit(self):
        """Should return error info when serve deploy fails."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"", b"Error: invalid config")
            mock_process.returncode = 1
            mock_exec.return_value = mock_process

            returncode, stdout, stderr = await run_serve_deploy("/tmp/config.yaml")

            assert returncode == 1
            assert "invalid config" in stderr

    @pytest.mark.asyncio
    async def test_calls_serve_with_config_path(self):
        """Should call 'serve deploy <config_path>'."""
        with patch("kodosumi.service.expose.boot.asyncio.create_subprocess_exec") as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"", b"")
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            await run_serve_deploy("/path/to/config.yaml")

            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert Path(args[0]).name == "serve"
            assert args[1] == "deploy"
            assert args[2] == "/path/to/config.yaml"


class TestStepDeploy:
    """Tests for _step_deploy()"""

    @pytest.mark.asyncio
    async def test_yields_step_start_message(self):
        """Should yield STEP_START message."""
        with patch("kodosumi.service.expose.boot.db") as mock_db:
            mock_db.init_database = AsyncMock()
            mock_db.get_all_exposes = AsyncMock(return_value=[])

            progress = BootProgress()
            messages = []
            async for msg in _step_deploy(progress):
                messages.append(msg)

            step_start = [m for m in messages if m.msg_type == MessageType.STEP_START]
            assert len(step_start) == 1
            assert step_start[0].step == BootStep.DEPLOY

    @pytest.mark.asyncio
    async def test_warns_when_no_exposes(self):
        """Should warn when no enabled exposes with bootstrap."""
        with patch("kodosumi.service.expose.boot.db") as mock_db:
            mock_db.init_database = AsyncMock()
            mock_db.get_all_exposes = AsyncMock(return_value=[])

            progress = BootProgress()
            messages = []
            async for msg in _step_deploy(progress):
                messages.append(msg)

            warnings = [m for m in messages if m.msg_type == MessageType.WARNING]
            assert any("No enabled exposes" in m.message for m in warnings)

    @pytest.mark.asyncio
    async def test_skips_disabled_exposes(self):
        """Should skip disabled exposes."""
        with patch("kodosumi.service.expose.boot.db") as mock_db:
            mock_db.init_database = AsyncMock()
            mock_db.get_all_exposes = AsyncMock(return_value=[
                {"name": "disabled-app", "enabled": False, "bootstrap": "import_path: x:y"}
            ])

            progress = BootProgress()
            messages = []
            async for msg in _step_deploy(progress):
                messages.append(msg)

            # Should not have any activity for disabled app
            activities = [m for m in messages if m.msg_type == MessageType.ACTIVITY and m.target == "disabled-app"]
            assert len(activities) == 0

    @pytest.mark.asyncio
    async def test_skips_exposes_without_bootstrap(self):
        """Should skip exposes without bootstrap."""
        with patch("kodosumi.service.expose.boot.db") as mock_db:
            mock_db.init_database = AsyncMock()
            mock_db.get_all_exposes = AsyncMock(return_value=[
                {"name": "no-bootstrap", "enabled": True, "bootstrap": ""},
                {"name": "empty-bootstrap", "enabled": True, "bootstrap": "   "}
            ])

            progress = BootProgress()
            messages = []
            async for msg in _step_deploy(progress):
                messages.append(msg)

            warnings = [m for m in messages if m.msg_type == MessageType.WARNING]
            assert any("No enabled exposes" in m.message for m in warnings)

    @pytest.mark.asyncio
    async def test_prepares_valid_exposes(self):
        """Should prepare deployment config for valid exposes."""
        with patch("kodosumi.service.expose.boot.db") as mock_db, \
             patch("kodosumi.service.expose.boot.run_serve_deploy") as mock_deploy, \
             patch("kodosumi.service.expose.boot.load_serve_config") as mock_config:

            mock_db.init_database = AsyncMock()
            mock_db.get_all_exposes = AsyncMock(return_value=[
                {"name": "app-1", "enabled": True, "bootstrap": "import_path: mod1:app"},
                {"name": "app-2", "enabled": True, "bootstrap": "import_path: mod2:app"}
            ])
            mock_config.return_value = {"applications": []}
            mock_deploy.return_value = (0, "success", "")

            progress = BootProgress()
            messages = []
            async for msg in _step_deploy(progress):
                messages.append(msg)

            # Should have activity messages for each app
            activities = [m for m in messages if m.msg_type == MessageType.ACTIVITY and m.target in ["app-1", "app-2"]]
            assert len(activities) == 2

    @pytest.mark.asyncio
    async def test_returns_deployed_names_in_data(self):
        """Should return deployed app names in STEP_END message data."""
        with patch("kodosumi.service.expose.boot.db") as mock_db, \
             patch("kodosumi.service.expose.boot.run_serve_deploy") as mock_deploy, \
             patch("kodosumi.service.expose.boot.load_serve_config") as mock_config:

            mock_db.init_database = AsyncMock()
            mock_db.get_all_exposes = AsyncMock(return_value=[
                {"name": "my-app", "enabled": True, "bootstrap": "import_path: x:y"}
            ])
            mock_config.return_value = {"applications": []}
            mock_deploy.return_value = (0, "success", "")

            progress = BootProgress()
            messages = []
            async for msg in _step_deploy(progress):
                messages.append(msg)

            step_end = [m for m in messages if m.msg_type == MessageType.STEP_END]
            assert len(step_end) == 1
            assert step_end[0].data is not None
            assert "deployed_names" in step_end[0].data
            assert "my-app" in step_end[0].data["deployed_names"]

    @pytest.mark.asyncio
    async def test_yields_error_on_deploy_failure(self):
        """Should yield ERROR message when serve deploy fails."""
        with patch("kodosumi.service.expose.boot.db") as mock_db, \
             patch("kodosumi.service.expose.boot.run_serve_deploy") as mock_deploy, \
             patch("kodosumi.service.expose.boot.load_serve_config") as mock_config:

            mock_db.init_database = AsyncMock()
            mock_db.get_all_exposes = AsyncMock(return_value=[
                {"name": "app", "enabled": True, "bootstrap": "import_path: x:y"}
            ])
            mock_config.return_value = {"applications": []}
            mock_deploy.return_value = (1, "", "Deployment failed: invalid config")

            progress = BootProgress()
            messages = []
            async for msg in _step_deploy(progress):
                messages.append(msg)

            errors = [m for m in messages if m.msg_type == MessageType.ERROR]
            assert len(errors) == 1
            assert "serve deploy failed" in errors[0].message

    @pytest.mark.asyncio
    async def test_warns_on_invalid_bootstrap(self):
        """Should warn but continue when bootstrap is invalid."""
        with patch("kodosumi.service.expose.boot.db") as mock_db, \
             patch("kodosumi.service.expose.boot.run_serve_deploy") as mock_deploy, \
             patch("kodosumi.service.expose.boot.load_serve_config") as mock_config:

            mock_db.init_database = AsyncMock()
            mock_db.get_all_exposes = AsyncMock(return_value=[
                {"name": "invalid-app", "enabled": True, "bootstrap": "not_import_path: x"},
                {"name": "valid-app", "enabled": True, "bootstrap": "import_path: x:y"}
            ])
            mock_config.return_value = {"applications": []}
            mock_deploy.return_value = (0, "success", "")

            progress = BootProgress()
            messages = []
            async for msg in _step_deploy(progress):
                messages.append(msg)

            # Should have warning for invalid app
            warnings = [m for m in messages if m.msg_type == MessageType.WARNING and m.target == "invalid-app"]
            assert len(warnings) == 1
            assert "missing 'import_path'" in warnings[0].message

            # Should still deploy valid app
            step_end = [m for m in messages if m.msg_type == MessageType.STEP_END]
            assert len(step_end) == 1
            assert "valid-app" in step_end[0].data["deployed_names"]


class TestStepDeployIntegration:
    """Integration tests that test the full deploy flow."""

    @pytest.mark.asyncio
    async def test_full_deploy_flow_mocked(self):
        """Test the full deploy flow with all components mocked."""
        with patch("kodosumi.service.expose.boot.db") as mock_db, \
             patch("kodosumi.service.expose.boot.run_serve_deploy") as mock_deploy, \
             patch("kodosumi.service.expose.boot.load_serve_config") as mock_config:

            # Setup mocks
            mock_db.init_database = AsyncMock()
            mock_db.get_all_exposes = AsyncMock(return_value=[
                {
                    "name": "test-agent",
                    "enabled": True,
                    "bootstrap": "import_path: tests.fixtures.agent:app\nruntime_env:\n  pip: [requests]"
                }
            ])
            mock_config.return_value = {
                "proxy_location": "EveryNode",
                "http_options": {"host": "0.0.0.0", "port": 8005},
                "applications": []
            }
            mock_deploy.return_value = (0, "Deployed 1 application", "")

            # Run deploy
            progress = BootProgress()
            messages = []
            async for msg in _step_deploy(progress):
                messages.append(msg)

            # Verify message sequence
            message_types = [m.msg_type for m in messages]
            assert MessageType.STEP_START in message_types
            assert MessageType.STEP_END in message_types
            assert MessageType.ERROR not in message_types

            # Verify progress updated
            assert progress.step_name == "Deploy"
            assert progress.activities_done > 0
