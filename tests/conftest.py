"""
Pytest fixtures for kodosumi tests.

Provides test isolation to prevent tests from polluting production databases.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolate_expose_database(tmp_path):
    """
    Automatically isolate expose database for all tests.

    This fixture patches the EXPOSE_DATABASE constant to use a temporary
    directory, preventing tests from writing to the production database.
    """
    test_db_path = str(tmp_path / "test_expose.db")

    with patch("kodosumi.service.expose.db.EXPOSE_DATABASE", test_db_path):
        yield test_db_path


@pytest.fixture
def temp_data_dir(tmp_path):
    """
    Provide a temporary data directory for tests.

    Use this for tests that need to write files to ./data/
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Also create subdirectories commonly used
    (data_dir / "execution").mkdir(exist_ok=True)
    (data_dir / "uploads").mkdir(exist_ok=True)

    return data_dir
