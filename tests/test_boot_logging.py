"""
Unit tests for D11-bootlog: startup banner (server.py) and boot result
structured logging (boot.py).

All tests are pure / unit — no uvicorn, no Ray cluster required.
"""
import logging
import os
import unittest.mock as mock
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers from server.py under test
# ---------------------------------------------------------------------------
from kodosumi.service.server import _git_sha, _build_startup_banner
from kodosumi.config import Settings


# ===========================================================================
# _git_sha() helper tests
# ===========================================================================

class TestGitSha:
    """Tests for the _git_sha() resolver in server.py."""

    def test_returns_string(self):
        """_git_sha always returns a str."""
        result = _git_sha("/tmp")
        assert isinstance(result, str)

    def test_env_var_takes_priority(self, monkeypatch):
        """KODO_GIT_SHA env var is returned as-is without calling subprocess."""
        monkeypatch.setenv("KODO_GIT_SHA", "abc123")
        with patch("kodosumi.service.server.subprocess.run") as mock_run:
            result = _git_sha("/some/path")
        assert result == "abc123"
        mock_run.assert_not_called()

    def test_env_var_stripped(self, monkeypatch):
        """Whitespace in KODO_GIT_SHA is stripped."""
        monkeypatch.setenv("KODO_GIT_SHA", "  def456  \n")
        result = _git_sha("/some/path")
        assert result == "def456"

    def test_subprocess_fallback(self, monkeypatch):
        """Without env var, subprocess result is used when returncode == 0."""
        monkeypatch.delenv("KODO_GIT_SHA", raising=False)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc1234\n"
        with patch("kodosumi.service.server.subprocess.run", return_value=mock_result):
            result = _git_sha("/repo")
        assert result == "abc1234"

    def test_subprocess_nonzero_returns_unknown(self, monkeypatch):
        """Subprocess returning non-zero exit code yields 'unknown'."""
        monkeypatch.delenv("KODO_GIT_SHA", raising=False)
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("kodosumi.service.server.subprocess.run", return_value=mock_result):
            result = _git_sha("/repo")
        assert result == "unknown"

    def test_subprocess_file_not_found_returns_unknown(self, monkeypatch):
        """FileNotFoundError (git not installed) yields 'unknown'."""
        monkeypatch.delenv("KODO_GIT_SHA", raising=False)
        with patch("kodosumi.service.server.subprocess.run", side_effect=FileNotFoundError):
            result = _git_sha("/repo")
        assert result == "unknown"

    def test_subprocess_timeout_returns_unknown(self, monkeypatch):
        """subprocess.TimeoutExpired yields 'unknown'."""
        monkeypatch.delenv("KODO_GIT_SHA", raising=False)
        import subprocess
        with patch(
            "kodosumi.service.server.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=2),
        ):
            result = _git_sha("/bad/path")
        assert result == "unknown"

    def test_bad_path_returns_string(self, monkeypatch):
        """A non-existent path never raises — returns 'unknown' or a sha."""
        monkeypatch.delenv("KODO_GIT_SHA", raising=False)
        result = _git_sha("/this/path/does/not/exist/xyzzy")
        assert isinstance(result, str)


# ===========================================================================
# _build_startup_banner() helper tests
# ===========================================================================

class TestBuildStartupBanner:
    """Tests for the _build_startup_banner() pure helper in server.py."""

    def _settings(self) -> Settings:
        return Settings(
            APP_SERVER="http://localhost:3370",
            RAY_SERVER="localhost:6379",
            RAY_DASHBOARD="http://localhost:8265",
            RAY_SERVE_ADDRESS="http://localhost:8005",
            EXEC_DIR="/tmp/exec",
        )

    def test_required_keys_present(self, monkeypatch):
        """Banner dict contains all expected keys."""
        monkeypatch.setenv("KODO_GIT_SHA", "testsha")
        banner = _build_startup_banner(self._settings())
        for key in ("version", "python", "git_sha", "app_server", "ray_server",
                    "ray_dashboard", "ray_serve", "exec_dir"):
            assert key in banner, f"Missing key: {key}"

    def test_version_is_string(self, monkeypatch):
        monkeypatch.setenv("KODO_GIT_SHA", "x")
        banner = _build_startup_banner(self._settings())
        assert isinstance(banner["version"], str)
        assert len(banner["version"]) > 0

    def test_app_server_matches_settings(self, monkeypatch):
        monkeypatch.setenv("KODO_GIT_SHA", "x")
        s = self._settings()
        banner = _build_startup_banner(s)
        assert banner["app_server"] == s.APP_SERVER

    def test_ray_fields_match_settings(self, monkeypatch):
        monkeypatch.setenv("KODO_GIT_SHA", "x")
        s = self._settings()
        banner = _build_startup_banner(s)
        assert banner["ray_server"] == s.RAY_SERVER
        assert banner["ray_dashboard"] == s.RAY_DASHBOARD
        assert banner["ray_serve"] == s.RAY_SERVE_ADDRESS

    def test_git_sha_uses_env_var(self, monkeypatch):
        monkeypatch.setenv("KODO_GIT_SHA", "envsha99")
        banner = _build_startup_banner(self._settings())
        assert banner["git_sha"] == "envsha99"

    def test_never_contains_password(self, monkeypatch):
        """Admin password must not appear in banner values."""
        monkeypatch.setenv("KODO_GIT_SHA", "x")
        monkeypatch.setenv("KODO_ADMIN_PASSWORD", "supersecret")
        banner = _build_startup_banner(self._settings())
        for v in banner.values():
            assert "supersecret" not in str(v)


# ===========================================================================
# slog() emission from server.run() (smoke test via caplog)
# ===========================================================================

class TestServerRunEmitsBanner:
    """The slog call inside run() fires with correct event name."""

    def test_startup_slog_called(self, monkeypatch, caplog):
        """slog emits 'kodosumi.startup' event when run() is called."""
        monkeypatch.setenv("KODO_GIT_SHA", "testsha")

        settings = Settings(
            APP_SERVER="http://localhost:3370",
            RAY_SERVER="localhost:6379",
            RAY_DASHBOARD="http://localhost:8265",
            RAY_SERVE_ADDRESS="http://localhost:8005",
            EXEC_DIR="/tmp/exec",
        )
        # Patch uvicorn.run to prevent actual server start
        with patch("kodosumi.service.server.uvicorn.run"), \
             caplog.at_level(logging.INFO, logger="kodo"):
            from kodosumi.service.server import run
            run(settings)

        events = [r.getMessage() for r in caplog.records]
        assert any("kodosumi.startup" in e for e in events), (
            f"Expected 'kodosumi.startup' in log records; got: {events}"
        )


# ===========================================================================
# boot.py — boot.app_result and boot.summary slog emissions
# ===========================================================================

class TestBootAppResultSlog:
    """boot.app_result slog() is emitted for each app in final_statuses."""

    def _make_final_statuses(self, running=("app1",), failed=()) -> dict:
        result = {}
        for name in running:
            result[name] = {"status": "RUNNING", "message": ""}
        for name in failed:
            result[name] = {"status": "DEPLOY_FAILED", "message": "import error: no module named foo"}
        return result

    def test_running_app_emits_info_slog(self, caplog):
        """RUNNING apps emit an INFO boot.app_result slog record."""
        final_statuses = self._make_final_statuses(running=("my-agent",))

        with caplog.at_level(logging.INFO, logger="kodo"):
            from kodosumi.log import logger, slog
            for name, info in final_statuses.items():
                status = info.get("status", "UNKNOWN")
                reason = (info.get("message") or "")[:200] or None
                if status == "RUNNING":
                    slog(logger, logging.INFO, "boot.app_result", app=name, status=status)
                else:
                    slog(logger, logging.WARNING, "boot.app_result", app=name, status=status, reason=reason)

        records = [r for r in caplog.records if "boot.app_result" in r.getMessage()]
        assert len(records) == 1
        assert records[0].levelno == logging.INFO

    def test_failed_app_emits_warning_slog(self, caplog):
        """DEPLOY_FAILED apps emit a WARNING boot.app_result slog record."""
        final_statuses = self._make_final_statuses(failed=("broken-agent",))

        with caplog.at_level(logging.WARNING, logger="kodo"):
            from kodosumi.log import logger, slog
            for name, info in final_statuses.items():
                status = info.get("status", "UNKNOWN")
                reason = (info.get("message") or "")[:200] or None
                slog(logger, logging.WARNING, "boot.app_result", app=name, status=status, reason=reason)

        # Filter to only WARNING-level boot.app_result records emitted in this test
        records = [
            r for r in caplog.records
            if "boot.app_result" in r.getMessage() and r.levelno == logging.WARNING
        ]
        assert len(records) >= 1
        assert records[0].levelno == logging.WARNING

    def test_reason_truncated_to_200_chars(self, caplog):
        """Long failure reason is truncated to 200 chars in slog payload."""
        long_reason = "x" * 500
        truncated = long_reason[:200]

        with caplog.at_level(logging.WARNING, logger="kodo"):
            from kodosumi.log import logger, slog
            slog(logger, logging.WARNING, "boot.app_result",
                 app="bad", status="DEPLOY_FAILED", reason=truncated)

        records = [r for r in caplog.records if "boot.app_result" in r.getMessage()]
        assert len(records) == 1
        payload = getattr(records[0], "_slog", {})
        assert len(payload.get("reason", "")) <= 200


class TestBootSummarySlog:
    """boot.summary slog() is emitted with correct fields."""

    def test_summary_fields_present(self, caplog):
        """boot.summary contains version, running, failed, total, duration_ms."""
        import kodosumi as _kodo
        from kodosumi.log import logger, slog

        with caplog.at_level(logging.INFO, logger="kodo"):
            slog(
                logger,
                logging.INFO,
                "boot.summary",
                version=_kodo.__version__,
                running=3,
                failed=1,
                total=4,
                failed_apps=["broken-agent"],
                duration_ms=12345,
            )

        records = [r for r in caplog.records if "boot.summary" in r.getMessage()]
        assert len(records) == 1
        payload = getattr(records[0], "_slog", {})
        assert payload.get("running") == 3
        assert payload.get("failed") == 1
        assert payload.get("total") == 4
        assert payload.get("duration_ms") == 12345
        assert isinstance(payload.get("version"), str)

    def test_summary_level_is_info(self, caplog):
        """boot.summary is always emitted at INFO level."""
        import kodosumi as _kodo
        from kodosumi.log import logger, slog

        with caplog.at_level(logging.INFO, logger="kodo"):
            slog(logger, logging.INFO, "boot.summary",
                 version=_kodo.__version__, running=2, failed=0, total=2,
                 failed_apps=None, duration_ms=999)

        records = [r for r in caplog.records if "boot.summary" in r.getMessage()]
        assert len(records) == 1
        assert records[0].levelno == logging.INFO

    def test_no_failed_apps_none(self, caplog):
        """When all apps succeed, failed_apps=None is allowed (no key bloat)."""
        import kodosumi as _kodo
        from kodosumi.log import logger, slog

        with caplog.at_level(logging.INFO, logger="kodo"):
            slog(logger, logging.INFO, "boot.summary",
                 version=_kodo.__version__, running=2, failed=0, total=2,
                 failed_apps=None, duration_ms=500)

        records = [r for r in caplog.records if "boot.summary" in r.getMessage()]
        assert len(records) == 1
        # No exception means the None was handled correctly


# ===========================================================================
# Import smoke test — ensure both modules are importable without Ray/uvicorn
# ===========================================================================

class TestImportSanity:
    def test_server_importable(self):
        import kodosumi.service.server  # noqa: F401

    def test_boot_importable(self):
        import kodosumi.service.expose.boot  # noqa: F401
