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
    _coerce_env_vars,
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


class TestCoerceEnvVars:
    """Tests for _coerce_env_vars() — YAML type coercion to strings."""

    def test_bools_become_lowercase_strings(self):
        env = _coerce_env_vars({"A": True, "B": False})
        assert env == {"A": "true", "B": "false"}

    def test_yaml_bool_aliases(self):
        """YAML parses yes/no/on/off as booleans too."""
        import yaml
        parsed = yaml.safe_load("yes: yes\nno: no\non: on\noff: off")
        env = _coerce_env_vars(parsed)
        for v in env.values():
            assert isinstance(v, str)
            assert v in ("true", "false")

    def test_ints_become_strings(self):
        env = _coerce_env_vars({"PORT": 8080, "HEX": 0x1A})
        assert env == {"PORT": "8080", "HEX": "26"}

    def test_floats_become_strings(self):
        env = _coerce_env_vars({"RATE": 1.5, "ZERO": 0.0})
        assert env == {"RATE": "1.5", "ZERO": "0.0"}

    def test_none_values_dropped(self):
        env = _coerce_env_vars({"KEEP": "val", "DROP": None})
        assert env == {"KEEP": "val"}
        assert "DROP" not in env

    def test_inf_nan_dropped(self):
        env = _coerce_env_vars({
            "INF": float("inf"),
            "NINF": float("-inf"),
            "NAN": float("nan"),
            "KEEP": "val",
        })
        assert env == {"KEEP": "val"}

    def test_dates_become_isoformat(self):
        import datetime
        env = _coerce_env_vars({"D": datetime.date(2026, 7, 15)})
        assert env == {"D": "2026-07-15"}

    def test_strings_unchanged(self):
        env = _coerce_env_vars({
            "KEY": "sk-abc123",
            "URL": "https://example.com",
            "EMPTY": "",
        })
        assert env == {
            "KEY": "sk-abc123",
            "URL": "https://example.com",
            "EMPTY": "",
        }

    def test_empty_dict(self):
        assert _coerce_env_vars({}) == {}

    def test_all_values_are_strings(self):
        """Full YAML round-trip: no non-string value survives."""
        import yaml
        raw = yaml.safe_load("""
          BOOL: true
          INT: 42
          FLOAT: 3.14
          NULL: null
          STR: hello
          DATE: 2026-01-01
          QUOTED: "true"
        """)
        env = _coerce_env_vars(raw)
        for k, v in env.items():
            assert isinstance(v, str), f"{k}={v!r} is {type(v).__name__}, not str"
        assert "NULL" not in env

    def test_coercion_logs_warning(self, caplog):
        """Every coerced value must emit a WARNING for visibility."""
        import logging
        with caplog.at_level(logging.WARNING):
            _coerce_env_vars({"FLAG": True, "PORT": 8080}, expose_name="test-app")
        coerced = [r for r in caplog.records
                   if r.msg == "boot.env_vars.coerced"]
        assert len(coerced) == 2
        keys = {r._slog["key"] for r in coerced}
        assert keys == {"FLAG", "PORT"}

    def test_dropped_null_logs_warning(self, caplog):
        """Dropped None values must emit a WARNING."""
        import logging
        with caplog.at_level(logging.WARNING):
            _coerce_env_vars({"GONE": None}, expose_name="test-app")
        dropped = [r for r in caplog.records
                   if r.msg == "boot.env_vars.dropped"]
        assert len(dropped) == 1
        assert dropped[0]._slog["key"] == "GONE"

    def test_dropped_inf_logs_warning(self, caplog):
        """Dropped inf/nan values must emit a WARNING."""
        import logging
        with caplog.at_level(logging.WARNING):
            _coerce_env_vars({"BAD": float("inf")}, expose_name="test-app")
        dropped = [r for r in caplog.records
                   if r.msg == "boot.env_vars.dropped"]
        assert len(dropped) == 1


class TestParseBootstrapEnvVars:
    """Integration: parse_bootstrap coerces env_vars end-to-end."""

    def test_coerces_env_vars_in_bootstrap(self):
        bootstrap = """
import_path: mymodule:app
runtime_env:
  env_vars:
    OTEL_SDK_DISABLED: true
    PORT: 8080
    VERSION: 1.5
    API_KEY: sk-abc
"""
        result = parse_bootstrap(bootstrap, "test")
        env = result["runtime_env"]["env_vars"]
        assert env["OTEL_SDK_DISABLED"] == "true"
        assert env["PORT"] == "8080"
        assert env["VERSION"] == "1.5"
        assert env["API_KEY"] == "sk-abc"
        for v in env.values():
            assert isinstance(v, str)

    def test_no_env_vars_still_works(self):
        bootstrap = """
import_path: mymodule:app
runtime_env:
  pip: [openai]
"""
        result = parse_bootstrap(bootstrap, "test")
        assert "env_vars" not in result["runtime_env"]

    def test_no_runtime_env_still_works(self):
        result = parse_bootstrap("import_path: mymodule:app", "test")
        assert "runtime_env" not in result

    def test_ray_accepts_coerced_env_vars(self):
        """Verify Ray RuntimeEnv accepts the coerced values."""
        try:
            from ray.runtime_env import RuntimeEnv
        except ImportError:
            pytest.skip("ray not installed")

        bootstrap = """
import_path: mymodule:app
runtime_env:
  env_vars:
    ENABLED: true
    COUNT: 42
    RATE: 0.5
"""
        result = parse_bootstrap(bootstrap, "test")
        env_vars = result["runtime_env"]["env_vars"]
        rt = RuntimeEnv(env_vars=env_vars)
        assert rt["env_vars"] == env_vars


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
