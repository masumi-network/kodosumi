"""
Tests for audit logging functionality.
"""

import pytest
import tempfile
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

from tests.test_role import auth_client

from kodosumi.log import setup_audit_logger, get_audit_logger
from kodosumi.config import Settings


class MockSettings:
    """Mock settings for testing audit logger."""
    def __init__(self, log_file, max_bytes=1024*1024, backup_count=3):
        self.AUDIT_LOG_FILE = log_file
        self.AUDIT_LOG_MAX_BYTES = max_bytes
        self.AUDIT_LOG_BACKUP_COUNT = backup_count


# =============================================================================
# Audit Logger Setup Tests
# =============================================================================

def test_setup_audit_logger_creates_file(tmp_path):
    """Test that audit logger creates the log file."""
    log_file = str(tmp_path / "audit.log")
    settings = MockSettings(log_file)

    logger = setup_audit_logger(settings)

    # Write a log entry
    logger.info("Test audit entry")

    # Force flush
    for handler in logger.handlers:
        handler.flush()

    assert Path(log_file).exists()


def test_setup_audit_logger_uses_rotating_handler(tmp_path):
    """Test that audit logger uses RotatingFileHandler."""
    log_file = str(tmp_path / "audit.log")
    settings = MockSettings(log_file)

    logger = setup_audit_logger(settings)

    # Check that the handler is a RotatingFileHandler
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], RotatingFileHandler)


def test_setup_audit_logger_respects_settings(tmp_path):
    """Test that audit logger respects max_bytes and backup_count settings."""
    log_file = str(tmp_path / "audit.log")
    max_bytes = 5 * 1024 * 1024
    backup_count = 7
    settings = MockSettings(log_file, max_bytes, backup_count)

    logger = setup_audit_logger(settings)

    handler = logger.handlers[0]
    assert handler.maxBytes == max_bytes
    assert handler.backupCount == backup_count


def test_setup_audit_logger_creates_parent_dirs(tmp_path):
    """Test that audit logger creates parent directories if needed."""
    log_file = str(tmp_path / "nested" / "deep" / "audit.log")
    settings = MockSettings(log_file)

    logger = setup_audit_logger(settings)
    logger.info("Test entry")

    for handler in logger.handlers:
        handler.flush()

    assert Path(log_file).exists()


def test_get_audit_logger():
    """Test that get_audit_logger returns the correct logger."""
    logger = get_audit_logger()
    assert logger.name == "kodo.audit"


def test_audit_logger_levels(tmp_path):
    """Test that audit logger captures all log levels."""
    log_file = str(tmp_path / "audit.log")
    settings = MockSettings(log_file)

    logger = setup_audit_logger(settings)

    logger.debug("DEBUG message")
    logger.info("INFO message")
    logger.warning("WARNING message")
    logger.error("ERROR message")

    for handler in logger.handlers:
        handler.flush()

    content = Path(log_file).read_text()
    assert "DEBUG message" in content
    assert "INFO message" in content
    assert "WARNING message" in content
    assert "ERROR message" in content


def test_audit_logger_format(tmp_path):
    """Test that audit logger uses correct format."""
    log_file = str(tmp_path / "audit.log")
    settings = MockSettings(log_file)

    logger = setup_audit_logger(settings)
    logger.info("Test message")

    for handler in logger.handlers:
        handler.flush()

    content = Path(log_file).read_text()
    # Format: %(asctime)s %(levelname)s - %(message)s
    assert "INFO -" in content
    assert "Test message" in content


# =============================================================================
# Audit Stream Endpoint Tests
# =============================================================================

@pytest.mark.asyncio
async def test_audit_stream_empty(auth_client, tmp_path):
    """Test streaming audit log when file doesn't exist."""
    response = await auth_client.get("/audit/stream")
    assert response.status_code == 200

    data = response.json()
    assert "lines" in data
    assert "next_offset" in data
    assert "file_size" in data


@pytest.mark.asyncio
async def test_audit_stream_with_offset(auth_client, tmp_path):
    """Test streaming audit log with offset parameter."""
    response = await auth_client.get("/audit/stream?offset=0&limit=10")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data["lines"], list)
    assert isinstance(data["next_offset"], int)


@pytest.mark.asyncio
async def test_audit_stream_with_limit(auth_client, tmp_path):
    """Test streaming audit log with limit parameter."""
    response = await auth_client.get("/audit/stream?limit=5")
    assert response.status_code == 200

    data = response.json()
    # Should have at most 5 lines
    assert len(data["lines"]) <= 5


@pytest.mark.asyncio
async def test_audit_stream_filters_debug(tmp_path):
    """Test that audit stream filters out DEBUG level logs."""
    from litestar.testing import AsyncTestClient
    from kodosumi.service.app import create_app

    # Create audit log with mixed levels
    audit_log = tmp_path / "audit.log"
    audit_log.write_text(
        "2024-01-01 10:00:00 DEBUG - Debug message\n"
        "2024-01-01 10:00:01 INFO - Info message\n"
        "2024-01-01 10:00:02 WARNING - Warning message\n"
        "2024-01-01 10:00:03 ERROR - Error message\n"
        "2024-01-01 10:00:04 DEBUG - Another debug\n"
    )

    app = create_app(
        ADMIN_DATABASE=f"sqlite+aiosqlite:///{tmp_path}/admin.db",
        AUDIT_LOG_FILE=str(audit_log),
        EXEC_DIR=str(tmp_path / "exec"),
    )

    async with AsyncTestClient(app=app) as client:
        # Login
        response = await client.get(
            "/login", params={"name": "admin", "password": "admin"})
        assert response.status_code == 200

        # Get audit stream
        response = await client.get(
            "/audit/stream",
            cookies=response.cookies
        )
        assert response.status_code == 200

        data = response.json()
        lines = data["lines"]

        # Should not contain DEBUG lines
        debug_lines = [l for l in lines if "DEBUG" in l]
        assert len(debug_lines) == 0, f"Found DEBUG lines: {debug_lines}"

        # Should contain INFO, WARNING, ERROR
        assert any("INFO" in l for l in lines), "Should contain INFO lines"
        assert any("WARNING" in l for l in lines), "Should contain WARNING lines"
        assert any("ERROR" in l for l in lines), "Should contain ERROR lines"


@pytest.mark.asyncio
async def test_audit_stream_offset_pagination(tmp_path):
    """Test that offset-based pagination works correctly."""
    from litestar.testing import AsyncTestClient
    from kodosumi.service.app import create_app

    # Create audit log with multiple lines
    audit_log = tmp_path / "audit.log"
    lines = [f"2024-01-01 10:00:{i:02d} INFO - Line {i}\n" for i in range(20)]
    audit_log.write_text("".join(lines))

    app = create_app(
        ADMIN_DATABASE=f"sqlite+aiosqlite:///{tmp_path}/admin.db",
        AUDIT_LOG_FILE=str(audit_log),
        EXEC_DIR=str(tmp_path / "exec"),
    )

    async with AsyncTestClient(app=app) as client:
        # Login
        response = await client.get(
            "/login", params={"name": "admin", "password": "admin"})
        cookies = response.cookies

        # First request - get first 5 lines
        response = await client.get(
            "/audit/stream?offset=0&limit=5",
            cookies=cookies
        )
        data1 = response.json()
        assert len(data1["lines"]) == 5
        next_offset = data1["next_offset"]

        # Second request - use next_offset
        response = await client.get(
            f"/audit/stream?offset={next_offset}&limit=5",
            cookies=cookies
        )
        data2 = response.json()

        # Lines should be different
        assert data1["lines"] != data2["lines"]


# =============================================================================
# Audit Info Endpoint Tests
# =============================================================================

@pytest.mark.asyncio
async def test_audit_info_endpoint(auth_client):
    """Test audit info endpoint returns file information."""
    response = await auth_client.get("/audit/info")
    assert response.status_code == 200

    data = response.json()
    assert "exists" in data
    assert "path" in data
    assert "size" in data
    assert "max_bytes" in data
    assert "backup_count" in data


@pytest.mark.asyncio
async def test_audit_info_with_log_file(tmp_path):
    """Test audit info endpoint with existing log file."""
    from litestar.testing import AsyncTestClient
    from kodosumi.service.app import create_app

    # Create audit log
    audit_log = tmp_path / "audit.log"
    audit_log.write_text("2024-01-01 10:00:00 INFO - Test line\n")

    app = create_app(
        ADMIN_DATABASE=f"sqlite+aiosqlite:///{tmp_path}/admin.db",
        AUDIT_LOG_FILE=str(audit_log),
        EXEC_DIR=str(tmp_path / "exec"),
    )

    async with AsyncTestClient(app=app) as client:
        response = await client.get(
            "/login", params={"name": "admin", "password": "admin"})

        response = await client.get(
            "/audit/info",
            cookies=response.cookies
        )
        assert response.status_code == 200

        data = response.json()
        assert data["exists"] is True
        assert data["size"] > 0


# =============================================================================
# Log Rotation Integration Tests
# =============================================================================

def test_app_log_rotation_settings():
    """Test that app log rotation settings are present in Settings."""
    settings = Settings()
    assert hasattr(settings, "APP_LOG_MAX_BYTES")
    assert hasattr(settings, "APP_LOG_BACKUP_COUNT")
    assert settings.APP_LOG_MAX_BYTES > 0
    assert settings.APP_LOG_BACKUP_COUNT > 0


def test_spooler_log_rotation_settings():
    """Test that spooler log rotation settings are present in Settings."""
    settings = Settings()
    assert hasattr(settings, "SPOOLER_LOG_MAX_BYTES")
    assert hasattr(settings, "SPOOLER_LOG_BACKUP_COUNT")
    assert settings.SPOOLER_LOG_MAX_BYTES > 0
    assert settings.SPOOLER_LOG_BACKUP_COUNT > 0


def test_audit_log_rotation_settings():
    """Test that audit log rotation settings are present in Settings."""
    settings = Settings()
    assert hasattr(settings, "AUDIT_LOG_MAX_BYTES")
    assert hasattr(settings, "AUDIT_LOG_BACKUP_COUNT")
    assert settings.AUDIT_LOG_MAX_BYTES > 0
    assert settings.AUDIT_LOG_BACKUP_COUNT > 0
